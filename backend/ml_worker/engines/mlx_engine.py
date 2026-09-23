"""mlx-whisper engine for Apple Silicon (Metal-accelerated).

mlx-whisper produces native word timestamps, so this engine needs no
wav2vec2 alignment stage. Diarization stays external (shared pyannote stage).
"""

import gc
import logging
import os
import re
import tempfile
import threading
import time
from statistics import median

from ..audio import clear_mlx_cache, fmt_elapsed, load_audio_range, write_wav
from ..constants import (
    CHUNK_OVERLAP_SEC,
    CHUNK_SIZE_SEC,
    CHUNK_THRESHOLD_SEC,
    LANG_CONFIDENCE_MARGIN,
    LANG_CONFIDENCE_MIN,
    MLX_CYRILLIC_LANGUAGES,
    MLX_HOLE_MIN_SEC,
    MLX_HOLE_PAD_SEC,
    MLX_HOLE_WORD_CAP_SEC,
    MLX_LANG_PROBE_SEARCH_SEC,
    MLX_LOOP_MAX_DISTINCT,
    MLX_LOOP_WINDOW_WORDS,
    MLX_PROMPT_CHARS,
    MLX_PROMPT_REPEAT_LIMIT,
    MLX_PROMPT_WINDOW_SEC,
    MLX_SENTENCE_RATE_FRACTION,
    MLX_SENTENCE_RATE_WINDOWS,
    MLX_TEMPERATURES,
    MLX_VAD_MERGE_GAP_SEC,
    MLX_VAD_MIN_CUT_SEC,
    MLX_VAD_MIN_FLATNESS,
    MLX_VAD_MIN_KEEP_FRACTION,
    MLX_VAD_PAD_SEC,
    MLX_VAD_TONE_MIN_SEC,
    MLX_WINDOW_MIN_PROGRESS_SEC,
)
from .base import (
    EngineAdapter,
    EngineCapabilities,
    LanguageProbe,
    TranscribeContext,
    TranscribeOutput,
)

log = logging.getLogger(__name__)

# The band ``spectral_flatness`` reads. Bounded below the first formant and above
# by what survives a phone line, so the measure describes the part of the
# spectrum a voice and a tone actually differ in.
_FLATNESS_LO_HZ = 200.0
_FLATNESS_HI_HZ = 3800.0


def _is_dot_tail(raw):
    """The back half of "т.д." or "self.id": a full stop, then a letter or digit."""
    return raw[:1] == '.' and raw[1:2].isalnum()


def _merge_dot_tails(words, raws):
    """``words`` and ``raws`` with each dot tail folded into the word before it."""
    merged, merged_raws = [], []
    for w, raw in zip(words, raws, strict=True):
        if merged and _is_dot_tail(raw):
            head = merged[-1]
            merged_raws[-1] += raw
            head['word'] = merged_raws[-1]
            if w.get('end') is not None:
                head['end'] = w['end']
            if 'probability' in w and 'probability' in head:
                head['probability'] = min(head['probability'], w['probability'])
            continue
        merged.append(w)
        merged_raws.append(raw)
    return merged, merged_raws


def synchronize_segments_for_ui(segments):
    """Normalize mlx-whisper ``text`` / ``word`` fields so the recording UI can align words.

    ``recording.html`` finds a word inside its segment with ``indexOf``, so every
    word it is handed has to appear verbatim in the segment text. Trimming each
    word is what makes that hold; rebuilding the text is what keeps the two in
    step after the words have been moved around.

    The spacing comes from the model, not from the join. Whisper marks a word
    boundary with a leading space on the token, and that is also how it spells a
    hyphenated word: "как-то" arrives as ``[' как', '-то']``. Joining everything
    with a space printed "как -то" — 89 of them in one 38-minute interview.

    Words that arrive already trimmed carry no spacing left to read. When the
    text they came with already spells them, it was built from them on an earlier
    pass — spacing and all — so it stands; otherwise a plain space between them is
    the best guess left, which is what this always used to do.

    Whisper also opens a word on a punctuation token, so "т.д." arrives as
    ``[' т', '.д.']``. The text built here spells it right, but the saved
    transcript is rebuilt from the words once they are trimmed, and printed
    "т .д." — so a piece opening on a full stop and a letter goes back into the
    word it belongs to, here, where the spacing that says so can still be read.

    Run this last, after any stage that rewrites the word list: it is what leaves
    ``text`` agreeing with ``words``.
    """
    for seg in segments or []:
        words = seg.get('words')
        if not words:
            continue

        raws = [('' if w.get('word') is None else str(w.get('word', '')))
                for w in words]
        if any(_is_dot_tail(raw) for raw in raws[1:]):
            words, raws = _merge_dot_tails(words, raws)
            seg['words'] = words
        for w, raw in zip(words, raws, strict=True):
            w['word'] = raw.strip()
        tokens = [w['word'] for w in words if w['word']]
        if not tokens:
            continue

        if any(raw[:1].isspace() for raw in raws):
            parts = []
            for raw in raws:
                token = raw.strip()
                if not token:
                    continue
                if parts and raw[:1].isspace():
                    parts.append(' ')
                parts.append(token)
            seg['text'] = ''.join(parts)
        elif ''.join((seg.get('text') or '').split()) != ''.join(tokens):
            seg['text'] = ' '.join(tokens)


