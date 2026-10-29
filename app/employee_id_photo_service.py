"""Resolve and serve employee ID photos linked from eRMT NAS/network paths."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from flask import current_app

# Temporary demo link: Caido ID photo on local/NAS path via this eRMT number.
TEMP_ID_PHOTO_ERMT_NUMBER = 'ERMT-260716-01'
TEMP_ID_PHOTO_EMPLOYEE_BADGE = '40555'

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}


def _mime_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    guessed, _ = mimetypes.guess_type(path)
    if guessed and guessed.startswith('image/'):
        return guessed
    return {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
    }.get(ext, 'application/octet-stream')


def get_id_photo_ermt_record(employee):
    """Return the ErmtRecord linked as this employee's active ID photo."""
    if employee is None:
        return None
    from app.models import ErmtRecord

    if getattr(employee, 'id_photo_ermt_record_id', None):
        record = ErmtRecord.query.get(employee.id_photo_ermt_record_id)
        if record:
            return record
    return None


def resolve_id_photo_filesystem_path(employee) -> tuple[str | None, object | None, str | None]:
    """
    Resolve readable filesystem path for an employee's ID photo.
    Returns (path, ermt_record, error_reason).
    """
    record = get_id_photo_ermt_record(employee)
    if not record:
        return None, None, 'no_link'
    raw = (record.pdf_link or record.local_file_path or '').strip()
    if not raw:
        return None, record, 'no_path'
    path = raw
    if path.lower().startswith('file:'):
        from app.ermt_helpers import normalize_network_pdf_path
        normalized, err = normalize_network_pdf_path(path)
        if err or not normalized:
            return None, record, 'bad_path'
        path = normalized
    else:
        from app.ermt_helpers import rewrite_ermt_nas_aliases
        path = rewrite_ermt_nas_aliases(path) or path
    from app.ermt_nas_service import ensure_ermt_nas_connected, unc_path_isfile
    if path.startswith('\\\\'):
        if not ensure_ermt_nas_connected():
            return None, record, 'nas_unavailable'
    ext = Path(path).suffix.lower()
    if ext and ext not in IMAGE_EXTENSIONS:
        return None, record, 'not_image'
    if not unc_path_isfile(path):
        if path.startswith('\\\\'):
            from app.ermt_nas_service import ensure_ermt_nas_connected, nas_connection_status
            if not ensure_ermt_nas_connected(force=True):
                return None, record, 'nas_unavailable'
            if not unc_path_isfile(path):
                current_app.logger.warning(
                    'ID photo missing for employee %s at %s (%s)',
                    getattr(employee, 'id', None),
                    path,
                    nas_connection_status(),
                )
                return None, record, 'missing_file'
        else:
            return None, record, 'missing_file'
    return path, record, None


def read_employee_id_photo(employee) -> tuple[bytes | None, str | None, object | None]:
    """Return (image_bytes, mimetype, ermt_record) or (None, None, record_or_None)."""
    path, record, err = resolve_id_photo_filesystem_path(employee)
    if not path:
        return None, None, record
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        current_app.logger.warning('ID photo read failed for employee %s: %s', getattr(employee, 'id', None), exc)
        return None, None, record
    if not data:
        return None, None, record
    return data, _mime_for_path(path), record


def employee_has_id_photo(employee) -> bool:
    path, _, err = resolve_id_photo_filesystem_path(employee)
    return bool(path) and err is None


def can_view_employee_id_photo(viewer, employee) -> bool:
    """Self, Admin/HR, or portal admin may view; managers may view own only for now."""
    if viewer is None or employee is None:
        return False
    from app.portal_auth import is_portal_admin_session
    from app.portal_config import is_admin_or_hr_role

    if is_portal_admin_session():
        return True
    role = (getattr(viewer, 'role', None) or '').strip().lower()
    if is_admin_or_hr_role(role):
        return True
    linked = getattr(viewer, 'employee', None)
    if linked is not None and linked.id == employee.id:
        return True
    return False


def link_employee_id_photo_by_ermt_number(employee, record_number: str) -> tuple[bool, str]:
    """Admin helper: set employee.id_photo_ermt_record_id from an eRMT record number (no commit)."""
    from app.models import ErmtRecord

    number = (record_number or '').strip().upper()
    if not number:
        return False, 'Enter an eRMT record number.'
    record = ErmtRecord.query.filter_by(record_number=number).first()
    if not record:
        return False, f'eRMT record {number} not found.'
    path = (record.pdf_link or record.local_file_path or '').strip()
    if not path:
        return False, f'{number} has no network file path.'
    ext = Path(path.replace('/', '\\')).suffix.lower()
    if ext and ext not in IMAGE_EXTENSIONS:
        return False, f'{number} path is not an image ({ext or "unknown"}).'
    employee.id_photo_ermt_record_id = record.id
    return True, f'ID photo linked to {number}.'


def ensure_temp_id_photo_link() -> str | None:
    """
    One-time/temp: link employee badge 40555 to eRMT ERMT-260716-01 when unset.
    Returns a short status message or None if nothing to do / not found.
    """
    from app import db
    from app.models import Employee, ErmtRecord

    record = ErmtRecord.query.filter_by(record_number=TEMP_ID_PHOTO_ERMT_NUMBER).first()
    if not record:
        return f'Temp ID photo: {TEMP_ID_PHOTO_ERMT_NUMBER} not found'
    emp = Employee.query.filter_by(employee_id=TEMP_ID_PHOTO_EMPLOYEE_BADGE).first()
    if not emp:
        return f'Temp ID photo: employee {TEMP_ID_PHOTO_EMPLOYEE_BADGE} not found'
    if emp.id_photo_ermt_record_id == record.id:
        return None
    emp.id_photo_ermt_record_id = record.id
    db.session.commit()
    return f'Temp ID photo: linked {TEMP_ID_PHOTO_EMPLOYEE_BADGE} to {TEMP_ID_PHOTO_ERMT_NUMBER}'
