"""eRMT helpers — record types, numbering, NAS/network PDF path linking."""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import quote

ERMT_RECORD_TYPES = [
    'Memorandum',
    'Office Order',
    'Reports',
    'Certificates',
    'Contracts',
    'Personnel Records',
    '201 File',
    '2316 File',
    'ID Photo',
    'Others',
]


def is_valid_ermt_record_type(record_type: str | None) -> bool:
    return bool(record_type) and record_type in ERMT_RECORD_TYPES


def parse_ermt_record_type_fields(record_type, record_type_part):
    record_type = (record_type or '').strip()
    record_type_part = (record_type_part or '').strip()
    if record_type == 'Others':
        if not record_type_part:
            return None, None, 'Please specify the record type.'
        return record_type, record_type_part, None
    if record_type and not is_valid_ermt_record_type(record_type):
        return None, None, 'Invalid record type.'
    return record_type, None, None


def display_ermt_record_type(record_type, record_type_part=None):
    if record_type == 'Others' and record_type_part:
        return record_type_part
    return record_type or ''


def parse_ermt_date(value) -> datetime:
    if value is None or value == '':
        return datetime.utcnow()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f'Invalid date: {value}')


def generate_ermt_record_number(for_date=None) -> str:
    """Generate eRMT record number as ERMT-YYMMDD-NN."""
    from app.models import ErmtRecord

    when = parse_ermt_date(for_date) if for_date is not None else datetime.utcnow()
    date_prefix = when.strftime('%y%m%d')
    prefix = f'ERMT-{date_prefix}-'
    records = ErmtRecord.query.filter(ErmtRecord.record_number.like(f'{prefix}%')).all()

    def extract_seq(record_number):
        if not record_number or not record_number.startswith(prefix):
            return 0
        try:
            return int(record_number[len(prefix):])
        except ValueError:
            return 0

    latest_seq = max([extract_seq(r.record_number) for r in records], default=0)
    next_seq = latest_seq + 1
    if next_seq > 99:
        raise ValueError('Daily record limit (99) reached for this date.')
    return f'{prefix}{next_seq:02d}'


def get_ermt_nas_root() -> str:
    """Configured office NAS root (default \\\\192.168.20.3\\hrmdo)."""
    try:
        from flask import current_app
        root = (current_app.config.get('ERMT_NAS_ROOT') or '').strip()
        if root:
            return root.rstrip('\\')
    except RuntimeError:
        pass
    return r'\\192.168.20.3\hrmdo'


def get_ermt_nas_id_photos_root() -> str:
    """Folder for employee ID photos on the NAS."""
    try:
        from flask import current_app
        root = (current_app.config.get('ERMT_NAS_ID_PHOTOS') or '').strip()
        if root:
            return root.rstrip('\\')
    except RuntimeError:
        pass
    return get_ermt_nas_root() + r'\ID PICTURES'


