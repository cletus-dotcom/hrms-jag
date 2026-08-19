"""Load deployment environment variables before Flask config is imported.

Dev: reads ``<project>/.env``.
PyInstaller: reads ``<folder containing hrms.exe>/.env`` (or ``hrms.env``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def deploy_root_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def load_deploy_env() -> Path | None:
    """Load the first ``.env`` / ``hrms.env`` found. Returns the path loaded or None."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None

    candidates = [
        deploy_root_dir() / '.env',
        deploy_root_dir() / 'hrms.env',
        Path.cwd() / '.env',
        Path.cwd() / 'hrms.env',
    ]
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        load_dotenv(resolved, override=False)
        return resolved
    return None
