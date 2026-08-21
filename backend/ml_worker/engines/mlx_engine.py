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
)
from .base import (
    EngineAdapter,
    EngineCapabilities,
    LanguageProbe,
    TranscribeContext,
    TranscribeOutput,
)

log = logging.getLogger(__name__)


def synchronize_segments_for_ui(segments):
    """Normalize mlx-whisper ``text`` / ``word`` fields so the recording UI can align words.

    MLX tokens often include leading spaces; trimming each word and joining with a
    single space matches what ``recording.html`` expects from ``indexOf``.
    """
    for seg in segments or []:
        words = seg.get('words')
        if not words:
            continue
        tokens = []
        for w in words:
            raw = w.get('word', '')
            t = str(raw).strip() if raw is not None else ''
            if t:
                tokens.append(t)
            w['word'] = t
        if tokens:
            seg['text'] = ' '.join(tokens)


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

    def _transcribe_single(self, audio_path, language=None):
        """Transcribe one file with mlx-whisper. Returns (result_dict, language)."""
        import mlx_whisper
        tx_kw = {
            'path_or_hf_repo': self._model_path,
            'word_timestamps': True,
            # True keeps 30s windows coherent; False can cause garbled / duplicated text.
            'condition_on_previous_text': True,
        }
        if language:
            tx_kw['language'] = language
        result = mlx_whisper.transcribe(audio_path, **tx_kw)
        detected_lang = result.get('language', 'en')
        # Normalize output: ensure words have 'score' field for consistency
        for seg in result.get('segments', []):
            if 'words' in seg:
                for w in seg['words']:
                    if 'score' not in w:
                        w['score'] = w.get('probability', 1.0)
        synchronize_segments_for_ui(result.get('segments', []))
        return result, detected_lang

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

                # mlx-whisper needs a file path; write chunk to a temp WAV file
                chunk_audio = load_audio_range(audio_path, offset, duration)
                write_wav(tmp_path, chunk_audio)

                import mlx_whisper
                tx_kw = {
                    'path_or_hf_repo': self._model_path,
                    'word_timestamps': True,
                    'condition_on_previous_text': True,
                }
                if language:
                    tx_kw['language'] = language
                result = mlx_whisper.transcribe(tmp_path, **tx_kw)

                lang = result.get('language', language or 'en')
                if i == 0:
                    detected_lang = lang

                # Rebase timestamps and filter by ownership zone
                own_start = offset + (CHUNK_OVERLAP_SEC / 2 if i > 0 else 0)
                own_end = (offset + duration
                           - (CHUNK_OVERLAP_SEC / 2 if i < total_chunks - 1 else 0))

                for seg in result.get('segments', []):
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

                del chunk_audio
                gc.collect()
                clear_mlx_cache()
                ctx.check_cancel()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        all_segments.sort(key=lambda s: s.get('start', 0))
        synchronize_segments_for_ui(all_segments)
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
                    audio_path, language=language)
            finally:
                _heartbeat_stop.set()

            if total_duration == 0:
                segs = result.get('segments', [])
                if segs:
                    total_duration = segs[-1].get('end', 0)

        if language:
            detected_lang = language

        clear_mlx_cache()
        gc.collect()

        return TranscribeOutput(
            segments=result.get('segments', []),
            language=detected_lang,
            duration_seconds=total_duration,
            audio=None,
        )
