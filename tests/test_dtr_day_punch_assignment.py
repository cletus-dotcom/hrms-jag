"""Four-slot day assignment for retained duplicate punches."""
from __future__ import annotations

import unittest
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

from app.dtr_upload_batch_service import (
    FLAG_STATUS_OPEN,
    FLAG_STATUS_RESOLVED,
    apply_day_punch_assignments,
)


def _punch(pid, *, slot, punch_time, assigned_slot=None, is_selected=False):
    p = MagicMock()
    p.id = pid
    p.batch_id = 1
    p.employee_id = 84
    p.record_date = date(2026, 7, 6)
    p.slot = slot
    p.punch_time = punch_time
    p.assigned_slot = assigned_slot
    p.is_selected = is_selected
    p.source_timestamp = f'2026-07-06 {punch_time.strftime("%H:%M:%S")}'
    p.line_no = pid
    p.sequence_no = pid
    return p


def _flag(slot='am_in'):
    f = MagicMock()
    f.batch_id = 1
    f.employee_id = 84
    f.record_date = date(2026, 7, 6)
    f.slot = slot
    f.status = FLAG_STATUS_OPEN
    f.selected_punch_id = None
    f.resolved_by_user_id = None
    f.resolved_at = None
    return f


class ApplyDayPunchAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.batch = MagicMock()
        self.batch.id = 1
        self.batch.status = 'active'
        self.user = MagicMock()
        self.user.id = 9

        self.p439 = _punch(101, slot='am_in', punch_time=time(4, 39))
        self.p439b = _punch(102, slot='am_in', punch_time=time(4, 39))
        self.p903 = _punch(103, slot='am_in', punch_time=time(9, 3), assigned_slot='am_in', is_selected=True)
        self.p1240 = _punch(104, slot='pm_in', punch_time=time(12, 40), assigned_slot='pm_in', is_selected=True)
        self.p1703 = _punch(105, slot='pm_out', punch_time=time(17, 3), assigned_slot='pm_out', is_selected=True)
        self.day_punches = [self.p439, self.p439b, self.p903, self.p1240, self.p1703]

        self.dtr = MagicMock()
        self.dtr.id = 59062
        self.dtr.employee_id = 84
        self.dtr.record_date = date(2026, 7, 6)
        self.dtr.am_in = time(9, 3)
        self.dtr.am_out = None
        self.dtr.pm_in = time(12, 40)
        self.dtr.pm_out = time(17, 3)

        self.flag = _flag('am_in')

    def _run(self, choices, *, day_punches=None, open_flags=None, dtr=None, existing_item=True):
        day_punches = day_punches if day_punches is not None else self.day_punches
        open_flags = open_flags if open_flags is not None else [self.flag]
        dtr = self.dtr if dtr is None and dtr is not False else (None if dtr is False else dtr)

        with patch('app.dtr_upload_batch_service.DtrUploadBatch') as Batch, \
             patch('app.dtr_upload_batch_service.punches_for_employee_day', return_value=day_punches), \
             patch('app.dtr_upload_batch_service.DailyTimeRecord') as DTR, \
             patch('app.dtr_upload_batch_service.editable_flags_for_employee_day', return_value=open_flags), \
             patch('app.dtr_upload_batch_service.DtrUploadBatchItem') as Item, \
             patch('app.dtr_upload_batch_service.log_batch_item') as log_item, \
             patch('app.dtr_upload_batch_service.snapshot_dtr_row', return_value={}), \
             patch('app.dtr_upload_batch_service.db'):
            Batch.query.get.return_value = self.batch
            DTR.query.filter_by.return_value.first.return_value = dtr
            if existing_item:
                Item.query.filter_by.return_value.first.return_value = MagicMock()
            else:
                Item.query.filter_by.return_value.first.return_value = None
            ok, msg = apply_day_punch_assignments(
                batch_id=1,
                employee_id=84,
                record_date=date(2026, 7, 6),
                slot_choices=choices,
                user=self.user,
            )
            return ok, msg, log_item

    def test_cross_slot_move_0439_am_in_and_0903_am_out(self):
        ok, msg, _ = self._run({
            'am_in': 101,   # 04:39
            'am_out': 103,  # 09:03
            'pm_in': 'keep',
            'pm_out': 'keep',
        })
        self.assertTrue(ok, msg)
        self.assertEqual(self.dtr.am_in, time(4, 39))
        self.assertEqual(self.dtr.am_out, time(9, 3))
        self.assertEqual(self.dtr.pm_in, time(12, 40))
        self.assertEqual(self.dtr.pm_out, time(17, 3))
        self.assertEqual(self.p439.assigned_slot, 'am_in')
        self.assertTrue(self.p439.is_selected)
        self.assertEqual(self.p903.assigned_slot, 'am_out')
        self.assertTrue(self.p903.is_selected)
        self.assertIsNone(self.p439b.assigned_slot)
        self.assertFalse(self.p439b.is_selected)
        self.assertEqual(self.flag.status, FLAG_STATUS_RESOLVED)
        self.assertEqual(self.flag.selected_punch_id, 101)
        self.assertEqual(self.flag.resolved_by_user_id, 9)
        self.assertIsInstance(self.flag.resolved_at, datetime)

    def test_rejects_reused_punch(self):
        ok, msg, _ = self._run({
            'am_in': 101,
            'am_out': 101,
            'pm_in': 'keep',
            'pm_out': 'keep',
        })
        self.assertFalse(ok)
        self.assertIn('only one time slot', msg)

    def test_rejects_foreign_punch(self):
        ok, msg, _ = self._run({
            'am_in': 9999,
            'am_out': 'keep',
            'pm_in': 'keep',
            'pm_out': 'keep',
        })
        self.assertFalse(ok)
        self.assertIn('does not belong', msg)

    def test_clear_slot(self):
        ok, msg, _ = self._run({
            'am_in': 'clear',
            'am_out': 'keep',
            'pm_in': 'keep',
            'pm_out': 'keep',
        })
        self.assertTrue(ok, msg)
        self.assertIsNone(self.dtr.am_in)
        self.assertEqual(self.dtr.pm_in, time(12, 40))

    def test_keep_preserves_prior_assignment_when_unused(self):
        ok, msg, _ = self._run({
            'am_in': 101,
            'am_out': 'keep',
            'pm_in': 'keep',
            'pm_out': 'keep',
        })
        self.assertTrue(ok, msg)
        self.assertEqual(self.p1240.assigned_slot, 'pm_in')
        self.assertEqual(self.p1703.assigned_slot, 'pm_out')
        # Prior am_in assignment on 09:03 was replaced by 04:39
        self.assertEqual(self.p439.assigned_slot, 'am_in')
        self.assertIsNone(self.p903.assigned_slot)

    def test_resolves_all_open_flags_for_day(self):
        flag2 = _flag('pm_in')
        ok, msg, _ = self._run(
            {
                'am_in': 101,
                'am_out': 103,
                'pm_in': 104,
                'pm_out': 105,
            },
            open_flags=[self.flag, flag2],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self.flag.status, FLAG_STATUS_RESOLVED)
        self.assertEqual(flag2.status, FLAG_STATUS_RESOLVED)
        self.assertEqual(flag2.selected_punch_id, 104)
        self.assertIn('Resolved 2 open flag', msg)

    def test_reassigns_resolved_flags(self):
        """Resolved days can clear/re-map slots without reopening flags first."""
        self.flag.status = FLAG_STATUS_RESOLVED
        self.flag.selected_punch_id = 103
        self.dtr.am_in = time(4, 48)
        self.dtr.am_out = time(9, 3)
        self.dtr.pm_in = None
        self.dtr.pm_out = time(9, 3)
        p_am = _punch(201, slot='am_in', punch_time=time(4, 48), assigned_slot='am_in', is_selected=True)
        p_out = _punch(202, slot='pm_out', punch_time=time(9, 3), assigned_slot='am_out', is_selected=True)
        ok, msg, _ = self._run(
            {
                'am_in': 201,
                'am_out': 202,
                'pm_in': 'clear',
                'pm_out': 'clear',
            },
            day_punches=[p_am, p_out],
            open_flags=[self.flag],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self.dtr.am_in, time(4, 48))
        self.assertEqual(self.dtr.am_out, time(9, 3))
        self.assertIsNone(self.dtr.pm_in)
        self.assertIsNone(self.dtr.pm_out)
        self.assertEqual(self.flag.status, FLAG_STATUS_RESOLVED)
        self.assertEqual(p_out.assigned_slot, 'am_out')
        self.assertIn('Re-assigned 1 resolved flag', msg)

    def test_inactive_batch_rejected(self):
        self.batch.status = 'undone'
        ok, msg, _ = self._run({
            'am_in': 'keep',
            'am_out': 'keep',
            'pm_in': 'keep',
            'pm_out': 'keep',
        })
        self.assertFalse(ok)
        self.assertIn('not active', msg)


if __name__ == '__main__':
    unittest.main()
