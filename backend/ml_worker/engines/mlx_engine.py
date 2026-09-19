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

from ..audio import clear_mlx_cache, fmt_elapsed, load_audio_range, write_wav
from ..constants import (
    CHUNK_OVERLAP_SEC,
    CHUNK_SIZE_SEC,
    CHUNK_THRESHOLD_SEC,
    LANG_CONFIDENCE_MARGIN,
    LANG_CONFIDENCE_MIN,
    MLX_LANG_PROBE_SEARCH_SEC,
    MLX_PROMPT_CHARS,
    MLX_PROMPT_REPEAT_LIMIT,
    MLX_PROMPT_WINDOW_SEC,
    MLX_VAD_MERGE_GAP_SEC,
    MLX_VAD_MIN_CUT_SEC,
    MLX_VAD_MIN_FLATNESS,
    MLX_VAD_MIN_KEEP_FRACTION,
    MLX_VAD_PAD_SEC,
    MLX_VAD_TONE_MIN_SEC,
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

    Run this last, after any stage that rewrites the word list: it is what leaves
    ``text`` agreeing with ``words``.
    """
    for seg in segments or []:
        words = seg.get('words')
        if not words:
            continue

        raws = [('' if w.get('word') is None else str(w.get('word', '')))
                for w in words]
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


def gate_speech(samples):
    """Keep the speech of one mono take, as ``(samples, splices)``.

    mlx-whisper has no VAD, so whatever is in the file reaches the model, and
    Whisper does not skip what is not speech — it describes it. A phone interview
    opening on ~45s of ringback comes back as "Звук колокола.", which
    ``condition_on_previous_text`` then feeds to the next window as context. One
    38-minute recording lost its first 2.5 minutes that way, the consent question
    among them. whisperx never sees this because its own VAD runs first.

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
        regions = detect_speech(samples,
                                merge_gap=MLX_VAD_MERGE_GAP_SEC,
                                pad=MLX_VAD_PAD_SEC)
        regions = drop_tones(samples, regions, SAMPLE_RATE)
    except Exception as exc:
        log.warning('VAD failed (%s) — transcribing the audio as it is', exc)
        return samples, None

    kept = sum(end - start for start, end in regions)
    # No regions at all is the gate saying it found no loud/quiet structure to
    # measure, not that the take is silent. Under the keep floor it found
    # something, but too little to be believed over the recording itself.
    if not regions or kept < total * MLX_VAD_MIN_KEEP_FRACTION:
        log.info('VAD kept %.0f%% of %.0fs — passing the audio through untouched',
                 (kept / total) * 100, total)
        return samples, None
    if (total - kept) < MLX_VAD_MIN_CUT_SEC:
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


def is_runaway(segments, prompt=''):
    """True when a window looped instead of transcribing.

    Two signs, because they appear at different times. A window that repeats one
    line past ``MLX_PROMPT_REPEAT_LIMIT`` has stopped listening to the audio,
    whatever set it off. A window whose every segment is a line already in its
    own prompt has done something more specific: it has read the prompt back
    rather than the recording, which is the first step of the runaway that made
    3.5 minutes of an interview come back as "Звук колокола." — and the step
    worth catching, since the prompt is ours to withdraw.
    """
    if longest_repeat_run(segments) >= MLX_PROMPT_REPEAT_LIMIT:
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
    """The end of ``text``, cut on a word boundary, for the next window's prompt."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    return text[-limit:].split(' ', 1)[-1]


def plan_windows(samples, sample_rate=16000, max_sec=MLX_PROMPT_WINDOW_SEC):
    """Prompt-window bounds as ``[(start_sec, end_sec)]``, tiling the whole take.

    Cut in silence wherever there is silence to cut in, so a window boundary does
    not land mid-word: the prompt carries the sentence across, but only a gap
    keeps the two halves of a word together within one decode. The search is
    limited to the second half of each window so a pause early on cannot leave a
    ten-second window behind. With no gap to use, the window is cut at its
    length — Whisper already breaks the audio every 30 s, and one more seam costs
    less than letting a runaway run to the end of the recording.
    """
    from ..tracks import detect_speech

    total = len(samples) / sample_rate
    if total <= max_sec:
        return [(0.0, total)]

    try:
        regions = detect_speech(samples,
                                merge_gap=MLX_VAD_MERGE_GAP_SEC,
                                pad=MLX_VAD_PAD_SEC)
    except Exception as exc:
        log.warning('Prompt windows: VAD failed (%s) — cutting on length alone', exc)
        regions = []
    gaps = [(before[1] + after[0]) / 2
            for before, after in zip(regions, regions[1:], strict=False)]

    windows = []
    start = 0.0
    while start < total - 1e-6:
        limit = start + max_sec
        if limit >= total:
            windows.append((start, total))
            break
        cut = max((g for g in gaps if start + max_sec / 2 <= g <= limit),
                  default=limit)
        windows.append((start, cut))
        start = cut
    return windows


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
        }
        if prompt:
            tx_kw['initial_prompt'] = prompt
        if language:
            tx_kw['language'] = language
        return tx_kw

    def _decode_windows(self, samples, language=None, check_cancel=None):
        """Decode one gated take window by window. Returns ``(segments, language)``.

        The segments come back on the take's own clock. Each window is seeded
        with the tail of the last accepted one, which is what carries a sentence
        — and its punctuation — across the seam; a window that comes back looping
        is decoded again with nothing carried in, and its text is not passed on.
        """
        import mlx_whisper

        from ..tracks import SAMPLE_RATE

        windows = plan_windows(samples)
        tmp_path = os.path.join(tempfile.gettempdir(),
                                f'pine_window_{os.getpid()}.wav')
        detected_lang = language or 'en'
        all_segments = []
        prompt = ''

        try:
            for i, (start, end) in enumerate(windows):
                if check_cancel is not None:
                    check_cancel()

                piece = samples[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
                if len(piece) == 0:
                    continue
                write_wav(tmp_path, piece)

                result = mlx_whisper.transcribe(
                    tmp_path,
                    **self._decode_kwargs(language, prompt=prompt or None))
                segments = result.get('segments', []) or []

                if is_runaway(segments, prompt):
                    log.warning('Window %d/%d (%.0f-%.0fs) came back looping — '
                                'decoding it again with no prompt',
                                i + 1, len(windows), start, end)
                    result = mlx_whisper.transcribe(
                        tmp_path,
                        **self._decode_kwargs(language, conditioned=False))
                    segments = result.get('segments', []) or []
                    # Whatever set the loop off is in the text being carried, so
                    # the next window starts from the audio and nothing else.
                    prompt = ''
                else:
                    prompt = prompt_tail(' '.join(
                        str(seg.get('text') or '') for seg in segments)) or prompt

                if i == 0:
                    detected_lang = result.get('language', detected_lang)
                # Settled on the first window and held. Two minutes is a thin
                # thing to re-detect a language from, and one window that
                # disagrees writes its stretch of the interview in the wrong
                # alphabet.
                language = language or detected_lang

                all_segments.extend(shift_segments(segments, start))
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
                check_cancel=ctx.check_cancel)
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
