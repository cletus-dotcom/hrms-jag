from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for
from flask_login import UserMixin, login_user

from app.portal_config import PORTAL_ADMIN_USERNAME

PORTAL_ADMIN_USER_ID = 'portal_admin'


class PortalAdminUser(UserMixin):
    """Synthetic Flask-Login user — grants full Admin access in HRMS."""

    id = None
    username = PORTAL_ADMIN_USERNAME
    email = ''
    role = 'admin'
    employee_id = None
    is_active = True

    @property
    def employee(self):
        return None

    def get_id(self):
        return PORTAL_ADMIN_USER_ID

    def check_password(self, password):
        return False


def is_portal_admin_session():
    return session.get('is_portal_admin') is True


def start_portal_admin_session():
    login_user(PortalAdminUser(), remember=False)
    session['username'] = PORTAL_ADMIN_USERNAME
    session['user_role'] = 'admin'
    session['is_portal_admin'] = True
    session.modified = True


def portal_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_portal_admin_session():
            return view(*args, **kwargs)
        if request.is_json or request.method in ('POST', 'PUT', 'DELETE'):
            return jsonify({'ok': False, 'error': 'Portal admin access required.'}), 403
        flash('Portal administrator sign-in required.', 'error')
        return redirect(url_for('routes.login', next=request.path))
    return wrapped
