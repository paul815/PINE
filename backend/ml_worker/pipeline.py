"""MLPipeline — probe → transcribe → diarize → clean/map, no Flask, no DB.

The caller (worker ``__main__`` or the in-process fallback) supplies a JobEnv
(paths and model choices resolved from settings), a JobRequest (one recording)
and PipelineEvents (status/progress, language confirmation, cancellation).
The result is the transcript JSON payload, byte-identical in shape to what
PINE has always written.
"""

import logging
import sys
import time
from dataclasses import dataclass

from . import compat
from .audio import get_duration_secs, load_audio_range
from .constants import SPEAKER_LABELS
from .diarize import Diarizer, detect_diarize_device
from .engines import TranscribeContext, create_engine, engine_kind_for_model
from .errors import TranscriptionCancelled

log = logging.getLogger(__name__)


@dataclass
class JobEnv:
    """Settings-derived context, resolved by the Flask side per job."""
    stt_model_id: str
    model_dir: str
    diarize_dir: str
    pyannote_cache: str
    hf_token: str = ''
    hf_offline: bool = False


@dataclass
class JobRequest:
    """One recording to transcribe."""
    recording_id: int
    audio_path: str
    num_speakers: int | None = None
    forced_language: str | None = None   # project preset; skips probing
    confirm_language: bool = True        # False → auto-detect, never ask


def _noop_status(**kwargs):
    return None


def _noop_check_cancel():
    return None


def _no_language_ui(probe):
    raise RuntimeError('Language confirmation requested but no UI is attached')


@dataclass
class PipelineEvents:
    """Callbacks from the pipeline to whoever runs it.

    status(stage=..., message=..., percent=..., eta_secs=..., **extra)
    request_language(probe: LanguageProbe) -> str — block until the user picks
        a language; raise TranscriptionCancelled to abort.
    check_cancel() — raise TranscriptionCancelled when the job must stop.
    """
    status: callable = _noop_status
    request_language: callable = _no_language_ui
    check_cancel: callable = _noop_check_cancel


def map_speakers(raw_segments):
    """Map SPEAKER_XX labels to Moderator / Participant N."""
    seen = {}
    mapping = {}
    for seg in raw_segments:
        spk = seg.get('speaker')
        if spk and spk not in seen:
            idx = len(seen)
            label = SPEAKER_LABELS[idx] if idx < len(SPEAKER_LABELS) else f'Speaker {idx + 1}'
            seen[spk] = label
            mapping[spk] = label

    for seg in raw_segments:
        spk = seg.get('speaker')
        if spk and spk in mapping:
            seg['speaker'] = mapping[spk]

    return mapping


def clean_transcript_segments(segments):
    """Drop empty/degenerate segments and words before persisting transcript JSON."""
    clean_segments = []
    for seg in segments or []:
        start = float(seg.get('start', 0) or 0)
        end = float(seg.get('end', 0) or 0)
        had_words = bool(seg.get('words'))
        words_out = []
        for w in seg.get('words', []) or []:
            w_start = float(w.get('start', 0) or 0)
            w_end = float(w.get('end', 0) or 0)
            word = str(w.get('word', '') or '').strip()
            if not word or w_end <= w_start:
                continue
            words_out.append({
                'word': word,
                'start': w_start,
                'end': w_end,
            })

        text = str(seg.get('text', '') or '').strip()
        if words_out:
            text = ' '.join(w['word'] for w in words_out)
        elif had_words:
            # MLX artifact: keep neither empty nor all-zero-word segments.
            text = ''

        if not text or end <= start:
            continue

        clean_seg = {
            'start': start,
            'end': end,
            'text': text,
            'speaker': seg.get('speaker', ''),
        }
        if words_out:
            clean_seg['words'] = words_out
        clean_segments.append(clean_seg)
    return clean_segments


