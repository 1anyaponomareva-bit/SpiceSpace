"""Pause between bot messages sent one after another to the same chat.

Several messages in a row (e.g. the end of onboarding) used to land at once, which
is hard to read. Every send_message goes through PacedBot, so no call site changes:
a message to a chat waits until GAP_SECONDS have passed since the previous one.
"""

from __future__ import annotations

import asyncio
import time

from telegram.ext import ExtBot

GAP_SECONDS = 1.5

_next_slot: dict[object, float] = {}
_locks: dict[object, asyncio.Lock] = {}


def _lock_for(chat_id: object) -> asyncio.Lock:
    lock = _locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[chat_id] = lock
    return lock


async def wait_turn(chat_id: object, gap: float = GAP_SECONDS) -> None:
    """Wait until this chat may receive the next message, and book the slot after it."""
    async with _lock_for(chat_id):
        now = time.monotonic()
        slot = _next_slot.get(chat_id, 0.0)
        if slot > now:
            await asyncio.sleep(slot - now)
            now = time.monotonic()
        _next_slot[chat_id] = now + gap


class PacedBot(ExtBot):
    async def send_message(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        if chat_id is not None:
            await wait_turn(chat_id)
        return await super().send_message(*args, **kwargs)
