from __future__ import annotations

import unittest
from datetime import date, timedelta, timezone

from app.core.time_utils import LONDON_TZ_AVAILABLE, london_0801_utc


class TimeUtilsTests(unittest.TestCase):
    def test_london_0801_non_dst_maps_to_0801_utc(self):
        if not LONDON_TZ_AVAILABLE:
            self.skipTest("Europe/London timezone unavailable in environment")

        value = london_0801_utc(date(2026, 1, 15))
        self.assertEqual(value.utcoffset(), timedelta(0))
        self.assertEqual((value.hour, value.minute), (8, 1))
        self.assertEqual(value.tzinfo, timezone.utc)

    def test_london_0801_dst_maps_to_0701_utc(self):
        if not LONDON_TZ_AVAILABLE:
            self.skipTest("Europe/London timezone unavailable in environment")

        value = london_0801_utc(date(2026, 7, 15))
        self.assertEqual(value.utcoffset(), timedelta(0))
        self.assertEqual((value.hour, value.minute), (7, 1))
        self.assertEqual(value.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
