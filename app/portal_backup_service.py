"""PostgreSQL backup utilities for Portal Console."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from urllib.parse import urlparse

from flask import current_app


def backup_output_dir() -> str:
    root = current_app.root_path
    path = os.path.join(os.path.dirname(root), 'data', 'backups')
    os.makedirs(path, exist_ok=True)
    return path


def parse_database_url(uri: str | None = None) -> dict:
    raw = uri or current_app.config.get('SQLALCHEMY_DATABASE_URI') or ''
    parsed = urlparse(raw)
    return {
        'host': parsed.hostname or 'localhost',
        'port': str(parsed.port or 5432),
        'user': parsed.username or 'postgres',
        'password': parsed.password or '',
        'dbname': (parsed.path or '/hrms').lstrip('/') or 'hrms',
    }


def find_pg_dump() -> str:
    for version in ('17', '16', '15', '14', '13'):
        candidate = rf'C:\Program Files\PostgreSQL\{version}\bin\pg_dump.exe'
        if os.path.exists(candidate):
            return candidate
    return 'pg_dump'


def list_recent_backups(limit: int = 7) -> list[dict]:
    out_dir = backup_output_dir()
    files = [
        f for f in os.listdir(out_dir)
        if f.startswith('hrms_') and (f.endswith('.backup') or f.endswith('.sql'))
    ]
    files.sort(key=lambda name: os.path.getmtime(os.path.join(out_dir, name)), reverse=True)
    items = []
    for name in files[:limit]:
        path = os.path.join(out_dir, name)
        items.append({
            'name': name,
            'path': path,
            'size_mb': round(os.path.getsize(path) / (1024 * 1024), 2),
            'modified': datetime.fromtimestamp(os.path.getmtime(path)),
        })
    return items


def backup_hrms_db(retain: int = 7, fmt: str = 'custom') -> str:
    """Run pg_dump. fmt: 'custom' (.backup) or 'plain' (.sql)."""
    cfg = parse_database_url()
    out_dir = backup_output_dir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    ext = '.backup' if fmt == 'custom' else '.sql'
    filename = f'hrms_{timestamp}{ext}'
    filepath = os.path.join(out_dir, filename)

    pg_dump = find_pg_dump()
    cmd = [
        pg_dump,
        '-h', cfg['host'],
        '-p', cfg['port'],
        '-U', cfg['user'],
        '-F', 'c' if fmt == 'custom' else 'p',
        '-f', filepath,
        cfg['dbname'],
    ]
    env = os.environ.copy()
    if cfg['password']:
        env['PGPASSWORD'] = cfg['password']

    subprocess.run(cmd, check=True, env=env)

    backups = sorted(
        [f for f in os.listdir(out_dir) if f.startswith('hrms_') and f.endswith(ext)],
        key=lambda f: os.path.getmtime(os.path.join(out_dir, f)),
        reverse=True,
    )
    for old in backups[retain:]:
        try:
            os.remove(os.path.join(out_dir, old))
        except OSError:
            pass

    return filepath
