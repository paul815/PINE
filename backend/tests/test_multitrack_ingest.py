"""Tests for recognising and registering multi-track material."""

import os

import pytest

from app.services.multitrack_ingest import (
    detect_zoom_folder,
    meeting_name_from_folder,
    speaker_name_from_filename,
)

# ── the participant name Zoom left in the filename ──

@pytest.mark.parametrize('filename, expected', [
    # Layouts Zoom has used across versions.
    ('audio1234567890_Ivan Petrov.m4a', 'Ivan Petrov'),
    ('Ivan Petrov_1234567890.m4a', 'Ivan Petrov'),
    # The id after the prefix is stripped whatever its length.
    ('audio100_Ivan Petrov.m4a', 'Ivan Petrov'),
    ('audio99 Lena.m4a', 'Lena'),
    ('audio_only_Maria.m4a', 'Maria'),
    ('audioonly_Maria.m4a', 'Maria'),
    # No separator at all: the capital is what starts the name.
    ('audioPavelK.m4a', 'PavelK'),
    ('audioНаташа.m4a', 'Наташа'),
    ('Ivan_Petrov.m4a', 'Ivan Petrov'),
    ('2026-08-07 10.00.00 Ivan.m4a', 'Ivan'),
    # Nothing exotic to strip.
    ('Мария Иванова.m4a', 'Мария Иванова'),
    ('Anne-Marie O\'Brien.m4a', 'Anne-Marie O\'Brien'),
    # Nothing recognisable left — the caller falls back to a positional label.
    ('audio1234567890.m4a', ''),
    ('1234567890.m4a', ''),
    ('', ''),
])
def test_speaker_name_from_filename(filename, expected):
    assert speaker_name_from_filename(filename) == expected


def test_a_name_starting_with_audio_is_not_truncated():
    """"Audiophile" must not become "phile" — a lower-case letter is no boundary.

    The prefix only goes when a separator, a digit or a capital follows it.
    """
    assert speaker_name_from_filename('Audiophile.m4a') == 'Audiophile'
    assert speaker_name_from_filename('Audiard.m4a') == 'Audiard'


def test_full_path_is_accepted():
    assert speaker_name_from_filename(
        os.path.join('C', 'Zoom', 'Audio Record', 'audio99_Lena.m4a')) == 'Lena'


# ── what the recording ends up called ──

@pytest.mark.parametrize('parts, expected', [
    (('Zoom', '2026-08-07 10.00.00 Interview'), '2026-08-07 10.00.00 Interview'),
    # Pointed at the folder the tracks lie in — the meeting above it names this.
    (('Zoom', 'Interview', 'Audio Record'), 'Interview'),
    (('Zoom', 'Interview', 'audio_record'), 'Interview'),
    (('Zoom', 'Interview', 'audio1234567890'), 'Interview'),
    # A meeting whose own name merely begins with those letters is left alone.
    (('Zoom', 'Audiard interview'), 'Audiard interview'),
    # Including a capital straight after — that only names a track, not a folder.
    (('Zoom', 'AudioSync weekly'), 'AudioSync weekly'),
])
def test_meeting_name_from_folder(parts, expected):
    assert meeting_name_from_folder(os.path.join(*parts)) == expected


def test_meeting_name_ignores_a_trailing_separator():
    assert meeting_name_from_folder(
        os.path.join('Zoom', 'Interview') + os.sep) == 'Interview'


def test_meeting_name_falls_back_when_nothing_is_left():
    """Never "audio…", even with no meeting folder above to borrow from."""
    assert meeting_name_from_folder('Audio Record') == 'Zoom meeting'
    assert meeting_name_from_folder('') == 'Zoom meeting'


# ── the Zoom meeting folder ──

def _zoom_folder(tmp_path, track_names, extras=()):
    meeting = tmp_path / '2026-08-07 10.00.00 Interview'
    audio = meeting / 'Audio Record'
    audio.mkdir(parents=True)
    for name in track_names:
        (audio / name).write_bytes(b'\x00' * 64)
    for name, size in extras:
        (meeting / name).write_bytes(b'\x00' * size)
    return str(meeting)


def test_detects_tracks_and_names(tmp_path):
    folder = _zoom_folder(
        tmp_path, ['audio100_Ivan Petrov.m4a', 'audio101_Maria.m4a'])

    found = detect_zoom_folder(folder)

    assert [t['speaker_name'] for t in found['tracks']] == ['Ivan Petrov', 'Maria']
    assert all(os.path.isfile(t['path']) for t in found['tracks'])


def test_prefers_the_zoom_video_for_playback(tmp_path):
    folder = _zoom_folder(
        tmp_path, ['audio100_A.m4a', 'audio101_B.m4a'],
        extras=[('audio1234.m4a', 200), ('video1234.mp4', 900)])

    found = detect_zoom_folder(folder)

    assert os.path.basename(found['media']) == 'video1234.mp4'


