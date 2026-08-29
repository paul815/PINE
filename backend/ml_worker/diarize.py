"""Speaker diarization stage (pyannote), shared by every STT engine.

Two loading modes:
  * ``whisperx`` — ``whisperx.diarize.DiarizationPipeline`` +
    ``whisperx.assign_word_speakers`` (Windows/Linux path);
  * ``native``  — plain ``pyannote.audio.Pipeline`` + our own word/segment
    speaker assignment (Apple Silicon path, avoids WhisperX diarization import).

Both consume the unified segment format and return it with ``speaker`` filled.

The work splits in two: ``compute`` needs only the audio, ``assign`` needs only
the transcript plus what ``compute`` returned. ``run`` does both back to back;
the pipeline calls them separately so the expensive half can overlap the STT
stage it does not actually depend on.
"""

import logging
import os
import sys
import time

from . import compat
from .audio import load_audio_range
from .constants import (
    DIARIZE_CHUNK_OVERLAP_SEC,
    DIARIZE_CHUNK_SIZE_SEC,
    DIARIZE_CHUNK_THRESHOLD_SEC,
)
from .engines.base import _noop_check_cancel, _noop_status
from .errors import TranscriptionCancelled

log = logging.getLogger(__name__)

DEFAULT_DIARIZATION_MODEL = 'pyannote/speaker-diarization-community-1'


def speaker_kwargs(num_speakers=None):
    """Speaker-count constraint passed to the pyannote diarization pipeline.

    When the recording has an explicit speaker count, pin pyannote to
    exactly that many speakers (best quality when the count is known —
    e.g. 2 for a 1-on-1 interview, which avoids a spurious third voice).
    Otherwise fall back to the env min/max hints, which default to 2..4.
    That range is a constraint, not a guess: PINE only ever runs on
    interview recordings with two to four participants. Material outside
    that range — a monologue, or a group of five and up — needs an explicit
    num_speakers on the recording, or PINE_MIN_SPEAKERS / PINE_MAX_SPEAKERS
    for a whole batch.
    """
    try:
        n = int(num_speakers) if num_speakers else 0
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return {'num_speakers': n}
    _min = int(os.environ.get('PINE_MIN_SPEAKERS', '2'))
    _max = int(os.environ.get('PINE_MAX_SPEAKERS', '4'))
    return {'min_speakers': _min, 'max_speakers': _max}


def pipeline_hook_kwargs(pipeline, check_cancel):
    """``hook=`` for pyannote, so a long diarization can be cancelled.

    One call to pyannote is otherwise uninterruptible — it returns when it
    returns. That was survivable while diarization ran last and cancelling
    during it was rare; running it underneath transcription makes it the thing
    a cancel most often lands in, and an abandoned run would hold the GPU into
    the next job (the worker process is reused, not killed, on cancel).

    pyannote calls the hook between internal steps and between batches, so
    raising from it aborts within seconds. Returns {} when the installed
    pyannote takes no hook, in which case cancellation waits it out.
    """
    try:
        import inspect
        if 'hook' not in inspect.signature(pipeline.apply).parameters:
            return {}
    except (AttributeError, TypeError, ValueError):
        return {}

    def hook(step_name, step_artifact=None, file=None, total=None,
             completed=None):
        check_cancel()

    return {'hook': hook}


def detect_diarize_device():
    """Preferred torch device for pyannote on this machine."""
    try:
        import torch
        if torch.cuda.is_available():
            return 'cuda'
        if (sys.platform == 'darwin'
                and getattr(torch.backends.mps, 'is_available', lambda: False)()):
            return os.environ.get('PINE_DIARIZE_DEVICE', 'mps')
    except ImportError:
        pass
    return 'cpu'


