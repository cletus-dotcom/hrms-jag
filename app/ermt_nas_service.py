"""Connect the HRMS process to the office NAS share for eRMT / ID photo UNC paths.

When HRMS runs as hrms.exe on a server (Waitress / PyInstaller), the Windows account
often has no mapped drive to \\\\192.168.20.3\\hrmdo. Set environment variables before
starting the app:

  ERMT_NAS_ROOT=\\\\192.168.20.3\\hrmdo
  ERMT_NAS_USER=hrmdo
  ERMT_NAS_PASSWORD=<share password>

Credentials are read from the environment only — never commit passwords to the repo.
"""

from __future__ import annotations

import os
import sys
import threading

from flask import current_app

_nas_lock = threading.Lock()
_nas_connected = False
_nas_last_error: str | None = None


def _nas_root_from_config() -> str:
    try:
        root = (current_app.config.get('ERMT_NAS_ROOT') or '').strip()
        if root:
            return root.rstrip('\\')
    except RuntimeError:
        pass
    return (os.environ.get('ERMT_NAS_ROOT') or r'\\192.168.20.3\hrmdo').rstrip('\\')


def _nas_credentials_from_config() -> tuple[str, str]:
    user = (os.environ.get('ERMT_NAS_USER') or '').strip()
    password = os.environ.get('ERMT_NAS_PASSWORD') or ''
    try:
        if not user:
            user = (current_app.config.get('ERMT_NAS_USER') or '').strip()
        if not password:
            password = current_app.config.get('ERMT_NAS_PASSWORD') or ''
    except RuntimeError:
        pass
    return user, password


def nas_connection_status() -> str:
    """Human-readable NAS status for startup logs (no secrets)."""
    root = _nas_root_from_config()
    if _nas_connected and os.path.isdir(root):
        return f'NAS connected: {root}'
    if _nas_last_error:
        return f'NAS not available ({root}): {_nas_last_error}'
    if os.path.isdir(root):
        return f'NAS reachable without explicit login: {root}'
    user, _ = _nas_credentials_from_config()
    if not user:
        return (
            f'NAS not reachable ({root}). Set ERMT_NAS_USER and ERMT_NAS_PASSWORD '
            'in the server environment before starting hrms.exe.'
        )
    return f'NAS not reachable ({root}); check ERMT_NAS_USER / ERMT_NAS_PASSWORD.'


def ensure_ermt_nas_connected(*, force: bool = False) -> bool:
    """Ensure the configured NAS UNC share is reachable from this process."""
    global _nas_connected, _nas_last_error

    root = _nas_root_from_config()
    if not root.startswith('\\\\'):
        return True

    with _nas_lock:
        if force:
            _nas_connected = False

        if _nas_connected and not force and os.path.isdir(root):
            return True

        if os.path.isdir(root):
            _nas_connected = True
            _nas_last_error = None
            return True

        user, password = _nas_credentials_from_config()
        if not user or not password:
            _nas_last_error = (
                'ERMT_NAS_USER and ERMT_NAS_PASSWORD are not set for this process. '
                'Set them in Windows environment variables or in a .env file next to hrms.exe.'
            )
            return False

        if sys.platform != 'win32':
            _nas_last_error = 'UNC NAS login is only supported on Windows hosts.'
            return False

        if force:
            _win_disconnect_unc(root)

        last_err: str | None = None
        for candidate_user in _nas_username_candidates(user, root):
            ok, err = _win_connect_unc(root, candidate_user, password, disconnect_on_conflict=force)
            if ok and os.path.isdir(root):
                _nas_connected = True
                _nas_last_error = None
                try:
                    current_app.logger.info('NAS share connected: %s', root)
                except RuntimeError:
                    pass
                return True
            last_err = err

        _nas_connected = False
        _nas_last_error = last_err or f'Cannot open NAS share {root}'
        try:
            current_app.logger.warning('NAS connect failed for %s: %s', root, _nas_last_error)
        except RuntimeError:
            pass
        return False


def _nas_username_candidates(username: str, nas_root: str) -> list[str]:
    """Try plain username then host\\username (Synology SMB)."""
    username = (username or '').strip()
    if not username:
        return []
    candidates = [username]
    if '\\' not in username and '@' not in username:
        host = nas_root.lstrip('\\').split('\\', 1)[0]
        if host:
            prefixed = f'{host}\\{username}'
            if prefixed not in candidates:
                candidates.append(prefixed)
    return candidates


def _win_disconnect_unc(remote: str) -> None:
    if sys.platform != 'win32':
        return
    import ctypes
    ctypes.windll.mpr.WNetCancelConnection2W(remote, 0, True)


def _win_connect_unc(
    remote: str,
    username: str,
    password: str,
    *,
    disconnect_on_conflict: bool = False,
) -> tuple[bool, str | None]:
    """Map remote UNC using WNetAddConnection2W (stdlib ctypes, no pywin32)."""
    import ctypes
    from ctypes import wintypes

    class NETRESOURCEW(ctypes.Structure):
        _fields_ = [
            ('dwScope', wintypes.DWORD),
            ('dwType', wintypes.DWORD),
            ('dwDisplayType', wintypes.DWORD),
            ('dwUsage', wintypes.DWORD),
            ('lpLocalName', wintypes.LPCWSTR),
            ('lpRemoteName', wintypes.LPCWSTR),
            ('lpComment', wintypes.LPCWSTR),
            ('lpProvider', wintypes.LPCWSTR),
        ]

    RESOURCETYPE_DISK = 0x00000001
    NO_ERROR = 0
    ERROR_ALREADY_ASSIGNED = 85
    ERROR_DEVICE_ALREADY_REMEMBERED = 1202
    ERROR_SESSION_CREDENTIAL_CONFLICT = 1219

    nr = NETRESOURCEW()
    nr.dwType = RESOURCETYPE_DISK
    nr.lpRemoteName = remote

    rc = ctypes.windll.mpr.WNetAddConnection2W(
        ctypes.byref(nr),
        password,
        username,
        0,
    )

    if rc in (NO_ERROR, ERROR_ALREADY_ASSIGNED, ERROR_DEVICE_ALREADY_REMEMBERED):
        return True, None

    if rc == ERROR_SESSION_CREDENTIAL_CONFLICT:
        if disconnect_on_conflict:
            ctypes.windll.mpr.WNetCancelConnection2W(remote, 0, True)
            rc = ctypes.windll.mpr.WNetAddConnection2W(
                ctypes.byref(nr),
                password,
                username,
                0,
            )
            if rc in (NO_ERROR, ERROR_ALREADY_ASSIGNED, ERROR_DEVICE_ALREADY_REMEMBERED):
                return True, None
            if rc == ERROR_SESSION_CREDENTIAL_CONFLICT and os.path.isdir(remote):
                return True, None
        elif os.path.isdir(remote):
            return True, None

    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.kernel32.FormatMessageW(
        0x00001000,
        None,
        rc,
        0,
        buf,
        len(buf),
        None,
    )
    msg = buf.value.strip() or f'Windows error {rc}'
    return False, msg
