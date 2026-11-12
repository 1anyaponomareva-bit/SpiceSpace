"""Free-days texts: onboarding intro, announcement for earlier users, day 3 and day 4 evenings."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lava_pay  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402

import ast  # noqa: E402

_src = (ROOT / "main.py").read_text(encoding="utf-8")
_TRIAL_TEXTS = None
for _node in ast.parse(_src).body:
    if isinstance(_node, ast.Assign) and any(getattr(t, "id", "") == "_TRIAL_TEXTS" for t in _node.targets):
        _TRIAL_TEXTS = ast.literal_eval(_node.value)


class _Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)


class _Db:
    def __init__(self) -> None:
        self.flags: set[str] = set()

    def cycle_flag_sent(self, profile, key):
        return key in self.flags

    def mark_cycle_flag(self, cid, key):
        self.flags.add(key)
        return None


class _Ob:
    @staticmethod
    def _friend_word(lang):
        return "подруга"


def _ns(today: date, start_date: str = "2026-11-13") -> dict:
    ns = {
        "_TRIAL_TEXTS": _TRIAL_TEXTS,
        "SUBSCRIPTIONS_ENABLED": True,
        "PAYMENTS_START_DATE": start_date,
        "TRIAL_DAYS": 4,
        "date": date,
        "timedelta": timedelta,
        "lava_pay": lava_pay,
        "db_store": _Db(),
        "ob": _Ob,
        "log": __import__("logging").getLogger("t"),
        "ui_lang": lambda p: "ru",
        "_profile_local_date": lambda p: today,
        "_profile_is_premium_active": lambda p, d: False,
        "_subscription_offer_keyboard": lambda cid, lang: None,
    }
    return _main_functions(
        {
            "_trial_text", "trial_intro_text", "_trial_prices", "_maybe_send_payments_announcement",
            "_maybe_send_trial_evening_message", "_trial_start_raw", "_trial_day", "_lava_end_text",
        },
        ns,
    )


class TrialTextsTests(unittest.TestCase):
    def test_space_speaks_feminine_no_masculine(self) -> None:
        ru = " ".join(_TRIAL_TEXTS["ru"].values())
        for bad in ("бесплатный:", "напомнил", "написал", "был с тобой", "Спейс полностью бесплатный"):
            self.assertNotIn(bad, ru)

    def test_intro_only_for_new_trial(self) -> None:
        ns = _ns(date(2026, 11, 20))
        self.assertIn("4 дня", ns["trial_intro_text"]({}, "ru", True))
        self.assertEqual(ns["trial_intro_text"]({}, "ru", False), "")

    def test_announcement_once_for_earlier_users(self) -> None:
        ns = _ns(date(2026, 11, 13))
        bot = _Bot()
        old = {"name": "Аня", "trial_start_date": "2026-09-01"}
        asyncio.run(ns["_maybe_send_payments_announcement"](bot, 1, old, {}))
        asyncio.run(ns["_maybe_send_payments_announcement"](bot, 1, old, {}))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("до 16.11.2026 включительно", bot.sent[0])
        new = {"name": "Оля", "trial_start_date": "2026-11-13"}
        asyncio.run(ns["_maybe_send_payments_announcement"](bot, 2, new, {}))
        self.assertEqual(len(bot.sent), 1)

    def test_no_announcement_before_start(self) -> None:
        ns = _ns(date(2026, 11, 12))
        bot = _Bot()
        asyncio.run(ns["_maybe_send_payments_announcement"](bot, 1, {"trial_start_date": "2026-09-01"}, {}))
        self.assertEqual(bot.sent, [])

    def test_day3_and_day4_evenings(self) -> None:
        prof = {"name": "Аня", "trial_start_date": "2026-11-13", "main_goal": "Запустить курс"}
        for today, expect in (
            (date(2026, 11, 14), None),
            (date(2026, 11, 15), "завтра последний бесплатный день"),
            (date(2026, 11, 16), "бесплатные 4 дня закончились"),
            (date(2026, 11, 17), None),
        ):
            ns = _ns(today)
            bot = _Bot()
            asyncio.run(ns["_maybe_send_trial_evening_message"](bot, 1, prof, today, {}))
            if expect is None:
                self.assertEqual(bot.sent, [], today)
            else:
                self.assertIn(expect, bot.sent[0])
                self.assertTrue(bot.sent[0].startswith("Аня,"))
        ns = _ns(date(2026, 11, 15))
        bot = _Bot()
        asyncio.run(ns["_maybe_send_trial_evening_message"](bot, 1, prof, date(2026, 11, 15), {}))
        self.assertIn("590 ₽ в месяц или 1490 ₽ за 3 месяца", bot.sent[0])
        ns = _ns(date(2026, 11, 16))
        bot = _Bot()
        asyncio.run(ns["_maybe_send_trial_evening_message"](bot, 1, prof, date(2026, 11, 16), {}))
        self.assertIn("«Запустить курс»", bot.sent[0])


if __name__ == "__main__":
    unittest.main()


class AnnouncementDayBeforeTests(unittest.TestCase):
    def test_user_onboarded_day_before_gets_no_announcement(self) -> None:
        ns = _ns(date(2026, 11, 13))
        bot = _Bot()
        prof = {"name": "Аня", "trial_start_date": "2026-11-12"}
        asyncio.run(ns["_maybe_send_payments_announcement"](bot, 1, prof, {}))
        self.assertEqual(bot.sent, [])
