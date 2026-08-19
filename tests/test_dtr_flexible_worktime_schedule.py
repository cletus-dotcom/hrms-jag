"""Quincena Flexible Worktime list must drive late/undertime targets."""
from __future__ import annotations

import unittest
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

from app.routes import (
    _DTR_DUTY_MODELS,
    _arrangement_model_code_for_date,
    _flexible_worktime_entry_date_bounds,
    _flexible_worktime_row_for_date,
    _late_undertime_minutes_for_record,
    _pick_late_undertime_schedule,
)


def _flexi_row(**kw):
    base = dict(
        employee_id=84,
        date_start=date(2026, 7, 1),
        date_end=date(2026, 7, 15),
        duty_mode='split',
        time_in=time(5, 0),
        break_out=time(9, 0),
        break_in=time(13, 0),
        time_out=time(17, 0),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FlexibleWorktimeListScheduleTests(unittest.TestCase):
    def setUp(self):
        # Employee is NOT on the Employee.flexible_worktime flag, only the quincena list
        self.emp = SimpleNamespace(
            id=84,
            flexible_worktime=False,
            flexible_start_time=None,
            flexible_end_time=None,
            status_of_appointment='Job Order',
            nature_of_appointment='',
        )
        self.rec = SimpleNamespace(
            am_in=time(4, 39),
            am_out=time(9, 3),
            pm_in=time(12, 40),
            pm_out=time(17, 3),
        )
        self.day = date(2026, 7, 6)

    def _with_list(self, rows):
        return patch('app.routes._flexible_worktime_rows_for_employee', return_value=rows)

    def test_list_schedule_is_used_for_late_undertime(self):
        with self._with_list([_flexi_row()]):
            sched = _pick_late_undertime_schedule(self.emp, self.day)
            self.assertEqual(sched['am_in'], (5, 0))
            self.assertEqual(sched['am_out'], (9, 0))
            late, ut = _late_undertime_minutes_for_record(self.rec, sched)
        self.assertEqual(late, 0)
        self.assertEqual(ut, 0)

    def test_model_code_is_flex_when_only_on_list(self):
        with self._with_list([_flexi_row()]):
            self.assertEqual(_arrangement_model_code_for_date(self.emp, self.day), 'flex')

    def test_date_outside_list_range_has_no_flexi_row(self):
        with self._with_list([_flexi_row()]):
            self.assertIsNone(_flexible_worktime_row_for_date(self.emp, date(2026, 7, 20)))
            self.assertIsNotNone(_flexible_worktime_row_for_date(self.emp, self.day))

    def test_standard_schedule_would_flag_undertime(self):
        """Documents the bug: 5-day anchors mark 09:03 break-out as 177 min undertime."""
        late, ut = _late_undertime_minutes_for_record(self.rec, _DTR_DUTY_MODELS['5day'])
        self.assertEqual(late, 0)
        self.assertEqual(ut, 177)

    def test_straight_mode_list_row(self):
        with self._with_list([
            _flexi_row(duty_mode='straight', time_in=time(5, 0), time_out=time(13, 0)),
        ]):
            sched = _pick_late_undertime_schedule(self.emp, self.day)
            self.assertEqual(sched['duty_mode'], 'straight')
            late, ut = _late_undertime_minutes_for_record(
                SimpleNamespace(am_in=time(4, 55), am_out=None, pm_in=None, pm_out=time(13, 5)),
                sched,
            )
        self.assertEqual(late, 0)
        self.assertEqual(ut, 0)

    def test_rest_mode_list_row(self):
        with self._with_list([
            _flexi_row(
                duty_mode='rest',
                time_in=None,
                break_out=None,
                break_in=None,
                time_out=None,
            ),
        ]):
            sched = _pick_late_undertime_schedule(self.emp, self.day)
            self.assertEqual(sched['duty_mode'], 'rest')
            late, ut = _late_undertime_minutes_for_record(self.rec, sched)
            self.assertEqual(_arrangement_model_code_for_date(self.emp, self.day), 'flex')
        self.assertEqual(late, 0)
        self.assertEqual(ut, 0)


class FlexibleWorktimeEntryDateBoundsTests(unittest.TestCase):
    def test_quincena_allows_one_day_buffer_each_side(self):
        start = date(2026, 7, 1)
        end = date(2026, 7, 15)
        lo, hi = _flexible_worktime_entry_date_bounds(start, end)
        self.assertEqual(lo, date(2026, 6, 30))
        self.assertEqual(hi, date(2026, 7, 16))

    def test_second_quincena_end_of_month(self):
        start = date(2026, 7, 16)
        end = date(2026, 7, 31)
        lo, hi = _flexible_worktime_entry_date_bounds(start, end)
        self.assertEqual(lo, date(2026, 7, 15))
        self.assertEqual(hi, date(2026, 8, 1))


if __name__ == '__main__':
    unittest.main()
