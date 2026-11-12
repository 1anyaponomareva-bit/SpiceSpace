"""Owner notifications: sent to the channel the owner added the bot to; never raise."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_lang_paths import _main_functions  # noqa: E402


class _Bot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise RuntimeError("blocked")
        self.sent.append((chat_id, text))


def _ns():
    return _main_functions(
        {"admin_notify_chat_id", "notify_admin", "handle_bot_added_to_chat", "_who"},
        {
            "os": os,
            "suppress": suppress,
            "ADMIN_TELEGRAM_ID": 42,
            "ADMIN_NOTIFY_CHAT_ID_DEFAULT": "",
            "_admin_notify_chat": {},
            "log": __import__("logging").getLogger("t"),
        },
    )


def _member_update(by, chat_type="channel", status="administrator"):
    upd = SimpleNamespace(
        chat=SimpleNamespace(id=-100777, type=chat_type, title="Спейс — уведомления"),
        from_user=SimpleNamespace(id=by),
        new_chat_member=SimpleNamespace(status=status),
    )
    return SimpleNamespace(my_chat_member=upd)


class AdminNotifyTests(unittest.TestCase):
    def test_owner_adds_bot_then_notices_go_there(self) -> None:
        ns = _ns()
        bot = _Bot()
        ctx = SimpleNamespace(bot=bot)
        asyncio.run(ns["handle_bot_added_to_chat"](_member_update(by=99), ctx))
        self.assertEqual(ns["admin_notify_chat_id"](), "")  # someone else: ignored
        asyncio.run(ns["handle_bot_added_to_chat"](_member_update(by=42), ctx))
        self.assertEqual(ns["admin_notify_chat_id"](), "-100777")
        asyncio.run(ns["notify_admin"](bot, "🆕 Новый человек: Оля"))
        self.assertEqual(bot.sent[-1], (-100777, "🆕 Новый человек: Оля"))

    def test_no_channel_or_error_is_silent(self) -> None:
        ns = _ns()
        asyncio.run(ns["notify_admin"](_Bot(), "x"))  # no channel yet
        ns["_admin_notify_chat"]["id"] = "-1"
        asyncio.run(ns["notify_admin"](_Bot(fail=True), "x"))  # must not raise

    def test_who(self) -> None:
        ns = _ns()
        u = SimpleNamespace(first_name="Оля", last_name=None, username="olya")
        self.assertEqual(ns["_who"](u), "Оля (@olya)")


if __name__ == "__main__":
    unittest.main()
