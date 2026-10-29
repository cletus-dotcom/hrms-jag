"""Workweek calendar: Friday holidays, ISO weeks, leave charge remarks."""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from unittest.mock import patch

from app.portal_config import is_admin_or_hr_role
from app.ph_holidays import (
    friday_holidays_touching_year,
    iso_week_friday,
    iso_week_monday,
    philippines_holidays_for_year,
)
from app.workweek_calendar_service import apply_five_day_remark, rescale_full_day_charge


class IsoWeekAndHolidayTests(unittest.TestCase):
    def test_january_1_2027_is_friday_in_december_week(self):
        nye = date(2027, 1, 1)
        self.assertEqual(nye.weekday(), 4)
        self.assertEqual(iso_week_monday(nye), date(2026, 12, 28))
        self.assertEqual(iso_week_friday(date(2026, 12, 28)), nye)

    def test_new_year_friday_flags_both_2026_and_2027(self):
        dates_2026 = {h.date for h in friday_holidays_touching_year(2026)}
        dates_2027 = {h.date for h in friday_holidays_touching_year(2027)}
        self.assertIn(date(2027, 1, 1), dates_2026)
        self.assertIn(date(2027, 1, 1), dates_2027)
        names = {h.date: h.name for h in friday_holidays_touching_year(2026)}
        self.assertEqual(names[date(2027, 1, 1)], "New Year's Day")

    def test_independence_day_2026_is_friday(self):
        hols = {h.date: h for h in philippines_holidays_for_year(2026)}
        self.assertEqual(date(2026, 6, 12).weekday(), 4)
        self.assertEqual(hols[date(2026, 6, 12)].kind, 'regular')
        self.assertIn(date(2026, 6, 12), {h.date for h in friday_holidays_touching_year(2026)})

    def test_christmas_2026_is_friday(self):
        self.assertEqual(date(2026, 12, 25).weekday(), 4)
        self.assertIn(date(2026, 12, 25), {h.date for h in friday_holidays_touching_year(2026)})


class WorkweekCalendarRoleTests(unittest.TestCase):
    def test_only_admin_and_hr_roles_may_edit(self):
        with patch('app.portal_config.is_portal_admin_role', return_value=False):
            self.assertTrue(is_admin_or_hr_role('admin'))
            self.assertTrue(is_admin_or_hr_role('HR'))
            self.assertFalse(is_admin_or_hr_role('manager'))
            self.assertFalse(is_admin_or_hr_role('employee'))
            self.assertFalse(is_admin_or_hr_role('payroll_maker'))
            self.assertFalse(is_admin_or_hr_role('dtr_uploader'))


class LeaveChargeRemarkTests(unittest.TestCase):
    def test_rescale_full_day_only(self):
        self.assertEqual(rescale_full_day_charge(Decimal('1.250'), Decimal('1.000')), Decimal('1.000'))
        self.assertEqual(rescale_full_day_charge(Decimal('1.000'), Decimal('1.250')), Decimal('1.250'))
        self.assertEqual(rescale_full_day_charge(Decimal('-1.250'), Decimal('1.000')), Decimal('-1.000'))
        self.assertEqual(rescale_full_day_charge(Decimal('0.500'), Decimal('1.000')), Decimal('0.500'))

    def test_flag_remark_keeps_existing_tags(self):
        tagged = apply_five_day_remark('leave_request_id=12', True)
        self.assertEqual(tagged, 'leave_request_id=12; 5-day workweek')
        self.assertEqual(apply_five_day_remark(tagged, True), tagged)
        self.assertEqual(apply_five_day_remark(tagged, False), 'leave_request_id=12')
        self.assertEqual(apply_five_day_remark(None, True), '5-day workweek')
        self.assertIsNone(apply_five_day_remark(None, False))


if __name__ == '__main__':
    unittest.main()
