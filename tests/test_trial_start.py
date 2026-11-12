"""Free days for people who started before paid access count from PAYMENTS_START_DATE."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_lang_paths import _main_functions  # noqa: E402


def _ns(start: str) -> dict:
    return _main_functions(
        {"_trial_start_raw", "_profile_in_trial", "_trial_day"},
        {
            "PAYMENTS_START_DATE": start,
            "TRIAL_DAYS": 4,
            "date": date,
            "_profile_local_date": lambda p: date(2026, 12, 1),
        },
    )


class TrialStartTests(unittest.TestCase):
    def test_old_user_counts_from_payments_start(self) -> None:
        ns = _ns("2026-11-20")
        old = {"trial_start_date": "2026-09-01"}
        self.assertEqual(ns["_trial_start_raw"](old), "2026-11-20")
        self.assertTrue(ns["_profile_in_trial"](old, date(2026, 11, 23)))   # day 4
        self.assertFalse(ns["_profile_in_trial"](old, date(2026, 11, 24)))  # day 5
        self.assertEqual(ns["_trial_day"](old, date(2026, 11, 20)), 1)
        # before the start date nobody is cut off
        self.assertTrue(ns["_profile_in_trial"](old, date(2026, 11, 15)))

    def test_new_user_counts_from_own_start(self) -> None:
        ns = _ns("2026-11-20")
        new = {"trial_start_date": "2026-11-25"}
        self.assertEqual(ns["_trial_start_raw"](new), "2026-11-25")
        self.assertFalse(ns["_profile_in_trial"](new, date(2026, 11, 29)))

    def test_no_start_date_keeps_own(self) -> None:
        ns = _ns("")
        self.assertEqual(ns["_trial_start_raw"]({"cycle_start_date": "2026-09-01"}), "2026-09-01")


if __name__ == "__main__":
    unittest.main()
