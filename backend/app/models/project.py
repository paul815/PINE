import json
from datetime import datetime, timezone

from ..extensions import db


class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(300), nullable=False, default='Untitled project')
    description = db.Column(db.Text, default='')
    objective = db.Column(db.Text, default='')
    research_questions = db.Column(db.Text, default='[]')
    hypotheses = db.Column(db.Text, default='[]')
    summary = db.Column(db.Text, default='')
    results_recommendations = db.Column(db.Text, default='')
    further_steps = db.Column(db.Text, default='')
    stakeholders = db.Column(db.Text, default='[]')
    enabled_sections = db.Column(db.Text, default='[]')
    methodology = db.Column(db.Text, default='')
    interview_guide = db.Column(db.Text, default='')
    key_findings = db.Column(db.Text, default='')
    recommendations = db.Column(db.Text, default='')
    enabled_summary_sections = db.Column(db.Text, default='["key_findings"]')
    custom_sections = db.Column(db.Text, default='[]')
    custom_summary_sections = db.Column(db.Text, default='[]')
    icon = db.Column(db.String(10), default='')
    is_archived = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.DateTime, nullable=True)
    is_system = db.Column(db.Boolean, default=False)
    folder_name = db.Column(db.String(300), nullable=False)
    default_transcription_language = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    recordings = db.relationship('Recording', backref='project', lazy=True,
                                 cascade='all, delete-orphan')
    segments = db.relationship('Segment', backref='project', lazy=True,
                               cascade='all, delete-orphan')

    def get_questions(self):
        try:
            return json.loads(self.research_questions)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_questions(self, items):
        self.research_questions = json.dumps(items)

    def get_hypotheses(self):
        try:
            return json.loads(self.hypotheses)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_hypotheses(self, items):
        self.hypotheses = json.dumps(items)

    def get_stakeholders(self):
        try:
            return json.loads(self.stakeholders)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_stakeholders(self, items):
        self.stakeholders = json.dumps(items)

    def get_enabled_sections(self):
        try:
            return json.loads(self.enabled_sections)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_enabled_sections(self, sections):
        self.enabled_sections = json.dumps(sections)

    def get_enabled_summary_sections(self):
        try:
            return json.loads(self.enabled_summary_sections)
        except (json.JSONDecodeError, TypeError):
            return ['key_findings']

    def set_enabled_summary_sections(self, sections):
        self.enabled_summary_sections = json.dumps(sections)

    def get_custom_sections(self):
        try:
            return json.loads(self.custom_sections)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_custom_sections(self, blocks):
        self.custom_sections = json.dumps(blocks)

    def get_custom_summary_sections(self):
        try:
            return json.loads(self.custom_summary_sections)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_custom_summary_sections(self, blocks):
        self.custom_summary_sections = json.dumps(blocks)

    def to_dict(self, include_recordings=False):
        d = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'objective': self.objective,
            'research_questions': self.get_questions(),
            'hypotheses': self.get_hypotheses(),
            'summary': self.summary,
            'results_recommendations': self.results_recommendations,
            'further_steps': self.further_steps,
            'stakeholders': self.get_stakeholders(),
            'enabled_sections': self.get_enabled_sections(),
            'methodology': self.methodology or '',
            'interview_guide': self.interview_guide or '',
            'key_findings': self.key_findings or '',
            'recommendations': self.recommendations or '',
            'enabled_summary_sections': self.get_enabled_summary_sections(),
            'custom_sections': self.get_custom_sections(),
            'custom_summary_sections': self.get_custom_summary_sections(),
            'icon': self.icon,
            'is_archived': self.is_archived,
            'archived_at': self.archived_at.isoformat() if self.archived_at else None,
            'is_system': bool(self.is_system),
            'folder_name': self.folder_name,
            'default_transcription_language': self.default_transcription_language or '',
            'recording_count': len(self.recordings),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_recordings:
            d['recordings'] = [r.to_dict() for r in self.recordings]
        return d
