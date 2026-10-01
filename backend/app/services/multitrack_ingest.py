"""Recognising material where every speaker already has their own track.

Two shapes arrive. Zoom's "record a separate audio file for each participant"
writes a folder per meeting with an ``Audio Record`` subfolder holding one file
per participant, all starting at the same instant. A multi-channel recording —
an interview with a mic each — puts the same thing in one file's channels.

Either way the job here is only to work out what the tracks are and what the
recording page should play; the tracks themselves are read straight from where
they lie, and nothing is mixed together (see ml_worker/multitrack.py).
"""

import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {'mp3', 'm4a', 'wav', 'ogg', 'flac'}
VIDEO_EXTENSIONS = {'mp4', 'mkv', 'webm'}
TRACK_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# The folder Zoom puts the per-participant files in. Matched case-insensitively
# because the casing has moved around between versions.
ZOOM_TRACK_DIRS = ('audio record', 'audio_record')

_SEPARATORS = ' _-.'
# Meeting ids and epoch stamps Zoom threads through filenames. Six digits is
# long enough that it will not eat a number someone actually put in their name.
_ID_RUN = re.compile(r'\d{6,}')
_DATETIME = re.compile(
    r'\d{4}[-.]\d{2}[-.]\d{2}(?:[ _-]+\d{2}[.:-]\d{2}[.:-]\d{2})?')
# Checked longest-first so "audio_only" is not shortened to "audio".
_RECORDER_TAGS = ('audio_only', 'audioonly', 'audio')


def _recorder_prefix_len(name, allow_capital=False):
    """How much of ``name`` is the recorder's own word, or 0 if none of it.

    Only a prefix when something separates it from what follows — otherwise a
    participant called "Audiophile" loses the front of their name.

    ``allow_capital`` also counts a capital letter as that separation, which
    is how Zoom's ``audioPavelK`` gives up its name. Filenames ask for it;
    folder names do not, so a meeting called "AudioSync" keeps its title.
    """
    lowered = name.lower()
    for tag in _RECORDER_TAGS:
        if not lowered.startswith(tag):
            continue
        rest = name[len(tag):]
        if rest == '' or rest[0] in _SEPARATORS or rest[0].isdigit():
            return len(tag)
        if allow_capital and rest[0].isupper():
            return len(tag)
    return 0


def speaker_name_from_filename(filename):
    """Best guess at the participant name Zoom encoded in a filename.

    Zoom has spelled these several ways across versions — ``audio1234_Name``,
    ``Name_1234``, ``audio_only_Name`` — so rather than matching one layout this
    strips the parts that are demonstrably not a name (the recorder's own
    prefix, meeting ids, timestamps) and keeps what is left.

    Returns '' when nothing recognisable survives, which lets the caller fall
    back to a positional label instead of showing the user "audio1234567890".
    """
    stem = os.path.splitext(os.path.basename(filename or ''))[0]
    work = stem

    cut = _recorder_prefix_len(work, allow_capital=True)
    if cut:
        # Digits straight after the recorder's own prefix are its id, at
        # whatever length it happens to use — nobody's name starts there.
        work = re.sub(r'^\d+', '', work[cut:].lstrip(_SEPARATORS))

    work = _DATETIME.sub(' ', work)
    work = _ID_RUN.sub(' ', work)
    work = work.strip(_SEPARATORS)
    if '_' in work and ' ' not in work:
        work = work.replace('_', ' ')
    work = re.sub(r'\s+', ' ', work).strip(_SEPARATORS).strip()

    if not work or work.isdigit():
        return ''
    return work


def meeting_name_from_folder(folder):
    """What to call a recording taken from a Zoom folder.

    Normally the meeting folder's own name — but people point the picker at the
    ``Audio Record`` subfolder as readily as at the meeting above it, and a
    recording listed as "Audio Record" says nothing about which meeting it was.
    Any folder named after the recorder rather than the meeting is stepped over
    in favour of the one above it, so the name never opens with "audio".
    """
    path = (folder or '').rstrip('\\/')
    seen = set()
    while path and path not in seen:
        seen.add(path)
        name = os.path.basename(path)
        if name and not _recorder_prefix_len(name):
            return name
        path = os.path.dirname(path)
    return 'Zoom meeting'


def _ext(path):
    return path.rsplit('.', 1)[-1].lower() if '.' in path else ''


def _audio_files(folder):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if _ext(n) in AUDIO_EXTENSIONS
            and os.path.isfile(os.path.join(folder, n))]


def _find_track_dir(folder):
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for name in entries:
        full = os.path.join(folder, name)
        if os.path.isdir(full) and name.lower() in ZOOM_TRACK_DIRS:
            return full
    return None


def _largest(paths):
    best = None
    best_size = -1
    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > best_size:
            best, best_size = path, size
    return best


def detect_zoom_folder(folder):
    """Describe a Zoom meeting folder, or None if it is not one.

    Returns ``{'tracks': [{'path', 'speaker_name'}], 'media': path|None}``.
    ``media`` is what the recording page should play — the meeting video when
    Zoom saved one, otherwise the mixed audio it also writes. None means neither
    exists and the caller has to build a mixdown to have anything to play.
    """
    if not folder or not os.path.isdir(folder):
        return None

    track_dir = _find_track_dir(folder)
    if not track_dir:
        return None

    paths = _audio_files(track_dir)
    if len(paths) < 2:
        # One participant is not multi-track; it is just a recording.
        log.info('Zoom folder %s has %d track(s) — not treating as multi-track',
                 folder, len(paths))
        return None

    tracks = [{'path': p, 'speaker_name': speaker_name_from_filename(p)}
              for p in paths]

    try:
        roots = [os.path.join(folder, n) for n in sorted(os.listdir(folder))]
    except OSError:
        roots = []
    files = [p for p in roots if os.path.isfile(p)]
    media = (_largest([p for p in files if _ext(p) in VIDEO_EXTENSIONS])
             or _largest([p for p in files if _ext(p) in AUDIO_EXTENSIONS]))

    return {'tracks': tracks, 'media': media}


def describe_multichannel(path):
    """Describe one file whose channels are the speakers, or None.

    Deliberately not applied to every stereo file that turns up: ordinary stereo
    is ordinary stereo, and reading its two channels as two people would invent
    a second speaker out of the room. The caller decides, from an explicit
    choice by the user.
    """
    from ml_worker.audio import count_channels

    if not path or not os.path.isfile(path):
        return None
    channels = count_channels(path)
    if channels < 2:
        return None
    return {
        'tracks': [{'path': path, 'speaker_name': f'Channel {i + 1}',
                    'channel': i} for i in range(channels)],
        'media': path,
    }


def build_mixdown(track_paths, out_path):
    """Mix the tracks down to one playable file. Returns the path, or None.

    Used only when the folder holds nothing playable. This file exists for the
    player alone — transcription reads the individual tracks, never this.
    """
    if not track_paths:
        return None

    cmd = ['ffmpeg', '-y', '-v', 'error']
    for path in track_paths:
        cmd += ['-i', path]
    cmd += [
        '-filter_complex',
        # normalize=0: amix otherwise divides by the input count, so a
        # four-person meeting plays back at a quarter of its level.
        f'amix=inputs={len(track_paths)}:duration=longest:normalize=0',
        '-ac', '1', out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    except Exception as exc:
        log.warning('Could not build mixdown for %d tracks: %s',
                    len(track_paths), exc)
        return None
    return out_path
