import os

PORTAL_ADMIN_USERNAME = (os.environ.get('PORTAL_ADMIN_USERNAME') or 'PortalAdmin').strip()
PORTAL_ADMIN_PASSWORD = (os.environ.get('PORTAL_ADMIN_PASSWORD') or 'portal123').strip()
PORTAL_ADMIN_ROLE = 'PortalAdmin'

SITE_UPLOAD_SUBDIR = 'site_uploads'
ALLOWED_LOGO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'}


def verify_portal_admin_credentials(username, password):
    return (
        (username or '').strip() == PORTAL_ADMIN_USERNAME
        and (password or '').strip() == PORTAL_ADMIN_PASSWORD
    )


def is_portal_admin_role(role=None):
    from app.portal_auth import is_portal_admin_session
    if is_portal_admin_session():
        return True
    return (role or '').strip() == PORTAL_ADMIN_ROLE


def is_admin_role(role=None):
    if is_portal_admin_role(role):
        return True
    return (role or '').strip().lower() == 'admin'


def is_admin_or_hr_role(role=None):
    if is_portal_admin_role(role):
        return True
    return (role or '').strip().lower() in ('admin', 'hr')


def is_reserved_portal_username(username):
    return (username or '').strip() == PORTAL_ADMIN_USERNAME
