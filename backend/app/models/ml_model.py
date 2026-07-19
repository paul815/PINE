from ..extensions import db


class MLModel(db.Model):
    __tablename__ = 'ml_models'

    id = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    function = db.Column(db.String(50), nullable=False)
    repo_id = db.Column(db.String(200))
    filename = db.Column(db.String(200))
    size_bytes = db.Column(db.BigInteger, default=0)
    downloaded_bytes = db.Column(db.BigInteger, default=0)
    status = db.Column(db.String(20), default='not_downloaded')
    path = db.Column(db.Text)
    language = db.Column(db.String(10))
    required = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'function': self.function,
            'repo_id': self.repo_id,
            'size_bytes': self.size_bytes,
            'downloaded_bytes': self.downloaded_bytes,
            'status': self.status,
            'language': self.language,
            'required': self.required,
            'error_message': self.error_message,
        }
