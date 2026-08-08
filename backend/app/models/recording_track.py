from ..extensions import db


class RecordingTrack(db.Model):
    """One speaker's own audio inside a multi-track recording.

    Rows exist only for recordings with ``source_kind == 'multitrack'``: a Zoom
    folder that holds a file per participant, or one multi-channel file whose
    channels are the speakers. The recording itself still points at a single
    playable file (the Zoom video, or a mixdown) — these are what gets
    transcribed, one pass each, and they are never mixed together.
    """

    __tablename__ = 'recording_tracks'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    recording_id = db.Column(
        db.Integer, db.ForeignKey('recordings.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    track_index = db.Column(db.Integer, nullable=False, default=0)
    # Absolute path for linked material, filename inside the project folder for
    # copied material — the same convention Recording.stored_name follows.
    source_path = db.Column(db.String(500), nullable=False)
    speaker_name = db.Column(db.String(200), default='')
    # Set only when the track is a channel of source_path rather than a file of
    # its own; the decode step splits the channel out on the way in.
    channel_index = db.Column(db.Integer, nullable=True)
    duration_seconds = db.Column(db.Float, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'recording_id': self.recording_id,
            'track_index': self.track_index,
            'source_path': self.source_path,
            'speaker_name': self.speaker_name or '',
            'channel_index': self.channel_index,
            'duration_seconds': self.duration_seconds,
        }
