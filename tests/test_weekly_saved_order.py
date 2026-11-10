"""After a weekly goal is saved, the question message must be the last one in the chat."""

from __future__ import annotations

import asyncio
import logging
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import onboarding_flow as ob  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402


class _Bot:
    def __init__(self, sent: list[str]) -> None:
        self.sent = sent

    async def send_message(self, chat_id: int, text: str, **_kw) -> None:
        self.sent.append(text)


def _ns(sent: list[str], due_now: bool) -> dict:
    async def post_task(bot, cid, profile, model_names):
        sent.append("TASK: что берёшь?")

    async def post_morning(bot, cid, profile, model_names):
        sent.append("MORNING: что берёшь?")

    ns = {
        "ob": ob,
        "log": logging.getLogger("t"),
        "datetime": datetime,
        "_user_lang": lambda p: "ru",
        "_profile_timezone_name": lambda p: "UTC",
        "_zone_or_default": lambda n: ZoneInfo("UTC"),
        "_morning_slot_due_now": lambda p, n: due_now,
        "_post_today_task_after_weekly": post_task,
        "_post_weekly_goal_morning": post_morning,
    }
    return _main_functions({"_after_weekly_goal_saved"}, ns)


class WeeklySavedOrderTests(unittest.TestCase):
    def _run(self, due_now: bool, from_week_start: bool) -> tuple[list[str], str]:
        sent: list[str] = []
        ns = _ns(sent, due_now)
        ret = asyncio.run(
            ns["_after_weekly_goal_saved"](_Bot(sent), 1, {}, [], "Оформить соцсети", from_week_start)
        )
        return sent, ret

    def test_confirm_first_then_task(self) -> None:
        sent, ret = self._run(due_now=False, from_week_start=False)
        self.assertEqual(ret, "")
        self.assertTrue(sent[0].startswith("Записала"))
        self.assertTrue(sent[-1].startswith("TASK"))

    def test_morning_now_order(self) -> None:
        sent, ret = self._run(due_now=True, from_week_start=True)
        self.assertEqual(ret, "")
        self.assertTrue(sent[0].startswith("Записала"))
        self.assertTrue(sent[-1].startswith("MORNING"))

    def test_evening_week_start_returns_text(self) -> None:
        sent, ret = self._run(due_now=False, from_week_start=True)
        self.assertEqual(sent, [])
        self.assertTrue(ret.startswith("Записала"))


if __name__ == "__main__":
    unittest.main()
