from datetime import datetime, UTC

from ..extensions import db


class Recording(db.Model):
    __tablename__ = 'recordings'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    original_name = db.Column(db.String(500), nullable=False)
    stored_name = db.Column(db.String(500), nullable=False)
    file_format = db.Column(db.String(10), default='')
    file_size_bytes = db.Column(db.BigInteger, default=0)
    duration_seconds = db.Column(db.Float, default=0)
    transcription_status = db.Column(db.String(20), default='pending', index=True)
    language = db.Column(db.String(10), default='')
    transcript_path = db.Column(db.Text, default='')
    error_message = db.Column(db.Text, default='')
    segment_id = db.Column(db.Integer, db.ForeignKey('segments.id'), nullable=True)
    participant_notes = db.Column(db.Text, default='')
    num_speakers = db.Column(db.Integer, nullable=True)
    is_linked = db.Column(db.Boolean, default=False)
    # 'single' — one file, speakers told apart by pyannote. 'multitrack' — every
    # speaker has their own track (see RecordingTrack), so there is nothing to
    # infer and diarization is skipped entirely.
    source_kind = db.Column(db.String(20), default='single')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    tracks = db.relationship(
        'RecordingTrack', backref='recording', lazy='select',
        cascade='all, delete-orphan',
        order_by='RecordingTrack.track_index',
    )

    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'original_name': self.original_name,
            'stored_name': self.stored_name,
            'file_format': self.file_format,
            'file_size_bytes': self.file_size_bytes,
            'duration_seconds': self.duration_seconds,
            'transcription_status': self.transcription_status,
            'language': self.language,
            'transcript_path': self.transcript_path,
            'error_message': self.error_message,
            'segment_id': self.segment_id,
            'participant_notes': self.participant_notes or '',
            'num_speakers': self.num_speakers,
            'is_linked': bool(self.is_linked),
            'source_kind': self.source_kind or 'single',
            'track_count': len(self.tracks or []),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