def unwrap_annotation(raw):
    """Reduce pyannote pipeline output to an Annotation with ``itertracks``.

    For community-1 (pyannote 4.0+), prefer ``exclusive_speaker_diarization``
    which assigns exactly one speaker per time-step — ideal for interviews.
    Falls back to ``speaker_diarization`` or raw Annotation for older models.
    """
    if isinstance(raw, tuple):
        raw = raw[0]

    # community-1 returns an object with .exclusive_speaker_diarization
    for attr in ('exclusive_speaker_diarization', 'speaker_diarization'):
        v = getattr(raw, attr, None)
        if v is not None and hasattr(v, 'itertracks'):
            log.debug('Using %s from pipeline output', attr)
            return v

    if isinstance(raw, dict):
        for key in ('exclusive_speaker_diarization', 'speaker_diarization',
                    'diarization', 'annotation'):
            v = raw.get(key)
            if v is not None and hasattr(v, 'itertracks'):
                return v

    return raw


def annotation_to_dataframe(annotation):
    """Convert pyannote Annotation to a DataFrame for whisperx.assign_word_speakers."""
    import pandas as pd
    rows = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        rows.append({
            'segment': turn,
            'label': None,
            'speaker': speaker,
            'start': turn.start,
            'end': turn.end,
        })
    return pd.DataFrame(rows)


def pyannote_audio_file_input(diarize_input):
    """Build pyannote AudioFile dict for ``Pipeline.__call__``.

    Always returns a ``{'waveform': Tensor, 'sample_rate': int}`` dict so
    pyannote never needs to decode audio itself (which would trigger
    torchcodec — broken on macOS).
    """
    import numpy as np
    import torch

    if isinstance(diarize_input, str):
        # File path — decode to waveform via ffmpeg so pyannote never
        # touches torchcodec (which fails on Apple Silicon).
        import struct
        import subprocess
        proc = subprocess.run(
            ['ffmpeg', '-y', '-i', diarize_input,
             '-ar', '16000', '-ac', '1',
             '-f', 's16le', 'pipe:1'],
            capture_output=True, check=True,
        )
        raw = proc.stdout
        n = len(raw) // 2
        samples = struct.unpack(f'<{n}h', raw)
        wf = (torch.tensor(samples, dtype=torch.float32)
              .unsqueeze(0) / 32768.0)
        return {'waveform': wf, 'sample_rate': 16000}
    arr = np.asarray(diarize_input, dtype=np.float32).flatten()
    wf = torch.from_numpy(arr).unsqueeze(0)
    return {'waveform': wf, 'sample_rate': 16000}


def normalize_diarize_audio_input(diarize_input):
    """Return numpy float32 mono waveform for DiarizationPipeline, or str path."""
    import numpy as np
    import torch

    if isinstance(diarize_input, dict) and 'waveform' in diarize_input:
        wf = diarize_input['waveform']
        if isinstance(wf, torch.Tensor):
            wf = wf.squeeze().detach().cpu().numpy()
        arr = np.asarray(wf, dtype=np.float32).flatten()
        return arr
    if isinstance(diarize_input, torch.Tensor):
        return diarize_input.squeeze().detach().cpu().numpy().astype(np.float32).flatten()
    if isinstance(diarize_input, np.ndarray):
        return np.asarray(diarize_input, dtype=np.float32).flatten()
    return diarize_input


def instantiate_whisperx_pipeline(model_name, hf_token, device):
    """Build DiarizationPipeline; WhisperX versions differ (token vs use_auth_token)."""
    import inspect

    import torch
    from whisperx.diarize import DiarizationPipeline

    if isinstance(device, str):
        device = torch.device(device)
    params = inspect.signature(DiarizationPipeline.__init__).parameters
    names = set(params)
    kwargs = {'device': device}
    if 'model_name' in names:
        kwargs['model_name'] = model_name
    elif 'model_config' in names:
        kwargs['model_config'] = model_name
    else:
        kwargs['model_name'] = model_name
    auth = hf_token or None
    if 'token' in names:
        kwargs['token'] = auth
    elif 'use_auth_token' in names:
        kwargs['use_auth_token'] = auth
    elif 'hf_token' in names:
        kwargs['hf_token'] = auth
    if 'cache_dir' in names:
        kwargs['cache_dir'] = None
    return DiarizationPipeline(**kwargs)


