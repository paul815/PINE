"""Per-speaker tracks: find the speech, cut out the rest, put the clock back.

When every speaker has their own track, who spoke when is not something to infer
— it is already in the recording. What is left is the mechanical part: each
track is transcribed on its own, so nothing has to be diarized, but a track is
not speech from end to end. In an interview the participant's track is dense
while the moderator's is mostly listening, and running the model over those
pauses would cost a full pass per track and invite Whisper to invent text over
digital silence.

So each track is *compacted* before transcription: speech regions are found,
concatenated into a short file, and the timestamps that come back are mapped
onto the original timeline. Summed over the tracks that is roughly one pass over
the meeting — what a single mixed file costs today — with none of the guessing.

The three steps are kept separate so each can be tested on its own:

    frame_db → speech_mask → mask_to_regions      where the speech is
    compact                                        the short file + a splice map
    remap                                          timestamps back on the clock

``detect_speech`` composes the first three for the common single-track case.
"""

import logging
from bisect import bisect_right

from .constants import (
    VAD_CLOSE_FRACTION,
    VAD_COMPACT_GAP_SEC,
    VAD_DOMINANCE_DB,
    VAD_FRAME_SEC,
    VAD_MERGE_GAP_SEC,
    VAD_MIN_DYNAMIC_DB,
    VAD_MIN_MARGIN_DB,
    VAD_MIN_SPEECH_SEC,
    VAD_OPEN_FRACTION,
    VAD_PAD_SEC,
    VAD_SILENCE_FLOOR_DB,
)

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Timestamps are compared against splice boundaries that were themselves derived
# from sample counts; a hair of slack keeps a word that ends exactly on a
# boundary inside its region instead of in the gap after it.
_EPS = 1e-6


# ── where the speech is ──

def frame_db(samples, sample_rate=SAMPLE_RATE, frame_sec=VAD_FRAME_SEC):
    """Per-frame loudness in dBFS, floored at ``VAD_SILENCE_FLOOR_DB``.

    The floor matters more than it looks. A Zoom per-participant track is
    digitally silent when that person is not talking, which is -inf dB, and a
    threshold derived from a range that starts at -inf sits so low that any
    stray sample reads as speech. Clamping gives the quiet end a defined value
    to measure from.
    """
    import numpy as np

    size = max(int(round(sample_rate * frame_sec)), 1)
    count = len(samples) // size
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    frames = np.asarray(samples[:count * size], dtype=np.float32).reshape(count, size)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    return np.maximum(db, VAD_SILENCE_FLOOR_DB).astype(np.float32)


def speech_thresholds(db):
    """Open/close thresholds for one track, or None when it holds no speech.

    Derived per track rather than fixed: tracks arrive at wildly different
    levels (a headset mic and a laptop mic in the same meeting), so an absolute
    threshold that suits one clips the other. The quiet end of *this* track is
    the reference, and speech has to stand a margin above it.
    """
    import numpy as np

    if db.size == 0:
        return None
    floor = float(np.percentile(db, 20))

    # The loud end is the *median of the frames that stand above the floor*, not
    # a high percentile of everything. A percentile assumes speech occupies a
    # known share of the track, and on a per-speaker track it does not: an
    # interviewer can hold the floor for three minutes of a two-hour meeting, at
    # which point even the 95th percentile is still silence and the track reads
    # as empty. Measuring only the frames that are already above the floor makes
    # the estimate independent of how much of the track is speech.
    loud = db[db > floor + VAD_MIN_MARGIN_DB]
    if loud.size == 0:
        # Nothing stands above this track's own quiet level, so there is no
        # loud/quiet structure to work with: the track is either silent
        # throughout or unbroken sound throughout. Absolute level is the only
        # thing left to ask. (Steady background noise with nobody talking reads
        # as the latter — Zoom's per-participant tracks are digitally silent, so
        # this only bites on a continuously noisy room mic.)
        if floor > VAD_SILENCE_FLOOR_DB + VAD_MIN_DYNAMIC_DB:
            return (floor - 1.0, floor - 1.0)
        return None

    peak = float(np.percentile(loud, 50))
    span = peak - floor
    if span < VAD_MIN_DYNAMIC_DB:
        return None

    open_db = floor + max(span * VAD_OPEN_FRACTION, VAD_MIN_MARGIN_DB)
    close_db = floor + max(span * VAD_CLOSE_FRACTION, VAD_MIN_MARGIN_DB * 0.5)
    open_db = min(open_db, peak - 1.0)
    close_db = min(close_db, open_db)
    return (open_db, close_db)


