"""Unit tests for transcription service (pure logic only)."""

from types import SimpleNamespace
from unittest.mock import patch

from app.services.annotations import annotations_filename
from app.services.transcription import (
    SPEAKER_LABELS,
    _build_transcript_filename,
)
from ml_worker.pipeline import map_speakers


class TestMapSpeakers:
    """Tests for map_speakers (SPEAKER_XX → Moderator / Participant N)."""

    def test_empty_segments(self):
        mapping = map_speakers([])
        assert mapping == {}

    def test_single_speaker(self):
        segs = [{'start': 0, 'end': 1, 'text': 'Hi', 'speaker': 'SPEAKER_00'}]
        mapping = map_speakers(segs)
        assert mapping == {'SPEAKER_00': 'Moderator'}
        assert segs[0]['speaker'] == 'Moderator'

    def test_two_speakers(self):
        segs = [
            {'speaker': 'SPEAKER_00'},
            {'speaker': 'SPEAKER_01'},
        ]
        mapping = map_speakers(segs)
        assert mapping['SPEAKER_00'] == 'Moderator'
        assert mapping['SPEAKER_01'] == 'Participant 1'

    def test_more_than_six_speakers(self):
        segs = [{'speaker': f'SPEAKER_{i:02d}'} for i in range(8)]
        mapping = map_speakers(segs)
        assert mapping['SPEAKER_06'] == 'Speaker 7'
        assert mapping['SPEAKER_07'] == 'Speaker 8'

    def test_segment_without_speaker_unchanged(self):
        segs = [{'speaker': ''}, {'speaker': 'SPEAKER_00'}]
        map_speakers(segs)
        assert segs[0]['speaker'] == ''
        assert segs[1]['speaker'] == 'Moderator'


class TestSpeakerLabels:
    """Constants check."""

    def test_speaker_labels_order(self):
        assert SPEAKER_LABELS[0] == 'Moderator'
        assert 'Participant' in SPEAKER_LABELS[1]


class TestTranscriptFilenames:
    def test_same_source_name_gets_unique_transcript_files(self):
        rec1 = SimpleNamespace(id=101, stored_name='interview.mp3', transcript_path=None)
        rec2 = SimpleNamespace(id=102, stored_name='interview.mp3', transcript_path=None)

        assert _build_transcript_filename(rec1) == 'interview_101_transcript.json'
        assert _build_transcript_filename(rec2) == 'interview_102_transcript.json'

    def test_existing_transcript_path_is_reused(self):
        rec = SimpleNamespace(
            id=101,
            stored_name='interview.mp3',
            transcript_path='interview_101_transcript.json',
        )

        assert _build_transcript_filename(rec) == 'interview_101_transcript.json'


class TestAnnotationFilenames:
    def test_annotations_follow_transcript_name_when_available(self):
        assert annotations_filename('interview_101_transcript.json') == 'interview_101_annotations.json'

    def test_annotations_fall_back_to_media_name_for_pending_recordings(self):
        assert annotations_filename('interview.mp3') == 'interview_annotations.json'


class TestRequeueInterrupted:
    """Tests for requeue_interrupted."""

    def test_requeue_resets_stuck_recordings(self, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.services.transcription import requeue_interrupted

            db.create_all()
            proj = Project(name='RQ', folder_name='rq')
            db.session.add(proj)
            db.session.flush()
            rec1 = Recording(
                project_id=proj.id, original_name='a.mp3', stored_name='a.mp3',
                transcription_status='transcribing',
            )
            rec2 = Recording(
                project_id=proj.id, original_name='b.mp3', stored_name='b.mp3',
                transcription_status='awaiting_language',
            )
            rec3 = Recording(
                project_id=proj.id, original_name='c.mp3', stored_name='c.mp3',
                transcription_status='transcribed',
            )
            db.session.add_all([rec1, rec2, rec3])
            db.session.commit()
            r1id, r2id, r3id = rec1.id, rec2.id, rec3.id

        with patch('app.services.transcription.enqueue') as mock_enqueue:
            requeue_interrupted(app)

        with app.app_context():
            from app.models.recording import Recording as Rec
            assert Rec.query.get(r1id).transcription_status == 'pending'
            assert Rec.query.get(r2id).transcription_status == 'pending'
            assert Rec.query.get(r3id).transcription_status == 'transcribed'
            assert mock_enqueue.call_count == 2

    def test_requeue_no_stuck_recordings(self, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.services.transcription import requeue_interrupted

            db.create_all()
            proj = Project(name='NoStuck', folder_name='nostuck')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id, original_name='ok.mp3', stored_name='ok.mp3',
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()

        with patch('app.services.transcription.enqueue') as mock_enqueue:
            requeue_interrupted(app)
            assert mock_enqueue.call_count == 0