def spectral_flatness(samples, sample_rate=16000):
    """How tone-like ``samples`` are: ~0 for a sine, ~1 for noise.

    Measured over the loudest half of the frames only. Ringback is a tone with
    silence between the rings, and digital silence is perfectly flat — averaging
    it in would hide the very thing this is looking for.

    And measured only between ``_FLATNESS_LO_HZ`` and ``_FLATNESS_HI_HZ``, which
    is not a detail. A phone call decoded to 16 kHz is empty above ~3.4 kHz, and
    a geometric mean taken across the whole spectrum is dominated by those empty
    bins: measured on synthetic signals, band-limiting drops a *voice* from 3.8e-2
    to 7e-8, i.e. straight past any threshold that separates it from a tone.
    Inside the band the two stay three orders of magnitude apart.

    Returns 1.0 (i.e. "not a tone") when there is too little audio to judge.
    """
    import numpy as np

    size = 512
    count = len(samples) // size
    if count < 4:
        return 1.0

    frames = np.asarray(samples[:count * size], dtype=np.float32).reshape(count, size)
    energy = np.mean(frames * frames, axis=1)
    frames = frames[energy >= np.median(energy)]
    if frames.shape[0] == 0:
        return 1.0

    spectrum = np.abs(np.fft.rfft(frames * np.hanning(size), axis=1)) ** 2
    freqs = np.fft.rfftfreq(size, 1.0 / sample_rate)
    band = (freqs >= _FLATNESS_LO_HZ) & (freqs <= _FLATNESS_HI_HZ)
    if band.sum() < 8:
        return 1.0

    spectrum = np.maximum(spectrum[:, band], 1e-20)
    flatness = (np.exp(np.mean(np.log(spectrum), axis=1))
                / np.mean(spectrum, axis=1))
    return float(np.median(flatness))


def drop_tones(samples, regions, sample_rate):
    """Regions that hold a voice rather than a tone.

    The energy gate cannot make this distinction — ringback, hold music and a
    person talking are all simply loud, which is why the tone reached the model
    and came back written down as "Звук колокола." Short regions are kept
    unexamined; see ``MLX_VAD_TONE_MIN_SEC``.
    """
    kept = []
    for start, end in regions:
        if (end - start) < MLX_VAD_TONE_MIN_SEC:
            kept.append((start, end))
            continue
        chunk = samples[int(start * sample_rate):int(end * sample_rate)]
        flatness = spectral_flatness(chunk)
        if flatness < MLX_VAD_MIN_FLATNESS:
            log.info('VAD: %.0f-%.0fs is a tone, not speech (flatness %.2g) — dropped',
                     start, end, flatness)
            continue
        kept.append((start, end))
    return kept


def keep_long_cuts(regions, total, protected=(), min_cut=MLX_VAD_MIN_CUT_SEC):
    """The same regions, with every cut too short to be worth its seam closed.

    A cut is not free. Each one is a seam ``tracks.remap`` has to put words back
    across, and — because the gate runs before the windows are planned — a pause
    the model no longer hears. An 88-minute interview came back gated at 18 cuts
    of one to two seconds each: 29s saved out of 5293, and with them every pause
    long enough for a window boundary to land in. So the crumbs are closed and
    only dead air worth the name is cut.

    A gap holding something ``drop_tones`` removed is never closed: it was cut
    for what was in it, not for its length.
    """
    if not regions:
        return []

    def held(lo, hi):
        return any(start < hi and end > lo for start, end in protected)

    merged = [list(regions[0])]
    for start, end in regions[1:]:
        if (start - merged[-1][1]) < min_cut and not held(merged[-1][1], start):
            merged[-1][1] = end
        else:
            merged.append([start, end])

    # The head and the tail are cuts too, judged on the same length.
    if merged[0][0] < min_cut and not held(0.0, merged[0][0]):
        merged[0][0] = 0.0
    if (total - merged[-1][1]) < min_cut and not held(merged[-1][1], total):
        merged[-1][1] = total
    return [(start, end) for start, end in merged]


def gate_speech(samples):
    """Keep the speech of one mono take, as ``(samples, splices)``.

    mlx-whisper has no VAD, so whatever is in the file reaches the model, and
    Whisper does not skip what is not speech — it describes it. A phone interview
    opening on ~45s of ringback comes back as "Звук колокола.", which
    ``condition_on_previous_text`` then feeds to the next window as context. One
    38-minute recording lost its first 2.5 minutes that way, the consent question
    among them. whisperx never sees this because its own VAD runs first.

    What it is not for is shaving pauses: see ``keep_long_cuts``. The gate earns
    its keep on one long stretch of dead air, not on a hundred short ones.

    ``splices`` is ``None`` when the audio passes through untouched, which is the
    answer whenever the gate is not confident: a pause left in costs seconds of
    decoding, speech cut out costs an answer. Callers hand the returned samples to
    the model and, when ``splices`` is not None, put the segments back on the
    original clock with ``tracks.remap``.
    """
    from ..tracks import SAMPLE_RATE, compact, detect_speech

    total = len(samples) / SAMPLE_RATE
    if total <= 0:
        return samples, None

    try:
        found = detect_speech(samples,
                              merge_gap=MLX_VAD_MERGE_GAP_SEC,
                              pad=MLX_VAD_PAD_SEC)
        voiced = drop_tones(samples, found, SAMPLE_RATE)
    except Exception as exc:
        log.warning('VAD failed (%s) — transcribing the audio as it is', exc)
        return samples, None

    kept = sum(end - start for start, end in voiced)
    # No regions at all is the gate saying it found no loud/quiet structure to
    # measure, not that the take is silent. Under the keep floor it found
    # something, but too little to be believed over the recording itself.
    if not voiced or kept < total * MLX_VAD_MIN_KEEP_FRACTION:
        log.info('VAD kept %.0f%% of %.0fs — passing the audio through untouched',
                 (kept / total) * 100, total)
        return samples, None

    regions = keep_long_cuts(voiced, total,
                             protected=[r for r in found if r not in voiced])
    kept = sum(end - start for start, end in regions)
    if (total - kept) < MLX_VAD_MIN_CUT_SEC:
        log.info('VAD: nothing to cut from %.0fs but pauses shorter than %.0fs '
                 '— passing the audio through untouched', total, MLX_VAD_MIN_CUT_SEC)
        return samples, None

    gated, splices = compact(samples, regions)
    log.info('VAD: %.0fs of %.0fs is speech across %d region(s) — %.0fs dropped',
             kept, total, len(regions), total - kept)
    return gated, splices


