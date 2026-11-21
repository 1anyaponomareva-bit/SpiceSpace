"""Updates of different people in parallel, one person's strictly in order (Anya, 21.11.2026).

Before, python-telegram-bot handled every update one by one: while the model answered one
person (onboarding makes 1-3 calls in a row), everyone else waited. Now up to
MAX_PARALLEL chats are handled at once; a per-chat lock keeps one person's messages in
the order they came, so onboarding and dialog state never interleave.

Every handled message logs how long it waited and how long it took («reply_time»), so
the median and the slow tail can be read from the logs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable

from telegram.ext import BaseUpdateProcessor

log = logging.getLogger("coach_bot")

MAX_PARALLEL = 8


def _chat_key(update: object) -> int | None:
    chat = getattr(update, "effective_chat", None)
    if chat is not None and getattr(chat, "id", None) is not None:
        return int(chat.id)
    user = getattr(update, "effective_user", None)
    if user is not None and getattr(user, "id", None) is not None:
        return int(user.id)
    return None


def _sent_at(update: object) -> datetime | None:
    msg = getattr(update, "effective_message", None)
    dt = getattr(msg, "date", None)
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


class ChatOrderedProcessor(BaseUpdateProcessor):
    def __init__(self, max_parallel: int = MAX_PARALLEL) -> None:
        # the base semaphore only bounds tasks waiting for their chat; the real limit
        # on parallel work is self._work, taken once the chat's turn has come
        super().__init__(max_concurrent_updates=1024)
        self._work = asyncio.Semaphore(max_parallel)
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, key: int) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def do_process_update(self, update: object, coroutine: Awaitable[Any]) -> None:
        key = _chat_key(update)
        start = time.perf_counter()
        if key is None:
            async with self._work:
                await coroutine
            return
        # locks are kept (one small object per chat): dropping one while a message
        # waits on it would let the next message of that chat overtake it
        async with self._lock(key):
            queued = time.perf_counter() - start
            async with self._work:
                begun = time.perf_counter()
                await coroutine
                work = time.perf_counter() - begun
        sent = _sent_at(update)
        total = (datetime.now(timezone.utc) - sent).total_seconds() if sent else None
        log.info(
            "reply_time cid=%s total_s=%s queued_s=%.1f work_s=%.1f",
            key, f"{total:.1f}" if total is not None else "-", queued, work,
        )

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
