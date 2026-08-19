"""Tests for overnight flexi Out pairing (next-morning → shift-start day)."""
from __future__ import annotations

import unittest
from datetime import date, time
from types import SimpleNamespace

from app.dtr_overnight_punch_service import (
    duty_schedule_is_overnight,
    is_next_morning_out_for_overnight,
    plan_overnight_out_moves_for_employee,
)


def _sched(tin, tout, mode='straight'):
    th, tm = tin
    oh, om = tout
    return {
        'duty_mode': mode,
        'am_in': (th, tm),
        'am_out': (th, tm) if mode == 'straight' else (12, 0),
        'pm_in': (oh, om) if mode == 'straight' else (13, 0),
        'pm_out': (oh, om),
    }


def _emp():
    return SimpleNamespace(
        id=57,
        employee_id='4757',
        directory_name='CUADRA, VINCENT',
        full_name='VINCENT CUADRA',
    )


def _rec(d, *, am_in=None, am_out=None, pm_in=None, pm_out=None):
    return SimpleNamespace(
        employee_id=57,
        record_date=d,
        am_in=am_in,
        am_out=am_out,
        pm_in=pm_in,
        pm_out=pm_out,
    )


class OvernightPairTests(unittest.TestCase):
    def test_overnight_detection_midnight_and_dawn(self):
        self.assertTrue(duty_schedule_is_overnight(_sched((16, 0), (0, 0))))
        self.assertTrue(duty_schedule_is_overnight(_sched((22, 0), (6, 0))))
        self.assertFalse(duty_schedule_is_overnight(_sched((8, 0), (17, 0))))

    def test_next_morning_window(self):
        s1600 = _sched((16, 0), (0, 0))
        self.assertTrue(is_next_morning_out_for_overnight(time(0, 1), s1600))
        self.assertTrue(is_next_morning_out_for_overnight(time(4, 0), s1600))
        self.assertFalse(is_next_morning_out_for_overnight(time(16, 0), s1600))
        self.assertFalse(is_next_morning_out_for_overnight(time(17, 0), s1600))

        s2200 = _sched((22, 0), (6, 0))
        self.assertTrue(is_next_morning_out_for_overnight(time(4, 1), s2200))
        self.assertTrue(is_next_morning_out_for_overnight(time(6, 0), s2200))
        self.assertFalse(is_next_morning_out_for_overnight(time(9, 0), s2200))
        self.assertFalse(is_next_morning_out_for_overnight(time(22, 30), s2200))

    def test_plan_moves_4757_style_chain(self):
        """Jul 2 missing out ← Jul 3 00:01; Jul 3 missing out ← Jul 4 04:01."""
        flexi = {
            date(2026, 7, 2): _sched((16, 0), (0, 0)),
            date(2026, 7, 3): _sched((22, 0), (6, 0)),
        }
        dtr = {
            date(2026, 7, 2): _rec(date(2026, 7, 2), am_in=time(15, 30)),
            date(2026, 7, 3): _rec(date(2026, 7, 3), am_in=time(18, 34), pm_out=time(0, 1)),
            date(2026, 7, 4): _rec(date(2026, 7, 4), am_in=time(18, 28), pm_out=time(4, 1)),
        }
        moves = plan_overnight_out_moves_for_employee(
            employee=_emp(),
            flexi_by_date=flexi,
            dtr_by_date=dtr,
            range_start=date(2026, 7, 2),
            range_end=date(2026, 7, 3),
        )
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves[0].start_date, date(2026, 7, 2))
        self.assertEqual(moves[0].source_date, date(2026, 7, 3))
        self.assertEqual(moves[0].out_time, time(0, 1))
        self.assertEqual(moves[1].start_date, date(2026, 7, 3))
        self.assertEqual(moves[1].source_date, date(2026, 7, 4))
        self.assertEqual(moves[1].out_time, time(4, 1))

    def test_no_move_when_start_day_already_has_out(self):
        flexi = {date(2026, 7, 2): _sched((16, 0), (0, 0))}
        dtr = {
            date(2026, 7, 2): _rec(date(2026, 7, 2), am_in=time(16, 0), pm_out=time(0, 5)),
            date(2026, 7, 3): _rec(date(2026, 7, 3), pm_out=time(0, 1)),
        }
        moves = plan_overnight_out_moves_for_employee(
            employee=_emp(),
            flexi_by_date=flexi,
            dtr_by_date=dtr,
            range_start=date(2026, 7, 2),
            range_end=date(2026, 7, 2),
        )
        self.assertEqual(moves, [])

    def test_no_move_afternoon_out_on_next_day(self):
        flexi = {date(2026, 7, 2): _sched((16, 0), (0, 0))}
        dtr = {
            date(2026, 7, 2): _rec(date(2026, 7, 2), am_in=time(16, 0)),
            date(2026, 7, 3): _rec(date(2026, 7, 3), pm_out=time(17, 0)),
        }
        moves = plan_overnight_out_moves_for_employee(
            employee=_emp(),
            flexi_by_date=flexi,
            dtr_by_date=dtr,
            range_start=date(2026, 7, 2),
            range_end=date(2026, 7, 2),
        )
        self.assertEqual(moves, [])


if __name__ == '__main__':
    unittest.main()
