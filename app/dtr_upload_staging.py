"""Temporary server-side storage for parsed DTR upload batches (avoids huge HTML payloads)."""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path


def _staging_dir() -> Path:
    root = Path(__file__).resolve().parent.parent / 'data' / 'dtr_upload_staging'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _purge_old_staging_files(max_age_hours: int = 24) -> None:
    cutoff = time.time() - max_age_hours * 3600
    for path in _staging_dir().glob('*.json'):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def stage_dtr_upload_rows(rows: list[dict], owner_key: str) -> str:
    """Persist parsed rows; return opaque token for save step."""
    _purge_old_staging_files()
    token = secrets.token_urlsafe(24)
    payload = {
        'owner_key': owner_key,
        'rows': rows,
        'created_at': datetime.utcnow().isoformat(),
    }
    path = _staging_dir() / f'{token}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return token


def load_staged_dtr_upload_rows(token: str, owner_key: str) -> tuple[list[dict] | None, str | None]:
    """Load staged rows for the same uploader. Returns (rows, error_message)."""
    safe = (token or '').strip()
    if not safe or '/' in safe or '\\' in safe or '..' in safe:
        return None, 'Invalid upload session.'
    path = _staging_dir() / f'{safe}.json'
    if not path.is_file():
        return None, 'Upload session expired or not found. Please upload the file again.'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None, 'Upload session is corrupted. Please upload the file again.'
    if payload.get('owner_key') != owner_key:
        return None, 'Access denied for this upload session.'
    rows = payload.get('rows')
    if not isinstance(rows, list):
        return None, 'Upload session is invalid. Please upload the file again.'
    return rows, None


def discard_staged_dtr_upload(token: str) -> None:
    safe = (token or '').strip()
    if not safe or '/' in safe or '\\' in safe or '..' in safe:
        return
    path = _staging_dir() / f'{safe}.json'
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
