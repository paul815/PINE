"""Runs one transcription job: DB → JobEnv/JobRequest → ML worker → DB/files.

The ML pipeline executes in the worker subprocess by default. Set
``PINE_ML_INPROCESS=1`` to run it inside the backend process instead
(debug escape hatch; same pipeline code either way).
"""

import logging
import os
import sys
import threading

from ml_worker.engines import ENGINE_WHISPERX, engine_kind_for_model
from ml_worker.errors import TranscriptionCancelled

from ...extensions import db
from ...models.ml_model import MLModel
from ...models.project import Project
from ...models.recording import Recording
from ...models.setting import Setting
from ..file_utils import atomic_write_json
from . import (
    _build_transcript_filename,
    _check_cancel,
    _emit_status,
    _language_waiters,
    _language_waiters_lock,
    _touch_heartbeat,
)

log = logging.getLogger(__name__)

# Cached pipeline for PINE_ML_INPROCESS=1 (models stay loaded between jobs).
_inprocess_pipeline = None


def _inprocess_mode():
    return os.environ.get('PINE_ML_INPROCESS', '').strip() == '1'


# ── learned pace (how far off the shipped cost model this machine runs) ──

def _progress_scale_key(stt_model_id, multitrack):
    """Settings key for one machine/model/mode combination.

    Keyed on the model because a large model and a small one are not the same
    job, and on the mode because a multi-track recording transcribes only the
    speech it found while a single file goes through end to end — measuring
    them together would average two unrelated paces.
    """
    return f'progress_scale:{stt_model_id}:{"multi" if multitrack else "single"}'


def _stored_progress_scale(stt_model_id, multitrack):
    """Last learned pace for this combination; 1.0 until something is measured."""
    raw = Setting.get(_progress_scale_key(stt_model_id, multitrack), '')
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def _learn_progress_scale(stt_model_id, multitrack, measured):
    """Fold a finished job's measured pace into the stored one."""
    from ml_worker.constants import PROGRESS_SCALE_SMOOTHING

    key = _progress_scale_key(stt_model_id, multitrack)
    previous = _stored_progress_scale(stt_model_id, multitrack)
    raw = Setting.get(key, '')
    if not raw:
        # Nothing learned yet: take the first measurement whole rather than
        # averaging it against a 1.0 that was never observed.
        updated = measured
    else:
        updated = (previous * (1.0 - PROGRESS_SCALE_SMOOTHING)
                   + measured * PROGRESS_SCALE_SMOOTHING)
    Setting.set(key, f'{updated:.4f}')
    log.info('Progress pace for %s: measured %.2fx, now using %.2fx',
             key, measured, updated)


# ── job resolution (all DB access happens here, before the worker starts) ──

def _resolve_job(app, recording_id):
    """Build (env_dict, job_dict) for the pipeline. Returns None if the recording is gone."""
    from ..model_manager import (
        get_default_stt_model,
        normalize_stt_model_id,
        pyannote_hub_cache_root,
    )

    skip_confirm = os.environ.get('PINE_SKIP_LANG_CONFIRM', '').strip() == '1'

    with app.app_context():
        recording = db.session.get(Recording, recording_id)
        if not recording:
            log.error('Recording %d not found', recording_id)
            return None
        project = db.session.get(Project, recording.project_id)
        if not project:
            log.error('Project for recording %d not found', recording_id)
            return None

        projects_path = Setting.get('projects_path', app.config['DEFAULT_PROJECTS_PATH'])
        project_dir = os.path.join(projects_path, project.folder_name)
        if recording.is_linked:
            audio_path = recording.stored_name
        else:
            audio_path = os.path.join(project_dir, recording.stored_name)

        # Per-speaker tracks, when this recording has them. Same convention as
        # stored_name: linked material keeps absolute paths, copied material is
        # relative to the project folder.
        tracks = []
        for track in recording.tracks or []:
            path = track.source_path
            if not os.path.isabs(path):
                path = os.path.join(project_dir, path)
            tracks.append({
                'index': track.track_index,
                'path': path,
                'speaker_name': track.speaker_name or '',
                'channel': track.channel_index,
            })

        forced_language = None
        if not skip_confirm:
            preset = (project.default_transcription_language or '').strip().lower()
            if preset:
                log.info('Using project %d default transcription language: %s',
                         project.id, preset)
                forced_language = preset

        raw_stt_model_id = Setting.get('stt_model_id', get_default_stt_model())
        stt_model_id = normalize_stt_model_id(raw_stt_model_id)
        if stt_model_id != raw_stt_model_id:
            Setting.set('stt_model_id', stt_model_id)

        models_path = Setting.get('models_path', app.config['DEFAULT_MODELS_PATH'])
        hf_token = Setting.get('hf_token', '')
        onboarding_complete = Setting.get('onboarding_complete') == 'true'

        diarize_dir = os.path.join(models_path, 'pyannote-diarization')
        row = db.session.get(MLModel, 'pyannote-diarization')
        cand = (row.path if row and row.path else '') or ''
        if cand and os.path.isfile(os.path.join(cand, 'config.yaml')):
            diarize_dir = cand

        num_speakers = recording.num_speakers
        progress_scale = _stored_progress_scale(stt_model_id, bool(tracks))

        recording.transcription_status = 'transcribing'
        db.session.commit()

    env = {
        'stt_model_id': stt_model_id,
        'model_dir': os.path.join(models_path, stt_model_id),
        'diarize_dir': diarize_dir,
        'pyannote_cache': pyannote_hub_cache_root(models_path),
        'hf_token': hf_token,
        'hf_offline': onboarding_complete,
        'progress_scale': progress_scale,
    }
    job = {
        'recording_id': recording_id,
        'audio_path': audio_path,
        'num_speakers': num_speakers,
        'forced_language': forced_language,
        'confirm_language': not skip_confirm,
        'tracks': tracks,
    }
    return env, job