_REPEAT_STRIP = re.compile(r'[^\w\s]', re.UNICODE)


def _repeat_key(text):
    """What makes two segments "the same line" for the loop detector.

    Case, ё and punctuation are dropped because a loop rarely repeats itself
    byte for byte — "Звук колокола." and "звук колокола" are one runaway, not two
    transcriptions.
    """
    text = str(text or '').lower().replace('ё', 'е')
    return ' '.join(_REPEAT_STRIP.sub(' ', text).split())


def longest_repeat_run(segments):
    """Most consecutive segments carrying the same line."""
    best = 0
    run = 0
    previous = None
    for seg in segments or []:
        key = _repeat_key(seg.get('text'))
        if not key:
            previous = None
            run = 0
            continue
        run = run + 1 if key == previous else 1
        previous = key
        best = max(best, run)
    return best


def narrowest_vocabulary(segments, width=MLX_LOOP_WINDOW_WORDS):
    """Fewest different words in any ``width`` words in a row, or None if shorter."""
    words = ' '.join(_repeat_key(seg.get('text')) for seg in segments or []).split()
    if len(words) < width:
        return None
    return min(len(set(words[i:i + width])) for i in range(len(words) - width + 1))


def is_runaway(segments, prompt=''):
    """True when a window looped instead of transcribing.

    Three signs, because a loop takes more than one shape. A window that repeats
    one line past ``MLX_PROMPT_REPEAT_LIMIT`` has stopped listening to the audio,
    whatever set it off. So has one that goes round a few words inside its
    segments, where no two lines match (see ``MLX_LOOP_MAX_DISTINCT``). A window
    whose every segment is a line already in its own prompt has done something
    more specific: it has read the prompt back rather than the recording, which is
    the first step of the runaway that made 3.5 minutes of an interview come back
    as "Звук колокола." — and the step worth catching, since the prompt is ours to
    withdraw.
    """
    if longest_repeat_run(segments) >= MLX_PROMPT_REPEAT_LIMIT:
        return True
    narrowest = narrowest_vocabulary(segments)
    if narrowest is not None and narrowest <= MLX_LOOP_MAX_DISTINCT:
        return True
    if not prompt:
        return False
    keys = {_repeat_key(seg.get('text')) for seg in segments or []}
    keys.discard('')
    if len(keys) != 1:
        return False
    only = keys.pop()
    # One segment is not a run: a window can legitimately hold a single "да",
    # and the prompt behind it will often contain that word.
    return (len(segments) > 1
            and len(only.split()) > 1
            and only in _repeat_key(prompt))


def prompt_tail(text, limit=MLX_PROMPT_CHARS):
    """The end of ``text`` up to its last finished sentence, for the next window.

    Whisper writes in the style of the prompt it is given, punctuation included,
    so a prompt that breaks off mid-sentence teaches it to break off too: in one
    88-minute interview five stretches, up to three windows long, came back with
    1.1 sentence endings per 100 words where the rest of the same transcript held
    12. Each began where a window had ended mid-word and handed its stump on.

    So only finished sentences are carried. On ordinary speech that costs almost
    nothing — measured over a clean transcript of the same interview, the last
    full stop is a median of 4 words back, 14 at the 90th percentile — and where
    a window holds no finished sentence at all, nothing is carried and the next
    window decodes from the audio alone, as this engine did before the prompt was
    carried at all.
    """
    text = ' '.join(str(text or '').split())
    cut = 0
    for match in re.finditer(r'[.!?…]["»\')\]]*(?=\s|$)', text):
        cut = match.end()
    text = text[:cut].strip()
    if len(text) <= limit:
        return text
    return text[-limit:].split(' ', 1)[-1]


_ENDING = re.compile(r'[.!?…]+["»”\')\]]*')
_OPENERS = '"«„“\'([—–-'


def count_sentence_endings(text):
    """Full stops, question marks and the like that actually end a sentence.

    One is counted where the text ends or the next word does not open in lower
    case. Counting every mark was fooled by the loop ``MLX_LOOP_MAX_DISTINCT``
    describes: "и т.д. .д. и т .д." spent 38 full stops on 139 characters, and
    a window whose other 90 seconds held no punctuation at all read as five times
    better punctuated than the recording. Nor does "т.д." in the middle of a
    sentence end it. A script with no case to read counts every ending followed by
    a word, as before.
    """
    count = 0
    for match in _ENDING.finditer(text):
        rest = text[match.end():]
        if not rest.strip():
            count += 1
            continue
        if not rest[0].isspace():
            continue
        nxt = rest.lstrip().lstrip(_OPENERS).lstrip()[:1]
        if nxt.isalnum() and not nxt.islower():
            count += 1
    return count


def sentence_rate(segments):
    """Sentence endings per 1000 characters of a window's text.

    The measure the collapse above is caught by. Per character rather than per
    word so it does not depend on how the language being transcribed spaces
    itself, and per window so it can be held against the rest of the recording.
    """
    text = ' '.join(str(seg.get('text') or '') for seg in segments or []).strip()
    if not text:
        return None
    return 1000.0 * count_sentence_endings(text) / len(text)