def test_falls_back_to_the_mixed_audio(tmp_path):
    folder = _zoom_folder(
        tmp_path, ['audio100_A.m4a', 'audio101_B.m4a'],
        extras=[('audio1234.m4a', 200)])

    found = detect_zoom_folder(folder)

    assert os.path.basename(found['media']) == 'audio1234.m4a'


def test_no_playable_file_reports_none(tmp_path):
    """The caller has to build a mixdown; it must not silently pick a track."""
    folder = _zoom_folder(tmp_path, ['audio100_A.m4a', 'audio101_B.m4a'])

    assert detect_zoom_folder(folder)['media'] is None


def test_folder_without_a_track_dir_is_not_multitrack(tmp_path):
    plain = tmp_path / 'plain'
    plain.mkdir()
    (plain / 'interview.mp4').write_bytes(b'\x00' * 64)

    assert detect_zoom_folder(str(plain)) is None


def test_single_participant_is_not_multitrack(tmp_path):
    """One track is just a recording — it should take the ordinary path."""
    folder = _zoom_folder(tmp_path, ['audio100_Alone.m4a'])

    assert detect_zoom_folder(folder) is None


def test_missing_folder_is_not_multitrack(tmp_path):
    assert detect_zoom_folder(str(tmp_path / 'nope')) is None


# ── the API ──

def test_inspect_reports_what_was_found(client, tmp_path):
    folder = _zoom_folder(
        tmp_path, ['audio100_Ivan.m4a', 'audio101_Maria.m4a'],
        extras=[('video1.mp4', 900)])

    resp = client.post('/api/utils/inspect-multitrack', json={'folder': folder})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['kind'] == 'zoom_folder'
    assert [t['speaker_name'] for t in body['tracks']] == ['Ivan', 'Maria']


def test_inspect_explains_an_ordinary_folder(client, tmp_path):
    plain = tmp_path / 'plain'
    plain.mkdir()

    resp = client.post('/api/utils/inspect-multitrack',
                       json={'folder': str(plain)})

    assert resp.status_code == 200
    assert resp.get_json()['kind'] is None
    assert resp.get_json()['error']


def test_inspect_requires_an_argument(client):
    assert client.post('/api/utils/inspect-multitrack', json={}).status_code == 400


def _project(client):
    resp = client.post('/api/projects', json={'name': 'Interviews'})
    return resp.get_json()['id']


def test_registering_a_zoom_folder_creates_tracks(client, app, tmp_path):
    project_id = _project(client)
    folder = _zoom_folder(
        tmp_path, ['audio100_Ivan.m4a', 'audio101_Maria.m4a'],
        extras=[('video1.mp4', 900)])

    resp = client.post(f'/api/projects/{project_id}/recordings/multitrack',
                       json={'folder': folder})

    assert resp.status_code == 201
    rec = resp.get_json()
    assert rec['source_kind'] == 'multitrack'
    assert rec['track_count'] == 2
    assert rec['num_speakers'] == 2
    assert rec['is_linked'] is True
    assert rec['file_format'] == 'mp4', 'the Zoom video should be what plays'
    assert rec['original_name'] == '2026-08-07 10.00.00 Interview'

    with app.app_context():
        from app.models.recording_track import RecordingTrack
        tracks = RecordingTrack.query.filter_by(
            recording_id=rec['id']).order_by(RecordingTrack.track_index).all()
        assert [t.speaker_name for t in tracks] == ['Ivan', 'Maria']
        assert all(os.path.isabs(t.source_path) for t in tracks)
        assert all(t.channel_index is None for t in tracks)


def test_registering_an_ordinary_folder_is_rejected(client, tmp_path):
    project_id = _project(client)
    plain = tmp_path / 'plain'
    plain.mkdir()

    resp = client.post(f'/api/projects/{project_id}/recordings/multitrack',
                       json={'folder': str(plain)})

    assert resp.status_code == 400


def test_deleting_the_recording_takes_its_tracks(client, app, tmp_path):
    project_id = _project(client)
    folder = _zoom_folder(
        tmp_path, ['audio100_Ivan.m4a', 'audio101_Maria.m4a'],
        extras=[('video1.mp4', 900)])
    rec = client.post(f'/api/projects/{project_id}/recordings/multitrack',
                      json={'folder': folder}).get_json()

    client.delete(f'/api/projects/{project_id}/recordings/{rec["id"]}')

    with app.app_context():
        from app.models.recording_track import RecordingTrack
        assert RecordingTrack.query.filter_by(recording_id=rec['id']).count() == 0


def test_ordinary_upload_is_still_single_track(client):
    """The pyannote path has to stay exactly as it was."""
    import io

    project_id = _project(client)
    resp = client.post(
        f'/api/projects/{project_id}/recordings',
        content_type='multipart/form-data',
        data={'file': (io.BytesIO(b'\x00' * 100), 'interview.mp3')})

    rec = resp.get_json()
    assert rec['source_kind'] == 'single'
    assert rec['track_count'] == 0
