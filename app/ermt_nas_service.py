"""Connect the HRMS process to the office NAS share for eRMT / ID photo UNC paths.

When HRMS runs as hrms.exe on a server (Waitress / PyInstaller), the Windows account
often has no mapped drive to \\\\192.168.20.3\\hrmdo. Set environment variables before
starting the app:

  ERMT_NAS_ROOT=\\\\192.168.20.3\\hrmdo
  ERMT_NAS_USER=hrmdo
  ERMT_NAS_PASSWORD=<share password>

Optional:
  ERMT_NAS_SKIP=1   — never attempt UNC login (app always starts; no ID photos from NAS)

NAS access is optional. Startup never waits on the share. Connection is attempted in a
background thread (or lazily on first photo request) with short TCP / UNC timeouts.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

from flask import current_app

_nas_lock = threading.Lock()
_nas_connected = False
_nas_last_error: str | None = None
_nas_host_down_until = 0.0
_nas_bg_started = False

_SMB_PROBE_TIMEOUT_SEC = 0.75
_UNREACHABLE_TTL_SEC = 120.0
_UNC_IO_TIMEOUT_SEC = 1.5
_WNET_TIMEOUT_SEC = 2.0


def _truthy_env(raw: str | None) -> bool:
    return (raw or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _nas_skip_requested() -> bool:
    env = os.environ.get('ERMT_NAS_SKIP')
    if env is not None and str(env).strip() != '':
        return _truthy_env(env)
    try:
        return _truthy_env(current_app.config.get('ERMT_NAS_SKIP'))
    except RuntimeError:
        return False


def _unc_host(root: str) -> str | None:
    if not (root or '').startswith('\\\\'):
        return None
    host = root.lstrip('\\').split('\\', 1)[0].strip()
    return host or None


def _smb_host_reachable(host: str, timeout: float = _SMB_PROBE_TIMEOUT_SEC) -> bool:
    """True when SMB (445/139) accepts a TCP connection. Avoids hanging UNC I/O."""
    if not host:
        return False
    for port in (445, 139):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _unc_stat_with_timeout(path: str, *, want_dir: bool, timeout: float = _UNC_IO_TIMEOUT_SEC) -> bool:
    """Timed isdir/isfile — dead UNC shares can block Windows for a minute+."""
    result = [False]

    def _run() -> None:
        try:
            result[0] = os.path.isdir(path) if want_dir else os.path.isfile(path)
        except OSError:
            result[0] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False
    return result[0]


def _isdir_with_timeout(path: str, timeout: float = _UNC_IO_TIMEOUT_SEC) -> bool:
    return _unc_stat_with_timeout(path, want_dir=True, timeout=timeout)


def unc_path_isfile(path: str, timeout: float = _UNC_IO_TIMEOUT_SEC) -> bool:
    """Safe isfile for local or UNC paths (never hangs the request thread long)."""
    if not path:
        return False
    if path.startswith('\\\\'):
        return _unc_stat_with_timeout(path, want_dir=False, timeout=timeout)
    try:
        return os.path.isfile(path)
    except OSError:
        return False


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


def nas_connection_status(*, probe: bool = False) -> str:
    """Human-readable NAS status for logs (no secrets).

    By default uses cached state only so startup/logging never blocks on UNC.
    Pass probe=True to actively check (still fail-fast).
    """
    root = _nas_root_from_config()
    if _nas_skip_requested():
        return f'NAS skipped (ERMT_NAS_SKIP): {root}'
    if _nas_connected:
        if not probe or _isdir_with_timeout(root):
            return f'NAS connected: {root}'
    if _nas_last_error:
        return f'NAS not available ({root}): {_nas_last_error}'
    if probe:
        host = _unc_host(root)
        if host and not _smb_host_reachable(host):
            return f'NAS host unreachable ({host}); HRMS is running without eRMT/ID-photo files.'
        if _isdir_with_timeout(root):
            return f'NAS reachable without explicit login: {root}'
    return f'NAS connect deferred / not yet available ({root}); HRMS runs without waiting on the share.'


def schedule_ermt_nas_connect(app=None) -> None:
    """Fire-and-forget NAS login so create_app / run.py never block."""
    global _nas_bg_started

    if _nas_skip_requested():
        ensure_ermt_nas_connected()
        return

    with _nas_lock:
        if _nas_bg_started:
            return
        _nas_bg_started = True

    flask_app = app
    if flask_app is None:
        try:
            flask_app = current_app._get_current_object()  # type: ignore[attr-defined]
        except RuntimeError:
            flask_app = None

    def _worker() -> None:
        try:
            if flask_app is not None:
                with flask_app.app_context():
                    ensure_ermt_nas_connected()
                    print(nas_connection_status())
            else:
                ensure_ermt_nas_connected()
                print(nas_connection_status())
        except Exception as exc:
            print(f'Warning: background NAS connect failed: {exc}')

    threading.Thread(target=_worker, name='ermt-nas-connect', daemon=True).start()


def ensure_ermt_nas_connected(*, force: bool = False) -> bool:
    """Ensure the configured NAS UNC share is reachable from this process.

    Fail-fast: short TCP probe, timed UNC checks, timed WNet login.
    Returns False when the share is offline — callers must continue without NAS.
    """
    global _nas_connected, _nas_last_error, _nas_host_down_until

    root = _nas_root_from_config()
    if not root.startswith('\\\\'):
        return True

    if _nas_skip_requested():
        _nas_connected = False
        _nas_last_error = 'ERMT_NAS_SKIP is set; office NAS login was not attempted.'
        return False

    now = time.monotonic()
    if not force and now < _nas_host_down_until:
        return False

    if _nas_connected and not force:
        return True

    host = _unc_host(root)
    if host and not _smb_host_reachable(host):
        _nas_connected = False
        _nas_last_error = (
            f'NAS host {host} is not reachable on SMB (port 445/139). '
            'HRMS will continue without ID photos / eRMT files until the share is back.'
        )
        _nas_host_down_until = now + _UNREACHABLE_TTL_SEC
        return False

    with _nas_lock:
        if force:
            _nas_connected = False

        if _nas_connected and not force:
            return True

        if _isdir_with_timeout(root):
            _nas_connected = True
            _nas_last_error = None
            _nas_host_down_until = 0.0
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
            ok, err = _win_connect_unc(
                root, candidate_user, password, disconnect_on_conflict=force
            )
            if ok and _isdir_with_timeout(root):
                _nas_connected = True
                _nas_last_error = None
                _nas_host_down_until = 0.0
                try:
                    current_app.logger.info('NAS share connected: %s', root)
                except RuntimeError:
                    pass
                return True
            last_err = err

        _nas_connected = False
        _nas_last_error = last_err or f'Cannot open NAS share {root}'
        _nas_host_down_until = time.monotonic() + _UNREACHABLE_TTL_SEC
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
    try:
        ctypes.windll.mpr.WNetCancelConnection2W(remote, 0, True)
    except Exception:
        pass


def _win_connect_unc(
    remote: str,
    username: str,
    password: str,
    *,
    disconnect_on_conflict: bool = False,
) -> tuple[bool, str | None]:
    """Map remote UNC using WNetAddConnection2W with a hard timeout (never hang startup)."""
    result: list[tuple[bool, str | None]] = [(False, 'NAS login timed out')]

    def _run() -> None:
        result[0] = _win_connect_unc_blocking(
            remote, username, password, disconnect_on_conflict=disconnect_on_conflict
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(_WNET_TIMEOUT_SEC)
    if t.is_alive():
        return False, f'NAS login timed out after {_WNET_TIMEOUT_SEC:.0f}s ({remote})'
    return result[0]


def _win_connect_unc_blocking(
    remote: str,
    username: str,
    password: str,
    *,
    disconnect_on_conflict: bool = False,
) -> tuple[bool, str | None]:
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
            if rc == ERROR_SESSION_CREDENTIAL_CONFLICT and _isdir_with_timeout(remote):
                return True, None
        elif _isdir_with_timeout(remote):
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
