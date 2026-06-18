import os
from datetime import timedelta


def _parse_max_content_length():
    """Bytes or None (no limit). Env MAX_CONTENT_LENGTH: integer or none/unlimited/empty."""
    raw = (os.environ.get('MAX_CONTENT_LENGTH') or '').strip()
    if not raw or raw.lower() in ('none', 'unlimited', '0'):
        return None
    return int(raw)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'postgresql://postgres:password@localhost/hrms'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Flask/Werkzeug — omit or use env when behind nginx/IIS so limits stay aligned.
    MAX_CONTENT_LENGTH = _parse_max_content_length()
