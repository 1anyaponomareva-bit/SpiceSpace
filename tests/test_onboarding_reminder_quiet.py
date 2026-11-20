"""The onboarding reminder never goes out at night in her time zone."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


def utc(h: int) -> datetime:
    return datetime(2026, 11, 21, h, 0, tzinfo=timezone.utc)


class Quiet(unittest.TestCase):
    def test_russian_without_zone_uses_moscow(self):
        st = {"lang": "ru"}
        self.assertTrue(main._onboarding_reminder_quiet(st, None, utc(22)))   # 01:00 MSK
        self.assertTrue(main._onboarding_reminder_quiet(st, None, utc(5)))    # 08:00 MSK
        self.assertFalse(main._onboarding_reminder_quiet(st, None, utc(6)))   # 09:00 MSK
        self.assertFalse(main._onboarding_reminder_quiet(st, None, utc(18)))  # 21:00 MSK
        self.assertTrue(main._onboarding_reminder_quiet(st, None, utc(19)))   # 22:00 MSK

    def test_known_zone_wins(self):
        st = {"lang": "ru", "timezone": "Asia/Novosibirsk"}  # UTC+7
        self.assertTrue(main._onboarding_reminder_quiet(st, None, utc(20)))   # 03:00 there
        self.assertFalse(main._onboarding_reminder_quiet(st, None, utc(3)))   # 10:00 there
        self.assertFalse(main._onboarding_reminder_quiet({}, {"timezone": "Europe/Moscow"}, utc(9)))


if __name__ == "__main__":
    unittest.main()