class MLPipeline:
    """Holds loaded models between jobs and runs the full pipeline per job."""

    def __init__(self):
        self._engine = None
        self._engine_key = None       # (stt_model_id, model_dir)
        self._diarizer = None
        self._diarizer_key = None     # (engine_kind, diarize_dir)

    def _ensure_models(self, env: JobEnv):
        compat.patch_torch_load_for_trusted_checkpoints()

        engine_kind = engine_kind_for_model(env.stt_model_id)
        prefer_mps = (sys.platform == 'darwin'
                      and detect_diarize_device() != 'cpu')

        engine_key = (env.stt_model_id, env.model_dir)
        if self._engine is None or self._engine_key != engine_key:
            self._engine = create_engine(env, prefer_mps=prefer_mps)
            self._engine.load()
            self._engine_key = engine_key
        else:
            self._engine.env = env

        diarizer_key = (engine_kind, env.diarize_dir)
        if self._diarizer is None or self._diarizer_key != diarizer_key:
            self._diarizer = Diarizer(env, engine_kind=engine_kind)
            self._diarizer.load()
            self._diarizer_key = diarizer_key
        else:
            self._diarizer.env = env

    def _resolve_language(self, env: JobEnv, job: JobRequest,
                          events: PipelineEvents, total_duration: float):
        """Optional probe + user confirmation. Returns whisper language code or None for auto."""
        if job.forced_language:
            log.info('Using preset transcription language for recording %s: %s',
                     job.recording_id, job.forced_language)
            return job.forced_language
        if not job.confirm_language:
            return None

        probe_secs = min(30.0, max(total_duration, 0.5) if total_duration > 0 else 30.0)
        try:
            probe_audio = load_audio_range(job.audio_path, 0, probe_secs)
        except Exception as exc:
            log.warning('Language probe: could not load audio sample: %s', exc)
            return None

        try:
            probe = self._engine.probe_language(job.audio_path, probe_audio)
        except Exception as exc:
            log.warning('Language probe (%s) failed: %s', self._engine.id, exc)
            return None
        if probe is None:
            return None
        if probe.uncertain:
            log.info(
                'Language uncertain (%s): best=%s p=%.2f second=%.2f — asking user',
                self._engine.id, probe.code, probe.confidence, probe.second_confidence)
            chosen = events.request_language(probe)
            if not chosen:
                raise RuntimeError('Language confirmation ended without a language')
            return chosen.strip().lower()
        return probe.code

    def run(self, env: JobEnv, job: JobRequest, events: PipelineEvents) -> dict:
        """Run the full pipeline for one recording; returns the transcript payload."""
        recording_id = job.recording_id
        compat.apply_hf_offline(env.hf_offline)

        events.status(stage='loading')
        pipeline_t0 = time.monotonic()

        total_duration = get_duration_secs(job.audio_path)

        self._ensure_models(env)
        log.info('PERF: model loading took %.1fs', time.monotonic() - pipeline_t0)

        forced_lang = self._resolve_language(env, job, events, total_duration)

        ctx = TranscribeContext(
            on_status=events.status,
            check_cancel=events.check_cancel,
        )
        out = self._engine.transcribe(
            job.audio_path, total_duration, forced_lang, ctx)
        total_duration = out.duration_seconds
        detected_lang = out.language

        stt_elapsed = time.monotonic() - pipeline_t0
        log.info('PERF: STT (%s) took %.1fs', self._engine.id, stt_elapsed)

        events.check_cancel()

        diarize_t0 = time.monotonic()
        events.status(stage='diarizing', message='Identifying speakers...')

        segments = out.segments
        try:
            diarize_input = out.audio if out.audio is not None else job.audio_path
            segments = self._diarizer.run(
                diarize_input, segments, recording_id,
                audio_path=job.audio_path, total_duration=total_duration,
                num_speakers=job.num_speakers,
                on_status=events.status)
        except TranscriptionCancelled:
            raise
        except Exception as exc:
            cause = exc
            for _ in range(3):
                nxt = getattr(cause, '__cause__', None) or getattr(cause, '__context__', None)
                if nxt is None:
                    break
                cause = nxt
            try:
                msg = str(cause)[:300]
            except Exception:
                msg = type(cause).__name__
            log.warning('Diarization failed: %s - skipping speaker labels', msg)

        if out.audio is not None:
            out.audio = None
            import gc
            gc.collect()

        log.info('PERF: diarization total took %.1fs', time.monotonic() - diarize_t0)

        speaker_map = map_speakers(segments)
        clean_segments = clean_transcript_segments(segments)

        elapsed_total = time.monotonic() - pipeline_t0
        log.info('PERF: total pipeline took %.1fs for %.0fs audio (%.2fx realtime)',
                 elapsed_total, total_duration, elapsed_total / max(total_duration, 1))

        return {
            'recording_id': recording_id,
            'language': detected_lang,
            'duration_seconds': total_duration,
            'speakers': speaker_map,
            'segments': clean_segments,
        }
