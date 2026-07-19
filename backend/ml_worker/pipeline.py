"""MLPipeline — probe → transcribe ∥ diarize → clean/map, no Flask, no DB.

The caller (worker ``__main__`` or the in-process fallback) supplies a JobEnv
(paths and model choices resolved from settings), a JobRequest (one recording)
and PipelineEvents (status/progress, language confirmation, cancellation).
The result is the transcript JSON payload, byte-identical in shape to what
PINE has always written.
"""

import logging
import sys
import threading
import time
from dataclasses import dataclass, replace

from . import compat
from .audio import get_duration_secs, load_audio_range
from .constants import PARALLEL_STAGES, SPEAKER_LABELS
from .diarize import Diarizer, detect_diarize_device
from .engines import TranscribeContext, create_engine, engine_kind_for_model
from .errors import TranscriptionCancelled
from .progress import ProgressMapper

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


def _log_diarization_failure(exc):
    """Diarization is best-effort: a failure costs speaker labels, not the job."""
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


class _DiarizeTask:
    """The audio-only half of diarization, optionally run under the STT stage.

    Owns the thread so the pipeline never leaves one behind: the ML worker
    process is reused between jobs rather than killed, so a diarization still
    running after its job ended would compete with the next one for the GPU.
    Every exit path joins.

    Errors are held rather than raised in the thread, and handed back on
    ``result()``, which keeps diarization best-effort exactly as it was when
    it ran inline — except cancellation, which is the job ending and must
    propagate.
    """

    def __init__(self, diarizer, job, total_duration, check_cancel):
        self._diarizer = diarizer
        self._job = job
        self._total_duration = total_duration
        self._check_cancel = check_cancel
        self._thread = None
        self._result = None
        self._error = None
        self.elapsed = 0.0

    @property
    def started(self):
        return self._thread is not None

    def start(self):
        self._thread = threading.Thread(
            target=self._work, name='diarize', daemon=True)
        self._thread.start()

    def run(self, diarize_input=None, on_status=_noop_status):
        """Run it here and now — the serial path."""
        self._work(diarize_input, on_status=on_status)

    def _work(self, diarize_input=None, on_status=_noop_status):
        t0 = time.monotonic()
        try:
            self._result = self._diarizer.compute(
                diarize_input if diarize_input is not None else self._job.audio_path,
                self._job.recording_id,
                audio_path=self._job.audio_path,
                total_duration=self._total_duration,
                num_speakers=self._job.num_speakers,
                on_status=on_status,
                check_cancel=self._check_cancel)
        except BaseException as exc:      # noqa: BLE001 — re-raised in result()
            self._error = exc
        finally:
            self.elapsed = time.monotonic() - t0

    def join(self):
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()

    def result(self):
        """Join and return the diarization, or None if it did not produce one."""
        self.join()
        log.info('PERF: diarization took %.1fs', self.elapsed)
        if self._error is not None:
            if isinstance(self._error, TranscriptionCancelled):
                raise self._error
            _log_diarization_failure(self._error)
            return None
        return self._result


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
        compat.apply_hf_offline(env.hf_offline)

        pipeline_t0 = time.monotonic()
        total_duration = get_duration_secs(job.audio_path)

        # Everything downstream reports its own local 0→100; the mapper folds
        # those into the single scale the UI shows. Nothing below this line
        # should emit a percent of its own.
        progress = ProgressMapper(total_duration, events.status,
                                  parallel=PARALLEL_STAGES).start()
        events = replace(events, status=progress)
        events.status(stage='loading')

        try:
            return self._run(env, job, events, progress,
                             pipeline_t0, total_duration)
        finally:
            progress.stop()

    def _run(self, env, job, events, progress, pipeline_t0, total_duration):
        recording_id = job.recording_id

        self._ensure_models(env)
        log.info('PERF: model loading took %.1fs', time.monotonic() - pipeline_t0)

        # The language prompt blocks on the user — their thinking time is not
        # the job being slow, so keep it out of the ETA.
        progress.pause()
        try:
            forced_lang = self._resolve_language(env, job, events, total_duration)
        finally:
            progress.resume()

        ctx = TranscribeContext(
            on_status=events.status,
            check_cancel=events.check_cancel,
        )

        diarize = _DiarizeTask(self._diarizer, job, total_duration,
                               events.check_cancel)
        if PARALLEL_STAGES:
            # Started before transcription rather than after it: pyannote reads
            # only the audio. Its progress is deliberately not reported while it
            # runs underneath the STT stage — ProgressMapper gives each stage a
            # disjoint band and clamps monotonically, so two stages reporting at
            # once would pin the bar to the diarize band and silently swallow
            # transcription's progress.
            diarize.start()

        try:
            out = self._engine.transcribe(
                job.audio_path, total_duration, forced_lang, ctx)
            total_duration = out.duration_seconds
            detected_lang = out.language

            stt_elapsed = time.monotonic() - pipeline_t0
            log.info('PERF: STT (%s) took %.1fs', self._engine.id, stt_elapsed)

            events.check_cancel()

            diarize_t0 = time.monotonic()
            events.status(stage='diarizing', message='Identifying speakers...')

            if not diarize.started:
                # Serial path: hand it the waveform the engine already decoded.
                diarize.run(out.audio, on_status=events.status)
            diarization = diarize.result()
            log.info('PERF: waited %.1fs for diarization after STT',
                     time.monotonic() - diarize_t0)
        finally:
            diarize.join()

        segments = out.segments
        if diarization is not None:
            try:
                segments = self._diarizer.assign(diarization, segments)
            except Exception as exc:      # noqa: BLE001 — best-effort labels
                _log_diarization_failure(exc)

        if out.audio is not None:
            out.audio = None
            import gc
            gc.collect()

        events.status(stage='finalizing', message='Finishing up...')
        speaker_map = map_speakers(segments)
        clean_segments = clean_transcript_segments(segments)
        progress.finish()

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
