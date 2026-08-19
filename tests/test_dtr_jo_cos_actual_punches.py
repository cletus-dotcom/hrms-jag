"""JO/COS DTR must store actual biometric In/Out, not generated 8 AM / 5 PM stamps."""
from __future__ import annotations

import unittest
from datetime import time
from types import SimpleNamespace

from app.dtr_jo_cos_actual_punch_service import (
    employee_is_jo_cos,
    is_generated_duty_stamp_for_slot,
    merge_prefer_actual_over_duty_stamp,
    parse_time_from_source_timestamp,
    punch_actual_time,
    row_is_orphan_generated_duty_stamp,
)
from app.dtr_parse import parse_dtr_dat_file


class JoCosActualPunchHelperTests(unittest.TestCase):
    def test_employee_is_jo_cos(self):
        self.assertTrue(employee_is_jo_cos(SimpleNamespace(
            status_of_appointment='Job Order', nature_of_appointment='Original',
        )))
        self.assertTrue(employee_is_jo_cos(SimpleNamespace(
            status_of_appointment='Contract of Service', nature_of_appointment='',
        )))
        self.assertFalse(employee_is_jo_cos(SimpleNamespace(
            status_of_appointment='Permanent', nature_of_appointment='',
        )))

    def test_generated_in_out_stamps(self):
        self.assertTrue(is_generated_duty_stamp_for_slot(time(8, 0), 'am_in'))
        self.assertTrue(is_generated_duty_stamp_for_slot(time(7, 0), 'am_in'))
        self.assertTrue(is_generated_duty_stamp_for_slot(time(17, 0), 'pm_out'))
        self.assertTrue(is_generated_duty_stamp_for_slot(time(18, 0), 'pm_out'))
        self.assertFalse(is_generated_duty_stamp_for_slot(time(7, 50), 'am_in'))
        self.assertFalse(is_generated_duty_stamp_for_slot(time(18, 5), 'pm_out'))
        self.assertFalse(is_generated_duty_stamp_for_slot(time(8, 0, 13), 'am_in'))

    def test_merge_replaces_stamp_with_actual(self):
        actual_in = time(7, 50, 13)
        self.assertEqual(
            merge_prefer_actual_over_duty_stamp(time(8, 0), actual_in, 'am_in'),
            actual_in,
        )
        actual_out = time(18, 5, 22)
        self.assertEqual(
            merge_prefer_actual_over_duty_stamp(time(17, 0), actual_out, 'pm_out'),
            actual_out,
        )
        kept = time(7, 48)
        self.assertEqual(
            merge_prefer_actual_over_duty_stamp(kept, time(7, 50), 'am_in'),
            kept,
        )

    def test_source_timestamp_keeps_seconds(self):
        t = parse_time_from_source_timestamp('2026-08-03 07:50:13', time(7, 50))
        self.assertEqual(t, time(7, 50, 13))
        punch = SimpleNamespace(source_timestamp='2026-08-06 18:05:22', punch_time=time(18, 5))
        self.assertEqual(punch_actual_time(punch), time(18, 5, 22))

    def test_orphan_generated_stamp_has_no_breaks(self):
        rec = SimpleNamespace(am_in=time(8, 0), am_out=None, pm_in=None, pm_out=time(17, 0))
        self.assertTrue(row_is_orphan_generated_duty_stamp(rec))
        rec2 = SimpleNamespace(am_in=time(5, 0), am_out=None, pm_in=None, pm_out=time(17, 0))
        self.assertFalse(row_is_orphan_generated_duty_stamp(rec2))
        rec3 = SimpleNamespace(am_in=time(8, 0), am_out=time(12, 0), pm_in=None, pm_out=time(17, 0))
        self.assertFalse(row_is_orphan_generated_duty_stamp(rec3))


class ParseKeepsBiometricSecondsTests(unittest.TestCase):
    def test_zk_time_includes_seconds(self):
        content = "1442\t2026-08-03 07:50:13\t1\t0\t0\t0\t0\n1442\t2026-08-03 17:05:41\t1\t1\t0\t0\t0\n"
        rows = parse_dtr_dat_file(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['check_in'], '07:50:13')
        self.assertEqual(rows[0]['check_out'], '17:05:41')
        self.assertEqual(rows[0]['punches'][0]['source_timestamp'], '2026-08-03 07:50:13')


if __name__ == '__main__':
    unittest.main()
