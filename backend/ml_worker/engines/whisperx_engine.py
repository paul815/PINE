"""WhisperX engine: faster-whisper decode + wav2vec2 forced alignment.

Runs on CUDA (float16, int8 below 6 GB VRAM) or CPU (int8). CTranslate2 has
no MPS backend, so on Apple Silicon this engine runs on CPU — the MLX engine
is the preferred Mac path.
"""

import gc
import logging
import os
import threading
import time

from .. import compat
from ..audio import fmt_elapsed, load_audio_range
from ..constants import (
    ALIGN_MODEL_CANDIDATES,
    CHUNK_OVERLAP_SEC,
    CHUNK_SIZE_SEC,
    CHUNK_THRESHOLD_SEC,
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


def detect_torch_device():
    """Return (device, compute_type) for CTranslate2/whisperx on this machine."""
    try:
        import torch
        if torch.cuda.is_available():
            compute_type = 'float16'
            vram = torch.cuda.get_device_properties(0)
            total_gb = (getattr(vram, 'total_memory', 0) or
                        getattr(vram, 'total_mem', 0)) / (1024 ** 3)
            if total_gb < 6:
                compute_type = 'int8'
            log.info('Using CUDA — %.0f GB VRAM, compute_type=%s',
                     total_gb, compute_type)
            return 'cuda', compute_type
        # cuda.is_available() is False for CPU-only PyTorch (+cpu wheels) even if the
        # NVIDIA driver and CUDA toolkit are installed system-wide.
        hint = ''
        try:
            ver = torch.__version__ or ''
            if '+cpu' in ver:
                hint = (
                    ' Installed PyTorch is CPU-only (%s); for GPU, reinstall torch with CUDA '
                    'from pytorch.org (e.g. cu128 matching your driver).'
                ) % ver
            elif '+' in ver and 'cu' in ver.split('+', 1)[1]:
                hint = (
                    ' PyTorch is CUDA-enabled (%s) but cuda.is_available() is false — '
                    'check NVIDIA driver, GPU visibility, and reboot after driver updates.'
                ) % ver
        except Exception:
            pass
        log.info('No CUDA in this PyTorch build — using CPU with int8.%s', hint)
    except ImportError:
        pass
    return 'cpu', 'int8'


def align_model_candidates(language_code: str):
    """Return ordered list of explicit align model ids to try for this language."""
    lang = (language_code or '').strip().lower()
    per_lang_env = os.environ.get(f'PINE_ALIGN_MODEL_{lang.upper()}', '').strip()
    if per_lang_env:
        return [m.strip() for m in per_lang_env.split(',') if m.strip()]
    global_env = os.environ.get('PINE_ALIGN_MODEL', '').strip()
    if global_env:
        return [m.strip() for m in global_env.split(',') if m.strip()]
    return ALIGN_MODEL_CANDIDATES.get(lang, [])


def probe_language_whisperx(pipeline, audio_np):
    """Return (language_code, confidence, options_for_ui) using first ~30s logic from whisperx."""
    import numpy as np
    from whisperx.audio import log_mel_spectrogram, N_SAMPLES

    model = getattr(pipeline, 'model', None)
    if model is None or not getattr(model.model, 'is_multilingual', True):
        return 'en', 1.0, [{'code': 'en', 'probability': 1.0}]

    audio_np = np.asarray(audio_np, dtype=np.float32).flatten()
    if audio_np.shape[0] < 4000:
        return 'en', 0.0, []

    model_n_mels = model.feat_kwargs.get('feature_size')
    segment = log_mel_spectrogram(
        audio_np[:N_SAMPLES] if audio_np.shape[0] >= N_SAMPLES else audio_np,
        n_mels=model_n_mels if model_n_mels is not None else 80,
        padding=0 if audio_np.shape[0] >= N_SAMPLES else N_SAMPLES - audio_np.shape[0],
    )
    encoder_output = model.encode(segment)
    results = model.model.detect_language(encoder_output)
    language_token, language_probability = results[0][0]
    code = str(language_token[2:-2])
    conf = float(language_probability)
    return code, conf, [{'code': code, 'probability': conf}]


class WhisperXEngine(EngineAdapter):
    id = 'whisperx'
    capabilities = EngineCapabilities(
        word_timestamps=True,
        diarization='external',
        streaming=False,
        multilingual=True,
    )

    def __init__(self, env=None):
        super().__init__(env)
        self._model = None
        self._model_dir = None
        self._device = None
        self._compute_type = None
        # Wav2Vec2 align model (reused across recordings; cleared on lang/device change)
        self._cached_align_model = None
        self._cached_align_metadata = None
        self._cached_align_language = None
        self._cached_align_device = None
        # Remember failing align model combinations to avoid repeated HF retries on each chunk/job.
        self._failed_align_keys = set()  # {(language_code, device)}

    @property
    def _hf_offline(self):
        return bool(getattr(self.env, 'hf_offline', False))

    def load(self):
        model_dir = self.env.model_dir
        if self._model is not None and self._model_dir == model_dir:
            return
        self._model = None
        if self._device is None:
            self._device, self._compute_type = detect_torch_device()
        import whisperx
        log.info('Loading WhisperX model from %s ...', model_dir)
        self._model = whisperx.load_model(
            model_dir,
            self._device,
            compute_type=self._compute_type,
        )
        self._model_dir = model_dir
        log.info('WhisperX model loaded.')

    def probe_language(self, audio_path, probe_audio):
        code, conf, options = probe_language_whisperx(self._model, probe_audio)
        return LanguageProbe(
            code=code,
            confidence=conf,
            options=options,
            uncertain=conf < LANG_CONFIDENCE_MIN,
        )

    # ── alignment ──

    def _get_align_model(self, whisperx, language_code, device):
        """Load or return cached alignment model for language_code + device."""
        key = (language_code, device)
        if key in self._failed_align_keys:
            raise RuntimeError(
                f'Alignment model previously failed for language={language_code} device={device}')
        if (self._cached_align_model is not None
                and self._cached_align_language == language_code
                and self._cached_align_device == device):
            return self._cached_align_model, self._cached_align_metadata
        if self._cached_align_model is not None:
            try:
                del self._cached_align_model
            except Exception:
                pass
            self._cached_align_model = None
            self._cached_align_metadata = None
            gc.collect()
        candidates = align_model_candidates(language_code)
        attempts = candidates if candidates else [None]
        errors = []
        model_a = None
        metadata = None
        for model_name in attempts:
            try:
                with compat.allow_hf_network_for_align(self._hf_offline):
                    kwargs = dict(language_code=language_code, device=device)
                    if model_name:
                        kwargs['model_name'] = model_name
                    model_a, metadata = whisperx.load_align_model(**kwargs)
                if model_name:
                    log.info('Loaded align model override for %s: %s',
                             language_code, model_name)
                break
            except Exception as exc:
                errors.append(exc)
                if model_name:
                    log.warning(
                        'Align model failed for lang=%s model=%s: %s',
                        language_code, model_name, exc)
                else:
                    log.warning(
                        'Default align model failed for lang=%s: %s',
                        language_code, exc)
                continue

        if model_a is None or metadata is None:
            self._failed_align_keys.add(key)
            raise errors[-1] if errors else RuntimeError(
                f'Failed to load align model for language={language_code}')
        self._cached_align_model = model_a
        self._cached_align_metadata = metadata
        self._cached_align_language = language_code
        self._cached_align_device = device
        return model_a, metadata

    # ── decoding ──

    def _transcribe_with_oom_retry(self, audio, language=None):
        """Transcribe with automatic batch_size halving on CUDA OOM."""
        batch_size = 16 if self._device == 'cuda' else 4
        min_batch = 2

        while batch_size >= min_batch:
            tx_kw = dict(batch_size=batch_size, print_progress=True)
            if language:
                tx_kw['language'] = language
            try:
                return self._model.transcribe(audio, **tx_kw)
            except RuntimeError as exc:
                if ('out of memory' in str(exc).lower()
                        and self._device == 'cuda'
                        and batch_size > min_batch):
                    log.warning(
                        'CUDA OOM with batch_size=%d — halving and retrying',
                        batch_size)
                    import torch
                    torch.cuda.empty_cache()
                    gc.collect()
                    batch_size = batch_size // 2
                else:
                    raise

        # Final attempt with min_batch — let any error propagate
        tx_kw = dict(batch_size=min_batch, print_progress=True)
        if language:
            tx_kw['language'] = language
        return self._model.transcribe(audio, **tx_kw)

    def _transcribe_chunked(self, whisperx, audio_path, total_duration,
                            language, ctx: TranscribeContext):
        """Transcribe + align long audio in chunks to limit RAM usage.

        Returns (result_dict, detected_language).
        """
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
        align_enabled = True

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

            ctx.on_status(stage='transcribing', message=msg, percent=pct, eta_secs=eta)
            log.info('Chunk %d/%d  offset=%.0fs  duration=%.0fs',
                     i + 1, total_chunks, offset, duration)

            chunk_audio = load_audio_range(audio_path, offset, duration)

            result = self._transcribe_with_oom_retry(chunk_audio, language=language)

            lang = result.get('language', language or 'en')
            if i == 0:
                detected_lang = language or lang

            # Align (cached align model, reused across chunks and recordings)
            if align_enabled:
                try:
                    model_a, metadata = self._get_align_model(
                        whisperx, detected_lang, self._device)
                    result = whisperx.align(
                        result['segments'], model_a, metadata, chunk_audio,
                        self._device, return_char_alignments=False)
                except Exception as exc:
                    align_enabled = False
                    log.warning(
                        'Alignment failed for chunk %d (lang=%s): %s - '
                        'skipping alignment for remaining chunks',
                        i, detected_lang, exc)

            # Ownership boundaries — each chunk keeps segments whose midpoint
            # falls inside its exclusive zone, eliminating overlap duplicates.
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
                mid = (seg['start'] + seg['end']) / 2
                if own_start <= mid < own_end:
                    all_segments.append(seg)

            del chunk_audio
            gc.collect()
            if self._device == 'cuda':
                import torch
                torch.cuda.empty_cache()
            ctx.check_cancel()

        all_segments.sort(key=lambda s: s.get('start', 0))
        return {'segments': all_segments}, detected_lang

    def transcribe(self, audio_path, total_duration, language, ctx: TranscribeContext) -> TranscribeOutput:
        import whisperx

        use_chunks = total_duration >= CHUNK_THRESHOLD_SEC and total_duration > 0
        audio = None

        if use_chunks:
            log.info('Using chunked processing (%.0f min)', total_duration / 60)
            result, detected_lang = self._transcribe_chunked(
                whisperx, audio_path, total_duration, language, ctx)
        else:
            audio = whisperx.load_audio(audio_path)
            total_duration = audio.shape[0] / 16000
            device_note = ' (CPU - may be slow)' if self._device == 'cpu' else ''
            ctx.on_status(stage='transcribing',
                          message=f'Transcribing audio...{device_note}')

            _heartbeat_stop = threading.Event()
            _transcribe_start = time.monotonic()

            def _heartbeat():
                while not _heartbeat_stop.wait(15):
                    elapsed = time.monotonic() - _transcribe_start
                    ctx.on_status(
                        stage='transcribing',
                        message=f'Transcribing... {fmt_elapsed(elapsed)} elapsed{device_note}',
                    )

            threading.Thread(target=_heartbeat, daemon=True).start()
            try:
                result = self._transcribe_with_oom_retry(audio, language=language)
            finally:
                _heartbeat_stop.set()

            detected_lang = language or result.get('language', 'en')
            ctx.on_status(stage='aligning', message='Aligning word timestamps...')
            try:
                model_a, metadata = self._get_align_model(
                    whisperx, detected_lang, self._device)
                result = whisperx.align(
                    result['segments'], model_a, metadata, audio,
                    self._device, return_char_alignments=False)
            except Exception as exc:
                log.warning('Alignment failed for lang=%s: %s - skipping', detected_lang, exc)

        return TranscribeOutput(
            segments=result.get('segments', []),
            language=detected_lang,
            duration_seconds=total_duration,
            audio=audio,
        )
