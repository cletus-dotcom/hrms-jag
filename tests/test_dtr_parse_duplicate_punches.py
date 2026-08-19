"""Unit tests for DTR duplicate-punch parsing (no DB required)."""
from __future__ import annotations

import unittest

from app.dtr_parse import duplicate_slots_from_punches, parse_dtr_dat_file


SAMPLE_ZK = """\
1001\t2026-07-01 08:01:00\t1\t0\t0\t0\t0
1001\t2026-07-01 08:05:00\t1\t0\t0\t0\t0
1001\t2026-07-01 12:00:00\t1\t2\t0\t0\t0
1001\t2026-07-01 13:00:00\t1\t3\t0\t0\t0
1001\t2026-07-01 17:00:00\t1\t1\t0\t0\t0
1002\t2026-07-01 08:00:00\t1\t0\t0\t0\t0
1002\t2026-07-01 08:00:00\t1\t0\t0\t0\t0
1002\t2026-07-01 17:02:00\t1\t1\t0\t0\t0
"""


class ParseDtrDuplicatePunchTests(unittest.TestCase):
    def test_last_wins_keeps_selected_slot_values(self):
        rows = parse_dtr_dat_file(SAMPLE_ZK)
        by_emp = {r['employee_id']: r for r in rows}
        self.assertEqual(by_emp['1001']['check_in'], '08:05:00')
        self.assertEqual(by_emp['1001']['break_out'], '12:00:00')
        self.assertEqual(by_emp['1001']['break_in'], '13:00:00')
        self.assertEqual(by_emp['1001']['check_out'], '17:00:00')

    def test_retains_all_raw_punches_in_source_order(self):
        rows = parse_dtr_dat_file(SAMPLE_ZK)
        emp = next(r for r in rows if r['employee_id'] == '1001')
        self.assertEqual(len(emp['punches']), 5)
        self.assertEqual([p['time'] for p in emp['punches']], [
            '08:01:00', '08:05:00', '12:00:00', '13:00:00', '17:00:00',
        ])
        self.assertEqual([p['slot'] for p in emp['punches']], [
            'am_in', 'am_in', 'am_out', 'pm_in', 'pm_out',
        ])
        self.assertEqual(emp['punches'][0]['line_no'], 1)
        self.assertEqual(emp['punches'][1]['sequence_no'], 2)

    def test_flags_same_slot_multiple_including_identical_timestamps(self):
        rows = parse_dtr_dat_file(SAMPLE_ZK)
        emp1 = next(r for r in rows if r['employee_id'] == '1001')
        emp2 = next(r for r in rows if r['employee_id'] == '1002')
        self.assertTrue(emp1['has_duplicate_punches'])
        self.assertEqual(emp1['duplicate_slots'], ['am_in'])
        self.assertTrue(emp2['has_duplicate_punches'])
        self.assertEqual(emp2['duplicate_slots'], ['am_in'])
        dup = duplicate_slots_from_punches(emp2['punches'])
        self.assertEqual(len(dup['am_in']), 2)
        self.assertEqual(dup['am_in'][0]['time'], '08:00:00')
        self.assertEqual(dup['am_in'][1]['time'], '08:00:00')

    def test_different_slots_are_not_flagged(self):
        content = (
            "2001\t2026-07-02 08:00:00\t1\t0\t0\t0\t0\n"
            "2001\t2026-07-02 12:00:00\t1\t2\t0\t0\t0\n"
            "2001\t2026-07-02 13:00:00\t1\t3\t0\t0\t0\n"
            "2001\t2026-07-02 17:00:00\t1\t1\t0\t0\t0\n"
        )
        rows = parse_dtr_dat_file(content)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['has_duplicate_punches'])
        self.assertEqual(rows[0]['duplicate_slots'], [])
        self.assertEqual(len(rows[0]['punches']), 4)


if __name__ == '__main__':
    unittest.main()