def merge_chunk_speakers(turns, chunk_starts, overlap_sec):
    """Merge speaker labels across chunk boundaries using overlap regions.

    In each overlap zone between chunk N and chunk N+1, speakers from
    chunk N+1 are mapped to chunk N speakers by maximum temporal overlap.
    The mapping is applied transitively across all chunks.
    """
    from collections import defaultdict

    step = chunk_starts[1] - chunk_starts[0] if len(chunk_starts) > 1 else 0
    if step <= 0:
        return turns

    # Global speaker rename map
    rename = {}

    for ci in range(len(chunk_starts) - 1):
        # Overlap zone: [chunk_starts[ci+1], chunk_starts[ci] + step + overlap_sec)
        overlap_start = chunk_starts[ci + 1]
        overlap_end = overlap_start + overlap_sec

        # Collect turns from chunk ci and ci+1 in the overlap zone
        prev_prefix = f'c{ci}_'
        next_prefix = f'c{ci + 1}_'

        prev_turns = [(s, e, spk) for s, e, spk in turns
                      if spk.startswith(prev_prefix)
                      and s < overlap_end and e > overlap_start]
        next_turns = [(s, e, spk) for s, e, spk in turns
                      if spk.startswith(next_prefix)
                      and s < overlap_end and e > overlap_start]

        if not prev_turns or not next_turns:
            continue

        # Compute temporal overlap between each pair of speakers
        overlap_matrix = defaultdict(float)
        for ns, ne, nspk in next_turns:
            for ps, pe, pspk in prev_turns:
                ol = min(ne, pe) - max(ns, ps)
                if ol > 0:
                    overlap_matrix[(nspk, pspk)] += ol

        # For each next-chunk speaker, find best match in prev-chunk
        next_speakers = {spk for _, _, spk in next_turns}
        for nspk in next_speakers:
            best_prev = None
            best_ol = 0
            for (ns, ps), ol in overlap_matrix.items():
                if ns == nspk and ol > best_ol:
                    best_ol = ol
                    # Resolve transitively: prev speaker may already be renamed
                    resolved = ps
                    while resolved in rename:
                        resolved = rename[resolved]
                    best_prev = resolved
            if best_prev:
                rename[nspk] = best_prev

    # Apply rename transitively
    def _resolve(spk):
        seen = set()
        while spk in rename and spk not in seen:
            seen.add(spk)
            spk = rename[spk]
        return spk

    return [(s, e, _resolve(spk)) for s, e, spk in turns]


