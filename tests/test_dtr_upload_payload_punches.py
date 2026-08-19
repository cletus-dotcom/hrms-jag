"""Upload payload must carry raw punches so duplicate-slot flags can be created."""
from __future__ import annotations

import unittest

from app.dtr_parse import parse_dtr_dat_file
from app.routes import _dtr_save_payload_from_upload_rows


SAMPLE_ZK = """\
25888\t2026-07-06 04:39:14\t102\t0\t1\t0
25888\t2026-07-06 04:39:21\t102\t0\t1\t0
25888\t2026-07-06 09:03:03\t102\t0\t1\t0
25888\t2026-07-06 12:40:43\t102\t3\t1\t0
25888\t2026-07-06 17:03:25\t102\t1\t1\t0
"""


class UploadPayloadPunchTests(unittest.TestCase):
    def setUp(self):
        parsed = parse_dtr_dat_file(SAMPLE_ZK)
        self.payload = _dtr_save_payload_from_upload_rows(parsed)
        self.row = self.payload[0]

    def test_payload_keeps_selected_slot_times(self):
        self.assertEqual(self.row['employee_id'], '25888')
        self.assertEqual(self.row['date'], '2026-07-06')
        self.assertEqual(self.row['check_in'], '09:03:03')
        self.assertEqual(self.row['break_in'], '12:40:43')
        self.assertEqual(self.row['check_out'], '17:03:25')

    def test_payload_carries_all_raw_punches(self):
        punches = self.row['punches']
        self.assertEqual(len(punches), 5)
        am_in_times = [p['time'] for p in punches if p['slot'] == 'am_in']
        self.assertEqual(am_in_times, ['04:39:14', '04:39:21', '09:03:03'])

    def test_payload_marks_duplicate_slot(self):
        self.assertTrue(self.row['has_duplicate_punches'])
        self.assertEqual(self.row['duplicate_slots'], ['am_in'])

    def test_payload_is_json_serializable(self):
        import json

        json.dumps(self.payload)


if __name__ == '__main__':
    unittest.main()
