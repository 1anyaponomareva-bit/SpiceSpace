"""Day 8: if the new-week goal question went unanswered, the morning re-asks it."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import onboarding_flow as ob  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402

PROFILE = {"language_code": "ru", "timezone": "UTC"}


class _Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)


def _ns(onboarding: dict, claim: bool = True) -> dict:
    class _Db:
        def claim_send_slot(self, cid, field, value):
            return claim

    marked: list[str] = []
    ns = {
        "ob": ob,
        "onboarding": onboarding,
        "histories": {},
        "datetime": datetime,
        "timezone": timezone,
        "db_store": _Db(),
        "log": __import__("logging").getLogger("t"),
        "_profile_timezone_name": lambda p: "UTC",
        "_weekly_flow_started_local_date": lambda st, tz: st.get("_day"),
        "_mark_morning_slot_sent": lambda cid, p, today, up: marked.append(today),
        "_user_lang": lambda p: "ru",
        "sanitize_bot_reply": lambda t: t,
        "_webapp_keyboard": lambda cid, lang: None,
        "is_telegram_block_error": lambda e: False,
        "_mark_telegram_blocked": lambda cid, p: None,
        "_marked": marked,
    }
    return _main_functions(
        {"_unanswered_new_week_flow", "_new_week_nudge_turns", "_deliver_new_week_goal_nudge"},
        ns,
    )


def _st(day: str, turns=None, from_week_start=True) -> dict:
    return {
        "step": ob.OB_CHANGE_WEEKLY,
        "from_week_start": from_week_start,
        "_day": day,
        "weekly_turns": turns
        if turns is not None
        else [{"role": "assistant", "content": ob.ob_text("weekly_dialog_opening", "ru")}],
    }


class NewWeekMorningTests(unittest.TestCase):
    def test_detects_only_yesterdays_week_start_flow(self) -> None:
        onb = {1: _st("2026-11-10"), 2: _st("2026-11-11"), 3: _st("2026-11-10", from_week_start=False)}
        ns = _ns(onb)
        f = ns["_unanswered_new_week_flow"]
        self.assertIsNotNone(f(1, PROFILE, "2026-11-11"))
        self.assertIsNone(f(2, PROFILE, "2026-11-11"))  # same evening — not yet
        self.assertIsNone(f(3, PROFILE, "2026-11-11"))  # manual change — old logic
        self.assertIsNone(f(4, PROFILE, "2026-11-11"))

    def test_unanswered_gets_full_question(self) -> None:
        ns = _ns({})
        turns, text = ns["_new_week_nudge_turns"](_st("x")["weekly_turns"], "ru")
        self.assertTrue(text.startswith("Доброе утро"))
        self.assertIn("на этой неделе", text)
        self.assertEqual(turns, [{"role": "assistant", "content": text}])

    def test_half_answered_repeats_last_question(self) -> None:
        ns = _ns({})
        prev = [
            {"role": "assistant", "content": "Что хочешь успеть?"},
            {"role": "user", "content": "Запустить рекламу"},
            {"role": "assistant", "content": "Сколько постов в неделю реально?"},
        ]
        turns, text = ns["_new_week_nudge_turns"](prev, "ru")
        self.assertIn("не договорили", text)
        self.assertTrue(text.endswith("Сколько постов в неделю реально?"))
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[-1]["content"], text)
        self.assertEqual(turns[1]["content"], "Запустить рекламу")

    def test_deliver_turns_flow_into_same_day_change(self) -> None:
        onb = {1: _st("2026-11-10")}
        ns = _ns(onb)
        bot = _Bot()
        ok = asyncio.run(ns["_deliver_new_week_goal_nudge"](
            bot, 1, dict(PROFILE), onb[1], today="2026-11-11", user_profiles={}
        ))
        self.assertTrue(ok)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("цель на новую неделю", bot.sent[0])
        self.assertFalse(onb[1]["from_week_start"])
        self.assertEqual(onb[1]["weekly_turns"][-1]["content"], bot.sent[0])
        self.assertEqual(ns["_marked"], ["2026-11-11"])

    def test_deliver_skips_when_slot_taken(self) -> None:
        onb = {1: _st("2026-11-10")}
        ns = _ns(onb, claim=False)
        bot = _Bot()
        ok = asyncio.run(ns["_deliver_new_week_goal_nudge"](
            bot, 1, dict(PROFILE), onb[1], today="2026-11-11", user_profiles={}
        ))
        self.assertFalse(ok)
        self.assertEqual(bot.sent, [])
        self.assertTrue(onb[1]["from_week_start"])


if __name__ == "__main__":
    unittest.main()
