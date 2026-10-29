"""NAS connect must fail fast / never block app startup when the share is offline."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.ermt_nas_service import (
    _nas_skip_requested,
    _unc_host,
    ensure_ermt_nas_connected,
    nas_connection_status,
    schedule_ermt_nas_connect,
    unc_path_isfile,
)


class ErmtNasHelpersTests(unittest.TestCase):
    def test_unc_host(self):
        self.assertEqual(_unc_host(r'\\192.168.20.3\hrmdo'), '192.168.20.3')
        self.assertIsNone(_unc_host(r'C:\photos'))

    def test_skip_env(self):
        with patch.dict('os.environ', {'ERMT_NAS_SKIP': '1'}, clear=False):
            self.assertTrue(_nas_skip_requested())
        with patch.dict('os.environ', {'ERMT_NAS_SKIP': '0'}, clear=False):
            self.assertFalse(_nas_skip_requested())

    def test_unc_path_isfile_local(self):
        self.assertFalse(unc_path_isfile(''))
        self.assertFalse(unc_path_isfile(r'C:\definitely\missing\hrms-photo.jpg'))


class ErmtNasConnectTests(unittest.TestCase):
    def test_skip_does_not_touch_unc(self):
        with patch.dict('os.environ', {'ERMT_NAS_SKIP': '1'}, clear=False):
            with patch('app.ermt_nas_service._smb_host_reachable') as probe:
                with patch('app.ermt_nas_service._isdir_with_timeout') as isdir:
                    self.assertFalse(ensure_ermt_nas_connected())
                    probe.assert_not_called()
                    isdir.assert_not_called()
            self.assertIn('skipped', nas_connection_status().lower())

    def test_unreachable_host_returns_false(self):
        with patch.dict('os.environ', {'ERMT_NAS_SKIP': ''}, clear=False):
            with patch('app.ermt_nas_service._nas_skip_requested', return_value=False):
                with patch('app.ermt_nas_service._smb_host_reachable', return_value=False):
                    with patch('app.ermt_nas_service._isdir_with_timeout') as isdir:
                        with patch('app.ermt_nas_service._win_connect_unc') as win:
                            self.assertFalse(ensure_ermt_nas_connected(force=True))
                            isdir.assert_not_called()
                            win.assert_not_called()

    def test_schedule_does_not_call_ensure_inline(self):
        import app.ermt_nas_service as nas

        nas._nas_bg_started = False
        with patch.dict('os.environ', {'ERMT_NAS_SKIP': '0'}, clear=False):
            with patch('app.ermt_nas_service._nas_skip_requested', return_value=False):
                with patch('app.ermt_nas_service.ensure_ermt_nas_connected') as ensure:
                    with patch('app.ermt_nas_service.threading.Thread') as thr:
                        instance = thr.return_value
                        schedule_ermt_nas_connect()
                        thr.assert_called_once()
                        instance.start.assert_called_once()
                        ensure.assert_not_called()
        nas._nas_bg_started = False


if __name__ == '__main__':
    unittest.main()
