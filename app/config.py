import os
from datetime import timedelta

# Employment status (employee record, not appointment type)
EMPLOYEE_STATUSES = ('active', 'inactive', 'separated')

# Separation type options (aligned with TBGP MEMBER_SEPARATION_TYPES)
EMPLOYEE_SEPARATION_TYPES = (
    'Resigned',
    'End of Contract',
    'Retired',
    'Terminated',
    'Deceased',
)

# DTR justification reason dropdown (My DTR modal)
DTR_JUSTIFICATION_REASONS = (
    'Official Travel',
    'Compensatory Time Off',
    'On Leave',
    'Field Work',
    'Driving',
    'Overtime',
    'Biometric Error',
    'Brownount',
    'Attend LGU Meeting',
    'Excused with Dept. Head Consent',
    'Others',
)
DTR_JUSTIFICATION_REASON_OTHERS = 'Others'

def _parse_max_content_length():
    """Bytes or None (no limit). Env MAX_CONTENT_LENGTH: integer or none/unlimited/empty."""
    raw = (os.environ.get('MAX_CONTENT_LENGTH') or '').strip()
    if not raw or raw.lower() in ('none', 'unlimited', '0'):
        return None
    return int(raw)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'postgresql://postgres:password@localhost:5432/hrms'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Flask/Werkzeug — omit or use env when behind nginx/IIS so limits stay aligned.
    MAX_CONTENT_LENGTH = _parse_max_content_length()
    # Office NAS for eRMT / ID photos (UNC). Override with ERMT_NAS_ROOT if the share moves.
    ERMT_NAS_ROOT = (os.environ.get('ERMT_NAS_ROOT') or r'\\192.168.20.3\hrmdo').rstrip('\\')
    ERMT_NAS_ID_PHOTOS = (
        os.environ.get('ERMT_NAS_ID_PHOTOS')
        or (ERMT_NAS_ROOT + r'\ID PICTURES')
    )
    # Server / hrms.exe: set in OS environment (never commit passwords to git).
    ERMT_NAS_USER = os.environ.get('ERMT_NAS_USER') or ''
    ERMT_NAS_PASSWORD = os.environ.get('ERMT_NAS_PASSWORD') or ''