def is_collapsed(segments, rates, fraction=MLX_SENTENCE_RATE_FRACTION,
                 settle=MLX_SENTENCE_RATE_WINDOWS):
    """True when a window wrote far less punctuation than the recording does.

    Held against the recording's own median rather than a number in this file:
    what counts as normal punctuation depends on the language, the model and the
    speaker, and none of those are known here. The gap it has to find is not a
    subtle one. Over the 88-minute interview this was written for, the windows
    that read correctly ran from 8.3 to 18.1 sentence endings per 100 words and
    never once went below 8.3; the five collapsed stretches sat at 1.1 to 1.5.

    Until ``settle`` windows have been read there is no median to hold anything
    against, and a recording that simply punctuates sparsely is never flagged —
    it sets its own median.
    """
    if len(rates) < settle:
        return False
    rate = sentence_rate(segments)
    if rate is None:
        return False
    return rate < median(rates) * fraction


def trim_tail(segments, start, end, total,
              min_progress=MLX_WINDOW_MIN_PROGRESS_SEC):
    """A window's segments without its unfinished last one, and where to resume.

    A window is cut out of the waveform at a fixed length, so its final segment
    is whatever the model could make of a sentence that was still going — often
    half a word, and always without the audio that finishes it. Whisper's own
    loop never keeps that segment: it seeks back to where the last finished one
    ended and reads the rest with the audio it needs. This does the same across
    the windows this engine cuts, so the seam costs a few seconds decoded twice
    instead of costing the word it fell in.

    The last window of a take has no unfinished tail — the recording ends there —
    and a window whose only segment reaches back to its start is left alone
    rather than re-read from the beginning for no progress.
    """
    if end >= total - 1e-6:
        return segments, total
    if len(segments) > 1:
        resume = start + float(segments[-1].get('start', 0) or 0)
        if min_progress <= resume - start <= end - start:
            return segments[:-1], resume
    return segments, end


def shift_segments(segments, offset):
    """Move segments and their words onto a clock ``offset`` seconds later."""
    for seg in segments or []:
        seg['start'] = seg.get('start', 0) + offset
        seg['end'] = seg.get('end', 0) + offset
        for w in seg.get('words') or []:
            w['start'] = w.get('start', 0) + offset
            w['end'] = w.get('end', 0) + offset
            if 'score' not in w:
                w['score'] = w.get('probability', 1.0)
    return segments


def _alphabet(ch):
    """'cyr', 'lat' or 'other' for one letter."""
    code = ord(ch)
    if 0x0400 <= code <= 0x052F:
        return 'cyr'
    if ch.isascii() or 0x00C0 <= code <= 0x00FF:
        return 'lat'
    return 'other'


def is_alien_word(word, language=None):
    """True for a word nobody said: one the decoder sampled rather than read.

    Two signs. Cyrillic and Latin run together inside one word — "terugивает",
    "Никонаisme" — which no language does; a hyphen or an apostrophe is where
    they may meet, as in "YouTube-канал" or "SMS-ка", so each side of one is
    judged apart. And, in a language written in Cyrillic, a letter from a third
    alphabet: "ọn", "ọn坐", "generatedți" (see ``MLX_CYRILLIC_LANGUAGES``).
    """
    for part in re.split(r"[-'’]", str(word or '')):
        alphabets = {_alphabet(ch) for ch in part if ch.isalpha()}
        if 'cyr' in alphabets and 'lat' in alphabets:
            return True
        if 'other' in alphabets and language in MLX_CYRILLIC_LANGUAGES:
            return True
    return False


def _join_raw(raws):
    """Segment text from the words' raw spelling, the way Whisper spaced it."""
    raws = [str(r) for r in raws if str(r).strip()]
    if any(r[:1].isspace() for r in raws):
        return ''.join(raws)
    return ' '.join(raws)


def alien_words(segments, language=None):
    """Every word in ``segments`` that ``is_alien_word`` rejects."""
    found = []
    for seg in segments or []:
        words = seg.get('words')
        tokens = ([w.get('word') for w in words] if words
                  else str(seg.get('text') or '').split())
        found.extend(str(t).strip() for t in tokens
                     if is_alien_word(t, language))
    return found


def strip_alien_words(segments, language=None):
    """``segments`` without their alien words, and without any left empty."""
    kept = []
    for seg in segments or []:
        words = seg.get('words')
        if words:
            clean = [w for w in words if not is_alien_word(w.get('word'), language)]
            if len(clean) != len(words):
                seg = dict(seg, words=clean,
                           text=_join_raw(w.get('word', '') for w in clean))
        else:
            tokens = str(seg.get('text') or '').split()
            clean = [t for t in tokens if not is_alien_word(t, language)]
            if len(clean) != len(tokens):
                seg = dict(seg, text=' '.join(clean))
        if str(seg.get('text') or '').strip():
            kept.append(seg)
    return kept


def find_holes(segments, regions, until, min_len=MLX_HOLE_MIN_SEC,
               word_cap=MLX_HOLE_WORD_CAP_SEC):
    """Stretches of speech that no word covers, as ``[(start, end)]``.

    ``regions`` is where the gate heard speech and ``segments`` what the model
    wrote over it, on one clock; nothing past ``until`` is looked at. A word
    covers from its start for at most ``word_cap`` — the word before a skipped
    passage is often stretched across it. A segment with no word timings
    covers its whole span.
    """
    covered = []
    for seg in segments or []:
        words = seg.get('words') or []
        if words:
            for w in words:
                if w.get('start') is None or w.get('end') is None:
                    continue
                lo = float(w['start'])
                covered.append((lo, min(float(w['end']), lo + word_cap)))
        elif seg.get('start') is not None and seg.get('end') is not None:
            covered.append((float(seg['start']), float(seg['end'])))
    covered.sort()

    holes = []
    for r_start, r_end in regions or []:
        r_end = min(r_end, until)
        cursor = r_start
        for lo, hi in covered:
            if hi <= cursor:
                continue
            if lo >= r_end:
                break
            if lo - cursor >= min_len:
                holes.append((cursor, lo))
            cursor = max(cursor, hi)
        if r_end - cursor >= min_len:
            holes.append((cursor, r_end))
    return holes


