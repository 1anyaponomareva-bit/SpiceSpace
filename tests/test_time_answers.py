"""Time answers without a colon (21.11: «В 21 40» threw her out of onboarding)."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402


class ParseTime(unittest.TestCase):
    def test_without_colon(self):
        for raw, ctx, want in (
            ("В 21 40", "evening", "21:40"),
            ("21 40", "evening", "21:40"),
            ("21-40", "evening", "21:40"),
            ("21ч40", "evening", "21:40"),
            ("в 9 30", "evening", "21:30"),
            ("в 9 30", "morning", "09:30"),
            ("21:40", "evening", "21:40"),
            ("9", "morning", "09:00"),
            ("в 10 вечера", "evening", "22:00"),
            ("полдесятого вечера", "evening", "21:30"),
            ("полдесятого", "morning", "09:30"),
        ):
            self.assertEqual(ob.parse_time_nl(raw, ctx), want, raw)


class TimeStepKeepsOnboarding(unittest.TestCase):
    CID = 8801

    def tearDown(self):
        dict.pop(main.onboarding, self.CID, None)
        main.flow_mismatch_streak.pop(self.CID, None)

    def _escape(self, raw):
        exited = []

        async def fake_exit(update, cid, raw, prof):
            exited.append(raw)

        with mock.patch.object(main, "_exit_flow_and_reply", fake_exit):
            used = asyncio.run(main._try_flow_escape(mock.Mock(), self.CID, raw, {}))
        return used, exited

    def test_unreadable_time_twice_does_not_exit(self):
        dict.__setitem__(main.onboarding, self.CID, {"step": ob.OB_EVENING_TIME, "name": "A"})
        for raw in ("когда удобно", "не знаю"):
            used, exited = self._escape(raw)
            self.assertFalse(used, raw)
            self.assertEqual(exited, [])

    def test_time_with_space_matches(self):
        dict.__setitem__(main.onboarding, self.CID, {"step": ob.OB_EVENING_TIME, "name": "A"})
        self.assertTrue(main._structured_flow_reply_matches("time_setup", "В 21 40", self.CID, {}))

    def test_stop_still_exits(self):
        dict.__setitem__(main.onboarding, self.CID, {"step": ob.OB_EVENING_TIME, "name": "A"})
        used, exited = self._escape("стоп")
        self.assertTrue(used)


if __name__ == "__main__":
    unittest.main()
