"""mlx-whisper engine for Apple Silicon (Metal-accelerated).

mlx-whisper produces native word timestamps, so this engine needs no
wav2vec2 alignment stage. Diarization stays external (shared pyannote stage).
"""

import gc
import logging
import os
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

    def _decode_kwargs(self, language=None):
        """Arguments for one ``mlx_whisper.transcribe`` call."""
        tx_kw = {
            'path_or_hf_repo': self._model_path,
            'word_timestamps': True,
            # Off, and the comment that used to sit here had it backwards. The
            # prompt is a 30s window's only memory, so carrying it forward does
            # keep long sentences coherent — but it carries a mistake forward
            # just as faithfully, and mlx-whisper decodes greedily (it raises
            # NotImplementedError on beam search), which is the decoder most
            # prone to making one. On a recording that opened with ringback,
            # "Звук колокола." became the prompt for the window after it, and the
            # window after that, and 3.5 minutes of speech came back as that one
            # sentence repeating. Same 240s re-run with this False: 220 words
            # instead of 15. Punctuation and casing are a little worse without
            # it; a lost answer is not a little worse. ``gate_speech`` removes
            # the usual source of the first mistake, and this bounds what one
            # costs when it happens anyway.
            'condition_on_previous_text': False,
        }
        if language:
            tx_kw['language'] = language
        return tx_kw

    def _transcribe_single(self, audio_path, total_duration=0, language=None):
        """Transcribe one file with mlx-whisper. Returns (result_dict, language)."""
        import mlx_whisper

        gated_path, splices, cleanup = self._gated_copy(audio_path, total_duration)
        try:
            result = mlx_whisper.transcribe(gated_path, **self._decode_kwargs(language))
        finally:
            cleanup()

        detected_lang = result.get('language', 'en')
        segments = result.get('segments', [])
        if splices is not None:
            from ..tracks import remap
            segments = remap(segments, splices)
        # Normalize output: ensure words have 'score' field for consistency
        for seg in segments:
            for w in seg.get('words') or []:
                if 'score' not in w:
                    w['score'] = w.get('probability', 1.0)
        result['segments'] = segments
        return result, detected_lang

    def _gated_copy(self, audio_path, total_duration):
        """Speech-only copy of the file as ``(path, splices, cleanup)``.

        Falls back to the original path — ``splices`` None, ``cleanup`` a no-op —
        when the audio cannot be read or the gate declines to cut it.
        """
        def _noop():
            return None

        if total_duration <= 0:
            return audio_path, None, _noop
        try:
            samples = load_audio_range(audio_path, 0, total_duration)
        except Exception as exc:
            log.warning('VAD: could not decode %s (%s) — transcribing the file as it is',
                        audio_path, exc)
            return audio_path, None, _noop

        gated, splices = gate_speech(samples)
        if splices is None:
            del samples
            gc.collect()
            return audio_path, None, _noop

        tmp_path = os.path.join(tempfile.gettempdir(),
                                f'pine_gated_{os.getpid()}.wav')
        write_wav(tmp_path, gated)
        del samples, gated
        gc.collect()

        def _cleanup():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return tmp_path, splices, _cleanup

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
        detected_lang = language or 'en'
        chunked_start = time.monotonic()

        # Reuse a single temp file for all chunks
        tmp_path = os.path.join(tempfile.gettempdir(),
                                f'pine_chunk_{os.getpid()}.wav')

        try:
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

                # mlx-whisper needs a file path; write chunk to a temp WAV file.
                # The gate runs per chunk rather than over the whole file so the
                # decoded waveform stays bounded by the chunk size.
                chunk_audio = load_audio_range(audio_path, offset, duration)
                gated_audio, splices = gate_speech(chunk_audio)
                write_wav(tmp_path, gated_audio)

                import mlx_whisper
                result = mlx_whisper.transcribe(tmp_path,
                                                **self._decode_kwargs(language))

                lang = result.get('language', language or 'en')
                if i == 0:
                    detected_lang = lang

                segments = result.get('segments', [])
                if splices is not None:
                    # Back onto this chunk's own clock before the chunk offset
                    # below puts it back onto the recording's.
                    from ..tracks import remap
                    segments = remap(segments, splices)

                # Rebase timestamps and filter by ownership zone
                own_start = offset + (CHUNK_OVERLAP_SEC / 2 if i > 0 else 0)
                own_end = (offset + duration
                           - (CHUNK_OVERLAP_SEC / 2 if i < total_chunks - 1 else 0))

                for seg in segments:
                    seg['start'] = seg.get('start', 0) + offset
                    seg['end'] = seg.get('end', 0) + offset
                    if 'words' in seg:
                        for w in seg['words']:
                            w['start'] = w.get('start', 0) + offset
                            w['end'] = w.get('end', 0) + offset
                            if 'score' not in w:
                                w['score'] = w.get('probability', 1.0)
                    mid = (seg['start'] + seg['end']) / 2
                    if own_start <= mid < own_end:
                        all_segments.append(seg)

                del chunk_audio, gated_audio
                gc.collect()
                clear_mlx_cache()
                ctx.check_cancel()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        all_segments.sort(key=lambda s: s.get('start', 0))
        return {'segments': all_segments}, detected_lang

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
                    audio_path, total_duration, language=language)
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