def assign_speakers_simple(diarization, segments):
    """Assign speaker labels using pyannote ``Annotation`` (native path).

    ``diarization`` must support ``itertracks(yield_label=True)``.
    """
    from bisect import bisect_right
    from collections import Counter, defaultdict

    # Build a flat list of (start, end, speaker) from pyannote output
    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append((turn.start, turn.end, speaker))
    turns.sort(key=lambda t: t[0])

    # Re-cluster: absorb minor speakers into dominant temporal neighbors.
    # Pyannote on Mac/MPS can over-segment, assigning 4+ IDs to one person.
    speaker_time = defaultdict(float)
    for start, end, spk in turns:
        speaker_time[spk] += end - start
    if len(speaker_time) > 1:
        sorted_times = sorted(speaker_time.values(), reverse=True)
        threshold = sorted_times[1] * 0.25 if len(sorted_times) > 1 else 0
        major = {spk for spk, t in speaker_time.items() if t >= threshold}
        minor = {spk for spk, t in speaker_time.items() if t < threshold}
        if minor and major:
            remap = {}
            for spk in minor:
                neighbor_counts = Counter()
                for i, (_s, _e, sp) in enumerate(turns):
                    if sp != spk:
                        continue
                    if i > 0 and turns[i - 1][2] in major:
                        neighbor_counts[turns[i - 1][2]] += 1
                    if i < len(turns) - 1 and turns[i + 1][2] in major:
                        neighbor_counts[turns[i + 1][2]] += 1
                if neighbor_counts:
                    remap[spk] = neighbor_counts.most_common(1)[0][0]
            if remap:
                turns = [(s, e, remap.get(sp, sp)) for s, e, sp in turns]
                log.debug('Speaker re-cluster: merged %d minor speakers',
                          len(remap))

    # Merge same-speaker turns with small gaps and filter short noise
    merged = []
    for start, end, spk in turns:
        if end - start < 0.3:
            continue
        if merged and merged[-1][2] == spk and start - merged[-1][1] < 1.5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), spk)
        else:
            merged.append((start, end, spk))
    turns = merged

    turns_starts = [t[0] for t in turns]

    def _find_speaker(start, end):
        """Find the speaker with the most overlap for a given time range."""
        idx = bisect_right(turns_starts, start)
        best_speaker = ''
        best_overlap = 0.0
        for i in range(max(0, idx - 1), len(turns)):
            t_start, t_end, speaker = turns[i]
            if t_start >= end:
                break
            overlap = min(end, t_end) - max(start, t_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        return best_speaker

    for seg in segments:
        if 'words' in seg and seg['words']:
            # Assign speaker per word, then duration-weighted vote for segment
            speaker_durations = defaultdict(float)
            for w in seg['words']:
                w_spk = _find_speaker(w.get('start', 0), w.get('end', 0))
                w['speaker'] = w_spk
                if w_spk:
                    dur = w.get('end', 0) - w.get('start', 0)
                    speaker_durations[w_spk] += max(dur, 0)
            if speaker_durations:
                seg['speaker'] = max(speaker_durations,
                                     key=speaker_durations.get)
        else:
            seg['speaker'] = _find_speaker(
                seg.get('start', 0), seg.get('end', 0))

    return segments


class Diarizer:
    """Loads the pyannote pipeline once and labels speakers per recording."""

    def __init__(self, env=None, engine_kind='whisperx', device=None):
        self.env = env
        self.engine_kind = engine_kind    # 'whisperx' | 'mlx' (native loader)
        self.pipeline = None
        self.device = device

    # ── loading ──

    def load(self):
        if self.pipeline is not None:
            return
        compat.patch_torchaudio_for_pyannote()
        compat.patch_hf_hub_legacy_use_auth_token()
        import warnings
        warnings.filterwarnings('ignore', message='torchcodec')
        if sys.platform == 'darwin':
            compat.stub_torchcodec()
        if self.device is None:
            self.device = detect_diarize_device()

        # pyannote resolves sub-models with hf_hub_download(..., cache_dir=PYANNOTE_CACHE).
        # Must be set before whisperx/pyannote import so CACHE_DIR in pyannote is correct.
        os.makedirs(self.env.pyannote_cache, exist_ok=True)
        os.environ['PYANNOTE_CACHE'] = self.env.pyannote_cache

        diarize_dir = self.env.diarize_dir
        config_file = os.path.join(diarize_dir, 'config.yaml')

        log.info('Loading diarization pipeline from %s ...', diarize_dir)

        import torch

        if self.engine_kind == 'mlx':
            self._load_native(diarize_dir, config_file,
                              self.env.hf_token, torch.device(self.device))
        else:
            model_name = (config_file if os.path.isfile(config_file)
                          else DEFAULT_DIARIZATION_MODEL)

            def _make(device):
                return instantiate_whisperx_pipeline(
                    model_name, self.env.hf_token, device)

            try:
                self.pipeline = _make(torch.device(self.device))
                log.info('Diarization pipeline on %s', self.device)
            except Exception as exc:
                if self.device != 'cpu':
                    log.warning('Diarization init on %s failed (%s) — retrying on CPU',
                                self.device, exc)
                    self.device = 'cpu'
                    self.pipeline = _make(torch.device('cpu'))
                else:
                    raise
        log.info('Diarization pipeline loaded.')

    def _load_native(self, diarize_dir, config_file, hf_token, device):
        """Apple Silicon (MLX): pyannote ``Pipeline`` only — no WhisperX diarization import."""
        import inspect

        import torch
        from pyannote.audio import Pipeline

        pretrained = (diarize_dir if os.path.isfile(config_file)
                      else DEFAULT_DIARIZATION_MODEL)
        sig = inspect.signature(Pipeline.from_pretrained)
        kwargs = {}
        auth = hf_token or None
        if 'token' in sig.parameters:
            kwargs['token'] = auth
        elif 'use_auth_token' in sig.parameters:
            kwargs['use_auth_token'] = auth

        # How far the segmentation window slides, IN SECONDS. Two knobs in
        # pyannote are called some version of "step" and they are not the same
        # one: the pipeline's ``segmentation_step`` is a *fraction* of the window
        # and defaults to 0.1, while ``Inference.step`` — the attribute set below
        # — is seconds, and the pipeline builds it as 0.1 x 10s window = 1.0s.
        # This used to be set to 0.2 reading the fraction's scale, which is not
        # 2x coarser than the default but 5x finer: ~551 windows on 120s of audio
        # against 111, through both the segmentation and the embedding pass.
        # Measured on one 38-minute recording, MPS: 441.6s at 0.2, ~90s at 1.0,
        # ~55s at 2.0. Finer was not more accurate either — 0.2 found four
        # speakers in a two-person interview and left one of them unvoted-for and
        # missing from the transcript, where 1.0 and 2.0 both found two.
        #
        # 1.0 is pyannote's own default and what the model was evaluated at. 2.0
        # is available and roughly what the old comment meant to ask for, but it
        # is past the tested setting, so it stays opt-in.
        diarize_step = float(os.environ.get('PINE_DIARIZE_STEP', '1.0'))

        try:
            pipeline = Pipeline.from_pretrained(pretrained, **kwargs)
            # Set on the Inference object, not on pipeline.segmentation_step:
            # that one is read at construction time and assigning it here would
            # be silently ignored.
            if hasattr(pipeline, '_segmentation') and hasattr(pipeline._segmentation, 'step'):
                pipeline._segmentation.step = diarize_step
                log.info('Diarization segmentation step set to %.2fs', diarize_step)
            pipeline.to(device)
            # Smoke-test MPS with a tiny tensor to fail fast at load time
            if str(device) == 'mps':
                _dummy = {'waveform': torch.zeros(1, 32000), 'sample_rate': 16000}
                try:
                    pipeline(_dummy)
                    log.info('MPS smoke-test passed')
                except Exception as mps_exc:
                    log.warning('MPS smoke-test failed (%s) — will fall back to CPU', mps_exc)
                    raise
            self.pipeline = pipeline
            log.info('Native pyannote diarization on %s', self.device)
        except Exception as exc:
            if self.device != 'cpu':
                log.warning('Native pyannote init on %s failed (%s) — retrying on CPU',
                            self.device, exc)
                self.device = 'cpu'
                pipeline = Pipeline.from_pretrained(pretrained, **kwargs)
                pipeline.to(torch.device('cpu'))
                self.pipeline = pipeline
            else:
                raise

    # ── running ──

    def run(self, diarize_input, segments, recording_id,
            audio_path=None, total_duration=0, num_speakers=None,
            on_status=_noop_status):
        """Diarize and label ``segments`` in one pass. Returns the segments."""
        result = self.compute(
            diarize_input, recording_id, audio_path=audio_path,
            total_duration=total_duration, num_speakers=num_speakers,
            on_status=on_status)
        return self.assign(result, segments)

    def compute(self, diarize_input, recording_id, audio_path=None,
                total_duration=0, num_speakers=None,
                on_status=_noop_status, check_cancel=_noop_check_cancel):
        """Work out who spoke when — the half of the job that needs only audio.

        Kept separate from ``assign`` so the pipeline can start diarizing while
        the engine is still transcribing: pyannote never reads the transcript,
        only the speaker *assignment* does. Returns an opaque handle for
        ``assign``, or None when diarization produced nothing usable.
        """
        if self.engine_kind == 'mlx':
            return self._compute_native(
                diarize_input, audio_path=audio_path,
                total_duration=total_duration, num_speakers=num_speakers,
                on_status=on_status, check_cancel=check_cancel)
        return self._compute_whisperx(
            diarize_input, num_speakers=num_speakers, on_status=on_status)

    def assign(self, result, segments):
        """Attach the speaker labels from ``compute`` to transcript segments."""
        if result is None:
            return segments

        t0 = time.monotonic()
        if self.engine_kind == 'mlx':
            segments = assign_speakers_simple(result, segments)
            log.info('PERF: speaker assignment (native) took %.1fs',
                     time.monotonic() - t0)
            return segments

        import whisperx
        fill_nearest = os.environ.get('PINE_DIARIZE_FILL_NEAREST', '').strip() == '1'
        transcript_result = whisperx.assign_word_speakers(
            result,
            {'segments': segments},
            fill_nearest=fill_nearest,
        )
        segments = transcript_result.get('segments', segments)
        log.info('PERF: speaker assignment took %.1fs', time.monotonic() - t0)
        return segments

    def _compute_whisperx(self, diarize_input, num_speakers=None,
                          on_status=_noop_status):
        """Windows/Linux: whisperx's DiarizationPipeline, as a DataFrame."""
        import pandas as pd

        t0 = time.monotonic()
        diarize_input = normalize_diarize_audio_input(diarize_input)
        log.info('PERF: diarize audio prep took %.1fs', time.monotonic() - t0)
        _spk_kwargs = speaker_kwargs(num_speakers)

        t1 = time.monotonic()
        try:
            diarize_segments = self.pipeline(diarize_input, **_spk_kwargs)
        except Exception as exc:
            if self.device != 'cpu':
                import torch
                log.warning(
                    'Diarization on %s failed: %s — retrying on CPU',
                    self.device, exc)
                on_status(stage='diarizing',
                          message='Speaker ID failed on GPU, retrying on CPU…')
                self.pipeline.model.to(torch.device('cpu'))
                self.device = 'cpu'
                diarize_segments = self.pipeline(diarize_input, **_spk_kwargs)
            else:
                raise
        log.info('PERF: diarize pipeline (%s) took %.1fs',
                 self.device, time.monotonic() - t1)

        if sys.platform == 'darwin':
            compat.clear_torchcodec_decode_cache()

        if isinstance(diarize_segments, tuple):
            diarize_segments = diarize_segments[0]

        for _attr in ('exclusive_speaker_diarization', 'speaker_diarization'):
            _v = getattr(diarize_segments, _attr, None)
            if _v is not None:
                diarize_segments = annotation_to_dataframe(_v)
                break
        else:
            if hasattr(diarize_segments, 'itertracks') and not isinstance(
                    diarize_segments, pd.DataFrame):
                diarize_segments = annotation_to_dataframe(diarize_segments)

        return diarize_segments

    def _compute_native(self, diarize_input, audio_path=None, total_duration=0,
                        num_speakers=None, on_status=_noop_status,
                        check_cancel=_noop_check_cancel):
        """Apple Silicon: pyannote diarization on MPS/CPU, as an Annotation.

        Uses ``exclusive_speaker_diarization`` (one speaker per time-step)
        for reliable alignment with Whisper segments in interview audio.
        Very long audio falls back to chunking to stay inside memory — see
        DIARIZE_CHUNK_THRESHOLD_SEC for why that is a last resort, not a
        speed optimisation.
        """
        import torch

        if (total_duration >= DIARIZE_CHUNK_THRESHOLD_SEC
                and audio_path and os.path.isfile(audio_path)):
            log.info('Using chunked diarization (%.0f min, threshold %ds) — '
                     'speaker labels may drift across chunk boundaries',
                     total_duration / 60, DIARIZE_CHUNK_THRESHOLD_SEC)
            return self._compute_chunked(
                audio_path, total_duration, num_speakers=num_speakers,
                on_status=on_status, check_cancel=check_cancel)

        t0 = time.monotonic()
        diarize_input = normalize_diarize_audio_input(diarize_input)
        py_in = pyannote_audio_file_input(diarize_input)
        log.info('PERF: diarize audio prep took %.1fs', time.monotonic() - t0)

        _spk_kwargs = speaker_kwargs(num_speakers)
        _spk_kwargs.update(pipeline_hook_kwargs(self.pipeline, check_cancel))

        t1 = time.monotonic()
        try:
            diar_raw = self.pipeline(py_in, **_spk_kwargs)
        except TranscriptionCancelled:
            raise
        except Exception as exc:
            if self.device != 'cpu':
                log.warning(
                    'Diarization on %s failed: %s — retrying on CPU',
                    self.device, exc)
                on_status(stage='diarizing',
                          message='Speaker ID failed on GPU, retrying on CPU…')
                self.pipeline.to(torch.device('cpu'))
                self.device = 'cpu'
                diar_raw = self.pipeline(py_in, **_spk_kwargs)
            else:
                raise
        log.info('PERF: diarize pipeline (%s) took %.1fs',
                 self.device, time.monotonic() - t1)

        if self.device == 'mps':
            torch.mps.empty_cache()

        if sys.platform == 'darwin':
            compat.clear_torchcodec_decode_cache()

        annotation = unwrap_annotation(diar_raw)
        if not hasattr(annotation, 'itertracks'):
            log.warning(
                'Unexpected diarization type %s — skipping speaker labels',
                type(annotation).__name__)
            return None
        return annotation

    def _compute_chunked(self, audio_path, total_duration, num_speakers=None,
                         on_status=_noop_status, check_cancel=_noop_check_cancel):
        """Chunked diarization, for files too long to hold in memory at once.

        Splits audio into DIARIZE_CHUNK_SIZE_SEC chunks with overlap, runs
        pyannote on each, then merges speaker labels across chunk boundaries
        using the overlap regions. That merge is the weak point — it matches
        speakers by temporal overlap, so a chunk seam where only one person
        speaks can swap two identities for the rest of the file. Prefer
        whole-file diarization wherever it fits.
        """
        import torch

        _spk_kwargs = speaker_kwargs(num_speakers)
        _spk_kwargs.update(pipeline_hook_kwargs(self.pipeline, check_cancel))

        step = DIARIZE_CHUNK_SIZE_SEC - DIARIZE_CHUNK_OVERLAP_SEC
        chunk_starts = []
        offset = 0
        while offset < total_duration:
            chunk_starts.append(offset)
            offset += step
        total_chunks = len(chunk_starts)

        all_turns = []   # list of (global_start, global_end, chunk_speaker_label)
        t_total = time.monotonic()

        for i, offset in enumerate(chunk_starts):
            check_cancel()
            duration = min(DIARIZE_CHUNK_SIZE_SEC, total_duration - offset)
            log.info('Diarize chunk %d/%d  offset=%.0fs  duration=%.0fs',
                     i + 1, total_chunks, offset, duration)
            pct = round(i / total_chunks * 100)
            on_status(stage='diarizing',
                      message=f'Speaker ID chunk {i + 1}/{total_chunks} ({pct}%)…',
                      percent=pct)

            chunk_audio = load_audio_range(audio_path, offset, duration)
            py_in = pyannote_audio_file_input(chunk_audio)

            t1 = time.monotonic()
            try:
                diar_raw = self.pipeline(py_in, **_spk_kwargs)
            except TranscriptionCancelled:
                raise
            except Exception as exc:
                if self.device != 'cpu':
                    log.warning('Diarize chunk %d on %s failed: %s — retrying CPU',
                                i + 1, self.device, exc)
                    self.pipeline.to(torch.device('cpu'))
                    self.device = 'cpu'
                    diar_raw = self.pipeline(py_in, **_spk_kwargs)
                else:
                    raise
            log.info('PERF: diarize chunk %d/%d (%s) took %.1fs',
                     i + 1, total_chunks, self.device,
                     time.monotonic() - t1)

            if self.device == 'mps':
                torch.mps.empty_cache()

            annotation = unwrap_annotation(diar_raw)
            if hasattr(annotation, 'itertracks'):
                for turn, _, speaker in annotation.itertracks(yield_label=True):
                    global_start = turn.start + offset
                    global_end = turn.end + offset
                    all_turns.append((global_start, global_end,
                                      f'c{i}_{speaker}'))

        log.info('PERF: chunked diarization total took %.1fs',
                 time.monotonic() - t_total)

        if not all_turns:
            log.warning('Chunked diarization produced no turns — skipping speaker labels')
            return None

        # Merge speaker identities across chunk boundaries
        all_turns = merge_chunk_speakers(
            all_turns, chunk_starts, DIARIZE_CHUNK_OVERLAP_SEC)

        # Build a pyannote-like Annotation for assign_speakers_simple
        from pyannote.core import Annotation, Segment
        merged_annotation = Annotation()
        for start, end, spk in all_turns:
            merged_annotation[Segment(start, end)] = spk
        return merged_annotation