def _play_awaiting_input_sound(app):
    """Play the completion sound to flag that a recording needs user input (e.g. language)."""
    with app.app_context():
        if Setting.get('transcription_complete_sound_enabled', 'true') != 'true':
            return
        try:
            volume = max(0, min(100, int(Setting.get('transcription_complete_sound_volume', '50'))))
        except ValueError:
            volume = 50
    if volume <= 0:
        return
    from ..model_manager import play_install_complete_sound
    play_install_complete_sound(app, volume_pct=volume)


# ── language confirmation (blocks the job thread, HTTP wakes it) ──

def _await_user_language(app, recording_id, suggested_lang, suggested_conf,
                         language_options):
    """Set status awaiting_language, emit SocketIO, block until user submits or cancel."""
    ev = threading.Event()
    state = {'event': ev, 'language': None, 'cancelled': False}
    with _language_waiters_lock:
        _language_waiters[recording_id] = state

    project_id = None
    with app.app_context():
        recording = db.session.get(Recording, recording_id)
        if recording:
            project_id = recording.project_id
            recording.transcription_status = 'awaiting_language'
            db.session.commit()

    msg = (
        'Language unclear — please choose the recording language to continue.'
        if suggested_lang
        else 'Please choose the recording language to continue.'
    )
    _emit_status(
        recording_id, 'awaiting_language', stage='language', message=msg,
        project_id=project_id,
        guessed_language=suggested_lang or '',
        language_confidence=suggested_conf if suggested_conf is not None else -1.0,
        language_options=language_options or [],
    )
    _play_awaiting_input_sound(app)

    while True:
        if ev.wait(timeout=30.0):
            break
        _touch_heartbeat()
        _check_cancel(recording_id)

    chosen = state.get('language')
    cancelled = state.get('cancelled')
    with _language_waiters_lock:
        _language_waiters.pop(recording_id, None)

    if cancelled:
        raise TranscriptionCancelled(recording_id)
    if not chosen:
        raise RuntimeError('Language confirmation ended without a language')

    with app.app_context():
        recording = db.session.get(Recording, recording_id)
        if recording:
            recording.transcription_status = 'transcribing'
            db.session.commit()

    return chosen.strip().lower()


# ── execution paths ──

def _run_via_worker(app, recording_id, env_dict, job_dict):
    """Dispatch the job to the worker subprocess and relay its events."""
    from .worker_client import get_client, kill_worker

    client = get_client()
    client.send({'op': 'job', 'env': env_dict, 'job': job_dict})

    while True:
        ev = client.read_event()
        if ev is None:
            code = client.exit_code()
            kill_worker()
            raise RuntimeError(
                f'ML worker exited unexpectedly (exit code {code}). '
                'It was likely killed by the OS (out of memory?) — '
                'see backend logs for [ml-worker] lines.')

        _touch_heartbeat()
        kind = ev.get('ev')
        ev_rid = ev.get('recording_id')
        # Ignore stragglers from a previously cancelled/failed job.
        if ev_rid is not None and ev_rid != recording_id:
            log.debug('Ignoring stale worker event %s for recording %s', kind, ev_rid)
            continue

        if kind == 'status':
            extra = {k: v for k, v in ev.items()
                     if k not in ('ev', 'recording_id', 'stage', 'message', 'percent')}
            _emit_status(recording_id, 'transcribing',
                         stage=ev.get('stage', ''),
                         message=ev.get('message', ''),
                         percent=ev.get('percent'),
                         **extra)
        elif kind == 'language_request':
            try:
                chosen = _await_user_language(
                    app, recording_id,
                    ev.get('guessed') or '',
                    ev.get('confidence'),
                    ev.get('options') or [])
            except TranscriptionCancelled:
                # cancel_transcription() already forwarded the cancel op;
                # keep reading — the worker will confirm with 'cancelled'.
                continue
            client.send({'op': 'language', 'recording_id': recording_id,
                         'code': chosen})
        elif kind == 'result':
            return ev.get('transcript') or {}
        elif kind == 'cancelled':
            raise TranscriptionCancelled(recording_id)
        elif kind == 'error':
            raise RuntimeError(ev.get('message') or 'Transcription failed in ML worker')
        # 'ready'/'pong' and unknown events: ignore.