def words_inside(segments, lo, hi):
    """``segments`` cut down to the words whose middle falls in ``[lo, hi)``."""
    kept = []
    for seg in segments or []:
        words = seg.get('words') or []
        if not words:
            mid = (float(seg.get('start', 0)) + float(seg.get('end', 0))) / 2
            if lo <= mid < hi and str(seg.get('text') or '').strip():
                kept.append(dict(seg))
            continue
        inside = [w for w in words
                  if lo <= (float(w.get('start', 0)) + float(w.get('end', 0))) / 2 < hi]
        if not inside:
            continue
        kept.append(dict(seg, words=inside,
                         start=inside[0].get('start', seg.get('start')),
                         end=inside[-1].get('end', seg.get('end')),
                         text=_join_raw(w.get('word', '') for w in inside)))
    return kept


def probe_language_file(audio_path: str, model_path: str):
    """Return (best_code, top_p, second_p, options) or None on failure."""
    try:
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.load_models import load_model
    except ImportError:
        return None

    dtype = mx.float16
    model = load_model(model_path, dtype=dtype)
    if not model.is_multilingual:
        return 'en', 1.0, 0.0, [{'code': 'en', 'probability': 1.0}]

    mel = log_mel_spectrogram(audio_path, n_mels=model.dims.n_mels, padding=N_SAMPLES)
    mel_segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(dtype)
    _, probs = model.detect_language(mel_segment)
    if not probs:
        return 'en', 0.0, 0.0, []

    best = max(probs, key=probs.get)
    p1 = float(probs[best])
    others = sorted((float(p) for k, p in probs.items() if k != best), reverse=True)
    p2 = others[0] if others else 0.0
    options = sorted(
        ({'code': k, 'probability': float(v)} for k, v in probs.items()),
        key=lambda x: -x['probability'],
    )[:12]
    return best, p1, p2, options


def language_uncertain(top_confidence: float, second_confidence: float) -> bool:
    if top_confidence < LANG_CONFIDENCE_MIN:
        return True
    if (top_confidence - second_confidence) < LANG_CONFIDENCE_MARGIN:
        return True
    return False


