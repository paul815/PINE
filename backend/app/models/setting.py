from ..extensions import db


class Setting(db.Model):
    __tablename__ = 'settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=None):
        s = db.session.get(Setting, key)
        return s.value if s else default

    @staticmethod
    def get_many(keys_defaults):
        """Fetch multiple settings in one query.

        Args:
            keys_defaults: dict of {key: default_value}
        Returns:
            dict of {key: value} with defaults applied for missing keys.
        """
        keys = list(keys_defaults.keys())
        rows = Setting.query.filter(Setting.key.in_(keys)).all()
        found = {r.key: r.value for r in rows}
        return {k: found.get(k, keys_defaults[k]) for k in keys}

    @staticmethod
    def set(key, value):
        s = db.session.get(Setting, key)
        if s:
            s.value = str(value)
        else:
            s = Setting(key=key, value=str(value))
            db.session.add(s)
        db.session.commit()
