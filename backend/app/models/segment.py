from datetime import datetime, UTC

from ..extensions import db


class Segment(db.Model):
    __tablename__ = 'segments'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    name = db.Column(db.String(300), nullable=False, default='')
    description = db.Column(db.Text, default='')
    screener_questions = db.Column(db.Text, default='')
    target_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def to_dict(self, assigned_count=0):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'name': self.name,
            'description': self.description,
            'screener_questions': self.screener_questions,
            'target_count': self.target_count,
            'assigned_count': assigned_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