def speech_mask(db, thresholds):
    """Hysteresis gate over the frame loudness.

    Two thresholds, not one: a single threshold chatters on and off through the
    dips inside a word. Speech has to cross the higher one to start and fall
    below the lower one to stop.
    """
    import numpy as np

    mask = np.zeros(db.size, dtype=bool)
    if thresholds is None or db.size == 0:
        return mask
    open_db, close_db = thresholds

    # A frame-at-a-time loop: ~360k frames for a two-hour track, which is
    # milliseconds next to the transcription it feeds.
    active = False
    for i in range(db.size):
        level = db[i]
        if active:
            if level < close_db:
                active = False
        elif level >= open_db:
            active = True
        mask[i] = active
    return mask


def mask_to_regions(mask, duration=None, frame_sec=VAD_FRAME_SEC,
                    min_speech=VAD_MIN_SPEECH_SEC,
                    merge_gap=VAD_MERGE_GAP_SEC, pad=VAD_PAD_SEC):
    """Turn a per-frame mask into padded ``[(start, end)]`` regions in seconds.

    Order is deliberate: gaps are closed *before* short regions are dropped. An
    interview is full of back-channel — "угу", "да", "понятно" — landing at a
    few tenths of a second, and dropping first would delete exactly those. What
    survives the merge and is still shorter than ``min_speech`` is an isolated
    click, not a word.
    """
    regions = []
    start = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            regions.append((start * frame_sec, i * frame_sec))
            start = None
    if start is not None:
        regions.append((start * frame_sec, len(mask) * frame_sec))

    regions = _merge_close(regions, merge_gap)
    regions = [(s, e) for s, e in regions if e - s >= min_speech]

    limit = duration if duration is not None else (
        len(mask) * frame_sec if len(mask) else 0.0)
    padded = [(max(s - pad, 0.0), min(e + pad, limit)) for s, e in regions]
    # Padding can push neighbours into each other; close those too.
    return _merge_close(padded, 0.0)


def _merge_close(regions, gap):
    """Merge regions separated by ``gap`` seconds or less."""
    merged = []
    for start, end in regions:
        if merged and start - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def detect_speech(samples, sample_rate=SAMPLE_RATE, **kwargs):
    """Speech regions of one track, as ``[(start, end)]`` in seconds."""
    db = frame_db(samples, sample_rate=sample_rate)
    mask = speech_mask(db, speech_thresholds(db))
    return mask_to_regions(mask, duration=len(samples) / sample_rate, **kwargs)


def resolve_bleed(db_list, mask_list, dominance_db=VAD_DOMINANCE_DB):
    """Give a frame to the track that clearly owns it; leave real overlap alone.

    Only meaningful when the tracks came from one multi-channel recording, where
    every mic hears every speaker and the quiet copy of a voice can pass that
    track's own gate. Zoom's per-participant files carry one stream each and
    have nothing to resolve, so this runs as a no-op on them.

    A frame belongs to one track when it is ``dominance_db`` above the runner-up.
    When no track dominates, everyone who passed their own gate keeps the frame:
    two people talking at once is the case per-track transcription exists for,
    and forcing a winner would throw away one of them.
    """
    import numpy as np

    if len(mask_list) < 2:
        return list(mask_list)

    width = max(m.size for m in mask_list)
    levels = np.full((len(db_list), width), VAD_SILENCE_FLOOR_DB, dtype=np.float32)
    gates = np.zeros((len(mask_list), width), dtype=bool)
    # strict: the two lists are one entry per track and are indexed in
    # lockstep below, so a length mismatch is a caller bug, not a short zip.
    for i, (db, mask) in enumerate(zip(db_list, mask_list, strict=True)):
        levels[i, :db.size] = db
        gates[i, :mask.size] = mask

    order = np.argsort(levels, axis=0)
    columns = np.arange(width)
    loudest = order[-1]
    top = levels[loudest, columns]
    second = levels[order[-2], columns]
    decided = (top - second) >= dominance_db

    out = []
    for i in range(len(mask_list)):
        keep = gates[i] & (~decided | (loudest == i))
        out.append(keep[:mask_list[i].size])
    return out