def _run_inprocess(app, recording_id, env_dict, job_dict):
    """Debug path: run the same pipeline inside the backend process."""
    global _inprocess_pipeline
    from ml_worker.pipeline import JobEnv, JobRequest, MLPipeline, PipelineEvents

    if _inprocess_pipeline is None:
        _inprocess_pipeline = MLPipeline()

    def status(stage='', message='', percent=None, eta_secs=None, **extra):
        if eta_secs is not None:
            extra['eta_secs'] = eta_secs
        _emit_status(recording_id, 'transcribing', stage=stage,
                     message=message, percent=percent, **extra)

    def request_language(probe):
        return _await_user_language(
            app, recording_id, probe.code or '', probe.confidence,
            probe.options or [])

    events = PipelineEvents(
        status=status,
        request_language=request_language,
        check_cancel=lambda: _check_cancel(recording_id),
    )
    return _inprocess_pipeline.run(
        JobEnv(**env_dict), JobRequest(**job_dict), events)


# ── persistence ──

def _finalize(app, recording_id, transcript):
    """Write the transcript JSON, update the DB row, refresh the project README."""
    from ml_worker.pipeline import PROGRESS_SCALE_KEY

    # Rode along in the payload so both execution paths could carry it; it is
    # not part of the transcript, so take it out before anything is written.
    measured_scale = transcript.pop(PROGRESS_SCALE_KEY, None)
    detected_lang = transcript.get('language', 'en')

    play_tx_sound = False
    tx_sound_volume = 50
    with app.app_context():
        recording = db.session.get(Recording, recording_id)
        project = db.session.get(Project, recording.project_id)
        projects_path = Setting.get('projects_path', app.config['DEFAULT_PROJECTS_PATH'])
        project_dir = os.path.join(projects_path, project.folder_name)
        transcript_filename = _build_transcript_filename(recording)
        transcript_path = os.path.join(project_dir, transcript_filename)

        atomic_write_json(transcript_path, transcript)

        recording.transcription_status = 'transcribed'
        recording.language = detected_lang
        recording.transcript_path = transcript_filename
        recording.error_message = ''

        if measured_scale:
            from ..model_manager import get_default_stt_model, normalize_stt_model_id
            model_id = normalize_stt_model_id(
                Setting.get('stt_model_id', get_default_stt_model()))
            _learn_progress_scale(
                model_id, bool(recording.tracks), float(measured_scale))

        db.session.commit()

        from ...api.projects import _write_project_readme
        _write_project_readme(project)

        play_tx_sound = (
            Setting.get('transcription_complete_sound_enabled', 'true') == 'true'
        )
        try:
            tx_sound_volume = max(
                0,
                min(100, int(Setting.get('transcription_complete_sound_volume', '50'))),
            )
        except ValueError:
            tx_sound_volume = 50

    _emit_status(recording_id, 'transcribed', stage='done', message='Transcription complete')

    if play_tx_sound and tx_sound_volume > 0:
        from ..model_manager import play_install_complete_sound
        play_install_complete_sound(app, volume_pct=tx_sound_volume)

    log.info('Transcription complete for recording %d (%s)', recording_id, detected_lang)


# ── entry point (called from the queue worker thread) ──

def run_transcription_job(app, recording_id):
    """Run the full transcription flow for one recording."""
    from ..model_manager import (
        ensure_transcription_dependencies,
        repair_torch_companion_wheels_if_needed,
    )

    missing = ensure_transcription_dependencies()
    if missing:
        pip_pkgs = ' '.join(missing)
        raise RuntimeError(
            f"ML dependencies not installed: {', '.join(missing)}. "
            f"Run: {sys.executable} -m pip install {pip_pkgs} "
            "or open onboarding at http://pine.localhost:5000"
        )

    resolved = _resolve_job(app, recording_id)
    if resolved is None:
        return
    env_dict, job_dict = resolved

    if engine_kind_for_model(env_dict['stt_model_id']) == ENGINE_WHISPERX:
        if not repair_torch_companion_wheels_if_needed():
            raise RuntimeError(
                'torch and torchvision are from different wheel channels (e.g. +cpu vs +cu128). '
                'In the app venv run: python -m pip install --force-reinstall torchaudio torchvision '
                '--index-url https://download.pytorch.org/whl/cpu '
                '(or .../whl/cu128 if torch.__version__ shows +cu128).'
            )

    _emit_status(recording_id, 'transcribing', stage='loading')

    if _inprocess_mode():
        transcript = _run_inprocess(app, recording_id, env_dict, job_dict)
    else:
        transcript = _run_via_worker(app, recording_id, env_dict, job_dict)

    _finalize(app, recording_id, transcript)
