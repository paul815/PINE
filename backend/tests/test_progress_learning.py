"""The ETA learns this machine's pace from the jobs it finishes.

The shipped cost model is a starting guess and was measured to be roughly twice
as optimistic as at least one real machine. These cover the loop that corrects
it: read the stored pace before a job, fold the measured one back in after.
"""

import json
import os

import pytest

from app.services.transcription.job_runner import (
    _learn_progress_scale,
    _progress_scale_key,
    _stored_progress_scale,
)

MODEL = 'whisperx-large-v3'


def test_unmeasured_machine_starts_on_the_shipped_plan(app):
    with app.app_context():
        assert _stored_progress_scale(MODEL, False) == 1.0


def test_the_first_measurement_is_taken_whole(app):
    """Averaging it against a 1.0 nobody observed would halve a real finding."""
    with app.app_context():
        _learn_progress_scale(MODEL, False, 2.0)

        assert _stored_progress_scale(MODEL, False) == pytest.approx(2.0)


def test_later_measurements_are_smoothed(app):
    with app.app_context():
        _learn_progress_scale(MODEL, False, 2.0)
        _learn_progress_scale(MODEL, False, 3.0)

        # Moves toward the new figure without jumping to it.
        scale = _stored_progress_scale(MODEL, False)
        assert 2.0 < scale < 3.0


def test_it_converges_on_a_steady_machine(app):
    with app.app_context():
        for _ in range(8):
            _learn_progress_scale(MODEL, False, 2.5)

        assert _stored_progress_scale(MODEL, False) == pytest.approx(2.5, rel=0.05)


def test_single_and_multitrack_are_learned_apart(app):
    """Multi-track transcribes only the speech it found; different job entirely."""
    with app.app_context():
        _learn_progress_scale(MODEL, False, 2.0)
        _learn_progress_scale(MODEL, True, 4.0)

        assert _stored_progress_scale(MODEL, False) == pytest.approx(2.0)
        assert _stored_progress_scale(MODEL, True) == pytest.approx(4.0)


def test_models_are_learned_apart(app):
    with app.app_context():
        _learn_progress_scale(MODEL, False, 2.0)
        _learn_progress_scale('mlx-whisper-small', False, 0.5)

        assert _stored_progress_scale(MODEL, False) == pytest.approx(2.0)
        assert _stored_progress_scale('mlx-whisper-small', False) == pytest.approx(0.5)


def test_keys_are_distinct_per_model_and_mode():
    keys = {
        _progress_scale_key(MODEL, False),
        _progress_scale_key(MODEL, True),
        _progress_scale_key('other', False),
    }
    assert len(keys) == 3


def test_a_corrupt_stored_value_falls_back(app):
    from app.models.setting import Setting

    with app.app_context():
        Setting.set(_progress_scale_key(MODEL, False), 'not a number')

        assert _stored_progress_scale(MODEL, False) == 1.0


# ── the measured figure must not reach the transcript file ──

def _payload(recording_id, **extra):
    return dict({
        'recording_id': recording_id,
        'language': 'ru',
        'duration_seconds': 4468.0,
        'speakers': {'SPEAKER_00': 'Moderator'},
        'segments': [{'start': 0.0, 'end': 1.0, 'text': 'Hi',
                      'speaker': 'Moderator'}],
    }, **extra)


def test_finalize_keeps_the_measurement_out_of_the_transcript(
        app, project_with_recording):
    from app.extensions import db
    from app.models.recording import Recording
    from app.services.transcription.job_runner import _finalize
    from ml_worker.pipeline import PROGRESS_SCALE_KEY

    _pid, rid, project_dir = project_with_recording(status='pending')

    _finalize(app, rid, _payload(rid, **{PROGRESS_SCALE_KEY: 2.4}))

    with app.app_context():
        row = db.session.get(Recording, rid)
        with open(os.path.join(project_dir, row.transcript_path),
                  encoding='utf-8') as fh:
            saved = json.load(fh)

    assert PROGRESS_SCALE_KEY not in saved, 'internal metric leaked into the transcript'
    assert saved['segments'][0]['text'] == 'Hi'
    assert saved['language'] == 'ru'


def test_finalize_learns_from_the_measurement(app, project_with_recording):
    from app.services.transcription.job_runner import _finalize
    from ml_worker.pipeline import PROGRESS_SCALE_KEY

    _pid, rid, _dir = project_with_recording(status='pending')
    with app.app_context():
        before = _stored_progress_scale(_current_model(app), False)

    _finalize(app, rid, _payload(rid, **{PROGRESS_SCALE_KEY: 2.4}))

    with app.app_context():
        after = _stored_progress_scale(_current_model(app), False)

    assert before == 1.0
    assert after == pytest.approx(2.4)


def test_finalize_without_a_measurement_changes_nothing(
        app, project_with_recording):
    """A run too short to measure must not reset what was already learned."""
    from app.services.transcription.job_runner import _finalize

    _pid, rid, _dir = project_with_recording(status='pending')
    with app.app_context():
        _learn_progress_scale(_current_model(app), False, 2.0)

    _finalize(app, rid, _payload(rid))

    with app.app_context():
        assert _stored_progress_scale(_current_model(app), False) == pytest.approx(2.0)


def _current_model(app):
    from app.models.setting import Setting
    from app.services.model_manager import (
        get_default_stt_model,
        normalize_stt_model_id,
    )
    return normalize_stt_model_id(
        Setting.get('stt_model_id', get_default_stt_model()))