class MlxWhisperEngine(EngineAdapter):
    id = 'mlx'
    capabilities = EngineCapabilities(
        word_timestamps=True,
        diarization='external',
        streaming=False,
        multilingual=True,
    )

    def __init__(self, env=None):
        super().__init__(env)
        self._model_path = None

    def load(self):
        # mlx-whisper is stateless — no explicit model load.
        # Just store the model path for use in transcribe calls.
        self._model_path = self.env.model_dir
        log.info('mlx-whisper model path set to %s', self._model_path)

    def probe_window(self, audio_path, probe_secs, total_duration=0):
        """The first stretch of actual speech, rather than the first 30 s of file.

        Overrides the head-of-file default because nothing gates the audio on
        this path: the probe used to hear whatever opened the recording, which on
        a phone interview is ringback. Falls back to the head whenever the search
        finds nothing to prefer over it.
        """
        from ..tracks import SAMPLE_RATE, detect_speech

        search_secs = MLX_LANG_PROBE_SEARCH_SEC
        if total_duration > 0:
            search_secs = min(search_secs, total_duration)
        search_secs = max(search_secs, probe_secs)

        head = load_audio_range(audio_path, 0, search_secs)
        width = int(probe_secs * SAMPLE_RATE)
        try:
            regions = drop_tones(head,
                                 detect_speech(head,
                                               merge_gap=MLX_VAD_MERGE_GAP_SEC,
                                               pad=MLX_VAD_PAD_SEC),
                                 SAMPLE_RATE)
        except Exception as exc:
            log.warning('Language probe: VAD failed (%s) — listening from 0s', exc)
            return head[:width]
        if not regions:
            return head[:width]

        # Pulled back from the end so a probe that finds speech late still gets a
        # full window rather than the two seconds that were left.
        start = min(int(regions[0][0] * SAMPLE_RATE), max(len(head) - width, 0))
        log.info('Language probe: listening from %.0fs', start / SAMPLE_RATE)
        return head[start:start + width]

    def probe_language(self, audio_path, probe_audio):
        tmp_probe = os.path.join(
            tempfile.gettempdir(), f'pine_lang_probe_{os.getpid()}.wav')
        try:
            write_wav(tmp_probe, probe_audio)
            pr = probe_language_file(tmp_probe, self._model_path)
        finally:
            try:
                os.unlink(tmp_probe)
            except OSError:
                pass
        if not pr:
            return None
        best, p1, p2, options = pr
        return LanguageProbe(
            code=best,
            confidence=p1,
            second_confidence=p2,
            options=options,
            uncertain=language_uncertain(p1, p2),
        )

    def _decode_kwargs(self, language=None, prompt=None, conditioned=True):
        """Arguments for one ``mlx_whisper.transcribe`` call.

        ``prompt`` seeds the window with the tail of the last accepted one.
        ``conditioned`` is what a caller drops after ``is_runaway``: it decodes
        the window again with no memory at all, neither carried in nor built up.
        """
        tx_kw = {
            'path_or_hf_repo': self._model_path,
            'word_timestamps': True,
            # The prompt is a 30s window's only memory, so it is what carries a
            # sentence — and with it the punctuation and the casing — across the
            # seam. It carries a mistake across just as faithfully, and
            # mlx-whisper decodes greedily (it raises NotImplementedError on beam
            # search), which is the decoder most prone to making one: on a
            # recording that opened with ringback, "Звук колокола." became the
            # prompt for the window after it, and the window after that, and 3.5
            # minutes of speech came back as that one sentence repeating.
            #
            # Turning this off everywhere was the first answer to that, and it
            # worked — 220 words in place of 15 over the same 240s — but it paid
            # for them with every sentence boundary in every other transcript.
            # So the chain runs again, and what changed is who holds it: it is
            # cut at each window this engine hands over (MLX_PROMPT_WINDOW_SEC),
            # and a window that comes back looping is decoded again with this
            # False and no prompt. A runaway now costs one window, detected, and
            # cannot reach the next one.
            'condition_on_previous_text': bool(conditioned),
            # Its own fallback stops short of sampling; see MLX_TEMPERATURES.
            'temperature': MLX_TEMPERATURES,
        }
        if prompt:
            tx_kw['initial_prompt'] = prompt
        if language:
            tx_kw['language'] = language
        return tx_kw

    def _reread_collapsed(self, path, segments, rates, language, prompt):
        """A punctuated reading of a window that stopped punctuating, if any.

        Returns ``(segments, recovered)``. Without its prompt first, when it had
        one. Then with nothing carried between the 30s pieces inside the window
        either: mlx-whisper conditions each piece on the one before, so a piece
        that loses the punctuation hands the loss to every piece after it, and
        the first retry — a prompt taken away, that memory kept — can come back
        the same. A reading that loops or holds no text is never taken. When
        none recovers, the one that ended the most sentences is kept.
        """
        import mlx_whisper

        readings = [('', True)] if prompt else []
        readings.append(('', False))

        best = segments
        best_rate = sentence_rate(segments) or 0.0
        for reprompt, conditioned in readings:
            result = mlx_whisper.transcribe(
                path, **self._decode_kwargs(language, prompt=reprompt or None,
                                            conditioned=conditioned))
            candidate = result.get('segments', []) or []
            rate = sentence_rate(candidate)
            if rate is None or is_runaway(candidate, reprompt):
                continue
            if not is_collapsed(candidate, rates):
                return candidate, True
            if rate > best_rate:
                best, best_rate = candidate, rate
        return best, False

    def _reread_alien(self, path, segments, rates, language, index, span):
        """A window with no alien words in it, read again if it had any.

        The words are what the decoder sampled rather than read, and on the
        interview this was written for the sampling did not stop at them: the
        last 25 seconds came back as "тысячи DER terugивает ушко понятно…",
        where only two words give the passage away. So the window is read again
        with nothing carried in, and that reading is kept when it has fewer
        such words, does not loop and still punctuates like the recording.
        Whatever is left is struck out word by word.
        """
        import mlx_whisper

        aliens = alien_words(segments, language)
        if not aliens:
            return segments
        log.warning('Window %d (%s) came back with %d word(s) in no alphabet '
                    '%s is written in (%s) — reading it again with nothing '
                    'carried in', index, span, len(aliens), language,
                    ', '.join(aliens[:5]))
        result = mlx_whisper.transcribe(
            path, **self._decode_kwargs(language, conditioned=False))
        candidate = result.get('segments', []) or []
        if (candidate and not is_runaway(candidate)
                and len(alien_words(candidate, language)) < len(aliens)
                and not is_collapsed(candidate, rates)):
            segments = candidate
        else:
            log.warning('Window %d: the second reading was no better — '
                        'striking the words out', index)
        return strip_alien_words(segments, language)

    def _fill_holes(self, samples, start, segments, speech, until, language,
                    index, offset):
        """``segments`` with the speech the decoder skipped read on its own.

        ``segments`` are on the window's clock, which starts at ``start`` on
        the take's; ``speech`` is where the gate heard speech on the take's.
        Only the part of the window being kept — up to ``until`` — is looked
        at: the rest is read again by the next window anyway.

        whisperx gets this for nothing, because it decodes every stretch of
        speech separately and a skip cannot run past the stretch's end. Here a
        greedy 30s piece can jump its timestamp over a passage it did not
        manage to read — 19 seconds of one interview's answer went that way.
        Each hole is read with no prompt, and only the words that fall inside
        it are kept, so the audio either side cannot write a word twice.
        """
        import mlx_whisper

        from ..tracks import SAMPLE_RATE

        regions = [(max(lo, start) - start, min(hi, start + until) - start)
                   for lo, hi in speech
                   if hi > start and lo < start + until]
        holes = find_holes(segments, regions, until)
        if not holes:
            return segments

        total = len(samples) / SAMPLE_RATE
        hole_path = os.path.join(tempfile.gettempdir(),
                                 f'pine_hole_{os.getpid()}.wav')
        try:
            for a, b in holes:
                lo = max(0.0, start + a - MLX_HOLE_PAD_SEC)
                hi = min(total, start + b + MLX_HOLE_PAD_SEC)
                write_wav(hole_path,
                          samples[int(lo * SAMPLE_RATE):int(hi * SAMPLE_RATE)])
                result = mlx_whisper.transcribe(
                    hole_path, **self._decode_kwargs(language, conditioned=False))
                found = result.get('segments', []) or []
                if is_runaway(found):
                    found = []
                found = words_inside(strip_alien_words(found, language),
                                     start + a - lo, start + b - lo)
                recovered = sum(len(seg.get('words') or [])
                                or len(str(seg.get('text') or '').split())
                                for seg in found)
                log.info('Window %d: %.1fs of speech at %.0f-%.0fs held no '
                         'words — read on its own, %d word(s) recovered',
                         index, b - a, start + a + offset, start + b + offset,
                         recovered)
                if found:
                    segments = sorted(
                        list(segments) + shift_segments(found, lo - start),
                        key=lambda seg: seg.get('start', 0))
        finally:
            try:
                os.unlink(hole_path)
            except OSError:
                pass
        return segments

    def _decode_windows(self, samples, language=None, check_cancel=None,
                        rates=None, offset=0.0):
        """Decode one gated take window by window. Returns ``(segments, language)``.

        The segments come back on the take's own clock. Each window is seeded
        with the finished sentences of the last accepted one, which is what
        carries the punctuation and the casing across the seam, and each window
        ends where its last finished segment ended rather than at the length it
        was cut to, so no seam falls in the middle of a word.

        A window that comes back looping is decoded again with nothing carried
        in, and its text is not passed on. One writing far less punctuation than
        the rest of the recording is read again (``_reread_collapsed``); if no
        reading recovers, the prompt it was given goes on to the next window in
        place of its text. One holding words in no alphabet its language uses is
        read again too (``_reread_alien``), and speech no word covers is read on
        its own (``_fill_holes``).

        ``rates`` holds the recording's sentence rate per window. A caller that
        cuts the recording into several takes passes one list to all of them,
        so each take is judged against the recording from its first window.
        ``offset`` is where the take starts in the recording, for the log only:
        a window logged at 3311s is one that can be found in the recording at
        3311s, give or take what the gate cut before it.
        """
        import mlx_whisper

        from ..tracks import SAMPLE_RATE, detect_speech

        total = len(samples) / SAMPLE_RATE
        tmp_path = os.path.join(tempfile.gettempdir(),
                                f'pine_window_{os.getpid()}.wav')
        detected_lang = language or 'en'
        all_segments = []
        rates = [] if rates is None else rates
        prompt = ''
        start = 0.0
        index = 0
        try:
            speech = detect_speech(samples, merge_gap=MLX_VAD_MERGE_GAP_SEC,
                                   pad=MLX_VAD_PAD_SEC)
        except Exception as exc:
            log.warning('VAD failed (%s) — skipped speech will not be looked for',
                        exc)
            speech = []

        try:
            while start < total - 1e-3:
                if check_cancel is not None:
                    check_cancel()

                end = min(start + MLX_PROMPT_WINDOW_SEC, total)
                piece = samples[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
                if len(piece) == 0:
                    break
                write_wav(tmp_path, piece)
                index += 1
                span = f'{start + offset:.0f}-{end + offset:.0f}s'

                result = mlx_whisper.transcribe(
                    tmp_path,
                    **self._decode_kwargs(language, prompt=prompt or None))
                segments = result.get('segments', []) or []
                carry = True
                plain = False

                if is_runaway(segments, prompt):
                    log.warning('Window %d (%s) came back looping — '
                                'decoding it again with no prompt', index, span)
                    result = mlx_whisper.transcribe(
                        tmp_path,
                        **self._decode_kwargs(language, conditioned=False))
                    segments = result.get('segments', []) or []
                    # Whatever set the loop off is in the text being carried, so
                    # the next window starts from the audio and nothing else.
                    carry = False
                    plain = True
                    prompt = ''
                elif is_collapsed(segments, rates):
                    # Checked whether a prompt was carried in or not. Only
                    # checking after a prompt let one unrecovered window end
                    # the checks for good: its prompt was dropped, the next
                    # window came in with none, and every window after that
                    # went unread however little it punctuated.
                    log.warning('Window %d (%s) came back with %.1f '
                                'sentence endings per 1000 characters against '
                                '%.1f for the recording — reading it again',
                                index, span,
                                sentence_rate(segments) or 0.0, median(rates))
                    segments, carry = self._reread_collapsed(
                        tmp_path, segments, rates, language, prompt)
                    if not carry:
                        log.warning('Window %d did not recover — handing on '
                                    'the prompt it was given', index)

                if index == 1:
                    detected_lang = result.get('language', detected_lang)
                # Settled on the first window and held. Two minutes is a thin
                # thing to re-detect a language from, and one window that
                # disagrees writes its stretch of the interview in the wrong
                # alphabet.
                language = language or detected_lang

                if plain:
                    # Already read with nothing carried in: a second reading
                    # the same way has nothing different to offer.
                    segments = strip_alien_words(segments, language)
                else:
                    segments = self._reread_alien(
                        tmp_path, segments, rates, language, index, span)

                segments, resume = trim_tail(segments, start, end, total)
                segments = self._fill_holes(samples, start, segments, speech,
                                            resume - start, language, index,
                                            offset)
                rate = sentence_rate(segments)
                if rate is not None:
                    rates.append(rate)
                # A window whose text is not fit to carry leaves the prompt as it
                # was: the last finished sentences read well, rather than none,
                # which would leave the next window to settle its style alone.
                if carry:
                    prompt = prompt_tail(' '.join(
                        str(seg.get('text') or '') for seg in segments)) or prompt

                log.info('Window %d (%s): %d segment(s), %d word(s), %s sentence '
                         'endings per 1000 characters, %d segment(s) read at a '
                         'fallback temperature — next from %.0fs',
                         index, span, len(segments),
                         sum(len(str(seg.get('text') or '').split())
                             for seg in segments),
                         'no' if rate is None else f'{rate:.1f}',
                         sum(1 for seg in segments
                             if (seg.get('temperature') or 0) > 0),
                         resume + offset)

                all_segments.extend(shift_segments(segments, start))
                start = resume
                del piece
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return all_segments, detected_lang

    def _transcribe_single(self, audio_path, total_duration=0, language=None,
                           check_cancel=None):
        """Transcribe one file with mlx-whisper. Returns (result_dict, language)."""
        samples, splices = self._gated_samples(audio_path, total_duration)
        if samples is None:
            # Nothing was decoded from the file, so there is no waveform to cut
            # windows out of: hand the model the path, as this path did before
            # the prompt was carried at all.
            import mlx_whisper
            result = mlx_whisper.transcribe(audio_path,
                                            **self._decode_kwargs(language))
            segments = shift_segments(result.get('segments', []) or [], 0)
            return {'segments': segments}, result.get('language', 'en')

        segments, detected_lang = self._decode_windows(
            samples, language=language, check_cancel=check_cancel)
        del samples
        gc.collect()

        if splices is not None:
            from ..tracks import remap
            segments = remap(segments, splices)
        return {'segments': segments}, detected_lang

    def _gated_samples(self, audio_path, total_duration):
        """Speech-only samples of the take, as ``(samples, splices)``.

        ``splices`` is None when the gate passed the audio through, and the
        samples are then the recording as it is. Both are None when the file
        could not be decoded at all, which leaves the caller no waveform to work
        on and nothing to do but hand the model the path.
        """
        if total_duration <= 0:
            return None, None
        try:
            samples = load_audio_range(audio_path, 0, total_duration)
        except Exception as exc:
            log.warning('VAD: could not decode %s (%s) — transcribing the file as it is',
                        audio_path, exc)
            return None, None

        return gate_speech(samples)

    def _transcribe_chunked(self, audio_path, total_duration, language,
                            ctx: TranscribeContext):
        """Chunked mlx-whisper on Metal; word timestamps from MLX only."""
        step = CHUNK_SIZE_SEC - CHUNK_OVERLAP_SEC
        chunk_starts = []
        offset = 0
        while offset < total_duration:
            chunk_starts.append(offset)
            offset += step
        total_chunks = len(chunk_starts)

        all_segments = []
        detected_lang = language or None
        chunked_start = time.monotonic()
        # The punctuation every window is judged against belongs to the
        # recording, not the chunk. Started afresh per chunk, it left the first
        # three windows of each chunk unjudged, and a short last chunk unjudged
        # from start to end.
        rates = []

        for i, offset in enumerate(chunk_starts):
            duration = min(CHUNK_SIZE_SEC, total_duration - offset)

            if i == 0:
                msg = f'Chunk 1/{total_chunks} — starting…'
                pct = 0
                eta = None
            else:
                elapsed = time.monotonic() - chunked_start
                avg_secs = elapsed / i
                remaining = avg_secs * (total_chunks - i)
                pct = round(i / total_chunks * 100)
                eta = round(remaining)
                msg = (f'Chunk {i + 1}/{total_chunks} ({pct}%) — '
                       f'~{fmt_elapsed(remaining)} remaining')

            ctx.on_status(stage='transcribing', message=msg,
                          percent=pct, eta_secs=eta)
            log.info('Chunk %d/%d  offset=%.0fs  duration=%.0fs',
                     i + 1, total_chunks, offset, duration)

            # The gate runs per chunk rather than over the whole file so the
            # decoded waveform stays bounded by the chunk size.
            chunk_audio = load_audio_range(audio_path, offset, duration)
            gated_audio, splices = gate_speech(chunk_audio)

            # The prompt is not carried over a chunk seam. Chunks overlap by
            # CHUNK_OVERLAP_SEC, so the tail of the last one was decoded from
            # audio this one is about to hear again — a prompt from the future of
            # the window it would seed. One unprompted window every half hour is
            # the cheaper mistake.
            segments, lang = self._decode_windows(
                gated_audio, language=language or detected_lang,
                check_cancel=ctx.check_cancel, rates=rates, offset=offset)
            if i == 0:
                detected_lang = lang

            if splices is not None:
                # Back onto this chunk's own clock before the chunk offset
                # below puts it back onto the recording's.
                from ..tracks import remap
                segments = remap(segments, splices)

            # Rebase timestamps and filter by ownership zone
            own_start = offset + (CHUNK_OVERLAP_SEC / 2 if i > 0 else 0)
            own_end = (offset + duration
                       - (CHUNK_OVERLAP_SEC / 2 if i < total_chunks - 1 else 0))

            for seg in shift_segments(segments, offset):
                mid = (seg['start'] + seg['end']) / 2
                if own_start <= mid < own_end:
                    all_segments.append(seg)

            del chunk_audio, gated_audio
            gc.collect()
            clear_mlx_cache()
            ctx.check_cancel()

        all_segments.sort(key=lambda s: s.get('start', 0))
        return {'segments': all_segments}, detected_lang or 'en'

    def transcribe(self, audio_path, total_duration, language, ctx: TranscribeContext) -> TranscribeOutput:
        use_chunks = total_duration >= CHUNK_THRESHOLD_SEC and total_duration > 0
        engine_note = ' (Metal-accelerated)'
        ctx.on_status(stage='transcribing',
                      message=f'Transcribing audio...{engine_note}')

        if use_chunks:
            log.info('Using chunked mlx processing (%.0f min)', total_duration / 60)
            result, detected_lang = self._transcribe_chunked(
                audio_path, total_duration, language, ctx)
        else:
            _heartbeat_stop = threading.Event()
            _transcribe_start = time.monotonic()

            def _heartbeat():
                while not _heartbeat_stop.wait(15):
                    elapsed = time.monotonic() - _transcribe_start
                    ctx.on_status(
                        stage='transcribing',
                        message=f'Transcribing... {fmt_elapsed(elapsed)} elapsed{engine_note}',
                    )

            threading.Thread(target=_heartbeat, daemon=True).start()
            try:
                result, detected_lang = self._transcribe_single(
                    audio_path, total_duration, language=language,
                    check_cancel=ctx.check_cancel)
            finally:
                _heartbeat_stop.set()

            if total_duration == 0:
                segs = result.get('segments', [])
                if segs:
                    total_duration = segs[-1].get('end', 0)

        if language:
            detected_lang = language

        # Last, once: both paths above rewrite the word lists (the gate remaps
        # them, chunking rebases them), and this is what leaves each segment's
        # text agreeing with the words the UI will look for inside it.
        segments = result.get('segments', [])
        synchronize_segments_for_ui(segments)

        clear_mlx_cache()
        gc.collect()

        return TranscribeOutput(
            segments=segments,
            language=detected_lang,
            duration_seconds=total_duration,
            audio=None,
        )