# ── the short file ──

def compact(samples, regions, sample_rate=SAMPLE_RATE, gap_sec=VAD_COMPACT_GAP_SEC):
    """Concatenate the speech regions, returning ``(samples, splice_map)``.

    A short silence is inserted between regions so the model does not read two
    turns half an hour apart as one sentence.

    The splice map is ``[(compact_start, compact_end, original_start)]`` and is
    what ``remap`` needs to undo this.
    """
    import numpy as np

    pieces = []
    splices = []
    gap = np.zeros(max(int(round(sample_rate * gap_sec)), 0), dtype=np.float32)
    # Position is tracked in samples, not seconds: accumulating float durations
    # would drift away from the array the map is supposed to describe.
    cursor = 0

    for start, end in regions:
        i0 = max(int(round(start * sample_rate)), 0)
        i1 = min(int(round(end * sample_rate)), len(samples))
        chunk = samples[i0:i1]
        if len(chunk) == 0:
            continue
        if pieces and gap.size:
            pieces.append(gap)
            cursor += gap.size
        c_start = cursor
        pieces.append(chunk)
        cursor += len(chunk)
        splices.append((c_start / sample_rate, cursor / sample_rate,
                        i0 / sample_rate))

    out = (np.concatenate(pieces) if pieces
           else np.zeros(0, dtype=np.float32)).astype(np.float32)
    return out, splices


# ── back on the original clock ──

def _locate(t, splices, starts):
    """Index of the splice containing ``t``, or None if it fell in a gap."""
    i = bisect_right(starts, t) - 1
    if i < 0:
        return None
    if t > splices[i][1] + _EPS:
        return None
    return i


def _to_original(t, splice):
    c_start, c_end, o_start = splice
    return o_start + (min(max(t, c_start), c_end) - c_start)


def remap(segments, splices):
    """Move segments from the compacted timeline back to the original one.

    Words are placed individually and a segment is split where its words cross a
    splice, so a sentence the model stitched across a cut becomes two segments in
    the right two places rather than one segment spanning the silence between.

    Anything that lands in an inserted gap is dropped: there was no audio there,
    so there was nothing to transcribe.
    """
    if not splices:
        return []

    starts = [s[0] for s in splices]
    out = []

    for seg in segments or []:
        words = seg.get('words') or []
        if words:
            out.extend(_remap_words(seg, words, splices, starts))
            continue

        # No word timestamps (the MLX path can return segments without them):
        # the midpoint decides where the segment belongs.
        seg_start = float(seg.get('start', 0) or 0)
        seg_end = float(seg.get('end', 0) or 0)
        idx = _locate((seg_start + seg_end) / 2.0, splices, starts)
        if idx is None:
            continue
        out.append({
            'start': _to_original(seg_start, splices[idx]),
            'end': _to_original(seg_end, splices[idx]),
            'text': seg.get('text', ''),
            'speaker': seg.get('speaker', ''),
        })

    out.sort(key=lambda s: s['start'])
    return out


def _remap_words(seg, words, splices, starts):
    """Split one segment into runs of words sharing a splice, and move each."""
    runs = []
    for word in words:
        w_start = float(word.get('start', 0) or 0)
        w_end = float(word.get('end', 0) or 0)
        # The midpoint, so a word padded a few ms past its region still lands in it.
        idx = _locate((w_start + w_end) / 2.0, splices, starts)
        if idx is None:
            continue
        moved = dict(word)
        moved['start'] = _to_original(w_start, splices[idx])
        moved['end'] = _to_original(w_end, splices[idx])
        if runs and runs[-1][0] == idx:
            runs[-1][1].append(moved)
        else:
            runs.append((idx, [moved]))

    out = []
    for _, run in runs:
        out.append({
            'start': run[0]['start'],
            'end': run[-1]['end'],
            'text': ' '.join(str(w.get('word', '') or '').strip()
                             for w in run).strip(),
            'speaker': seg.get('speaker', ''),
            'words': run,
        })
    return out