def normalize_network_pdf_path(raw: str | None) -> tuple[str | None, str | None]:
    """
    Normalize a user-specified NAS / local-network PDF path.
    Returns (stored_path, error_message).
    Accepts UNC (\\\\server\\share\\file.pdf), mapped drives (Z:\\folder\\file.pdf),
    or file:// URLs. Rejects Google Drive / cloud http links.
    """
    value = (raw or '').strip().strip('"').strip("'")
    if not value:
        return None, None

    lower = value.lower()
    if 'drive.google.com' in lower or 'docs.google.com' in lower:
        return None, 'Google Drive links are not used. Paste a local network / NAS path instead.'

    # Already a file:// URL — keep as stored display path in UNC form when possible
    if lower.startswith('file:'):
        # file://///server/share/path.pdf or file:///Z:/path.pdf
        rest = re.sub(r'^file:/+', '', value, flags=re.IGNORECASE)
        rest = rest.replace('/', '\\')
        if re.match(r'^[A-Za-z]:\\', rest):
            return rewrite_ermt_nas_aliases(rest), None
        collapsed = _collapse_unc_body(rest if rest.startswith('\\') else ('\\' + rest))
        return rewrite_ermt_nas_aliases(collapsed), None

    # Reject other http(s) cloud URLs; allow only if somehow intranet http later — user asked network only
    if lower.startswith('http://') or lower.startswith('https://'):
        nas = get_ermt_nas_root()
        return None, (
            f'Use a local network path (e.g. {nas}\\folder\\file.pdf or Z:\\folder\\file.pdf), '
            'not a web URL.'
        )

    # Normalize slashes for UNC / Windows paths
    path = value.replace('/', '\\')

    # Mapped drive letter
    if re.match(r'^[A-Za-z]:\\', path):
        while '\\\\' in path[2:]:
            path = path[:2] + path[2:].replace('\\\\', '\\')
        return rewrite_ermt_nas_aliases(path), None

    # UNC \\server\share\... (also fix \\\server or more leading slashes)
    if path.startswith('\\'):
        return rewrite_ermt_nas_aliases(_collapse_unc_body(path)), None

    nas = get_ermt_nas_root()
    return None, (
        f'Enter a valid network path, e.g. {nas}\\ID PICTURES\\PLANTILLA\\40555.jpg '
        f'or Z:\\hrmdo\\ID PICTURES\\40555.jpg'
    )


def _collapse_unc_body(path: str) -> str:
    """Ensure UNC is exactly \\\\server\\share\\... with no doubled separators in the body."""
    path = path.replace('/', '\\')
    # Strip all leading backslashes, then re-apply exactly two.
    body = path.lstrip('\\')
    while '\\\\' in body:
        body = body.replace('\\\\', '\\')
    return '\\\\' + body


def rewrite_ermt_nas_aliases(path: str | None) -> str | None:
    """Map Synology/internal aliases to the office Windows UNC share.

    Examples:
      \\\\volume1\\hrmdo\\ID_PHOTO\\PLANTILLA\\5153.jpg
        -> \\\\192.168.20.3\\hrmdo\\ID PICTURES\\PLANTILLA\\5153.jpg
    """
    if not path:
        return path
    out = path.replace('/', '\\')
    nas = get_ermt_nas_root()
    # Synology DSM volume path used by mistake instead of the SMB share host.
    lower = out.lower()
    for alias in (r'\\volume1\hrmdo', r'\\volume1\HRMDO'):
        if lower.startswith(alias.lower()):
            out = nas + out[len(alias):]
            break
    # Folder rename on the share: ID_PHOTO -> ID PICTURES
    out = re.sub(r'(?i)\\ID_PHOTO\\', r'\\ID PICTURES\\', out)
    out = re.sub(r'(?i)\\ID_PHOTO$', r'\\ID PICTURES', out)
    return out


def network_path_to_open_href(stored_path: str | None) -> str:
    """
    Build a file:// href browsers can open for a stored network path.
    UNC \\\\server\\share\\a.pdf -> file://///server/share/a.pdf
    Drive Z:\\a.pdf -> file:///Z:/a.pdf
    """
    path = rewrite_ermt_nas_aliases((stored_path or '').strip()) or ''
    if not path:
        return ''
    lower = path.lower()
    if lower.startswith('file:'):
        return path
    if lower.startswith('http://') or lower.startswith('https://'):
        return path

    path = path.replace('/', '\\')
    if path.startswith('\\\\'):
        # file://///server/share/folder/file.pdf
        unc_body = path[2:].replace('\\', '/')
        parts = unc_body.split('/')
        encoded = '/'.join(quote(p, safe='') for p in parts if p != '')
        return f'file://///{encoded}'

    m = re.match(r'^([A-Za-z]):\\(.*)$', path)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace('\\', '/')
        encoded = '/'.join(quote(p, safe='') for p in rest.split('/') if p != '')
        return f'file:///{drive}:/{encoded}'

    return path


def can_edit_ermt(role: str | None) -> bool:
    from app.portal_config import is_admin_or_hr_role
    return is_admin_or_hr_role(role)


def can_delete_ermt(role: str | None) -> bool:
    from app.portal_config import is_admin_role
    return is_admin_role(role)
