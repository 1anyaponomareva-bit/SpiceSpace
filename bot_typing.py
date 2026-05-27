"""Telegram «печатает…» indicator while Claude or other slow work runs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from telegram.constants import ChatAction


async def keep_typing(bot, chat_id: int, stop_event: asyncio.Event) -> None:
    """Repeat typing action every 4s until stop_event is set."""
    try:
        while not stop_event.is_set():
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        return


@asynccontextmanager
async def typing_while(bot, chat_id: int):
    """Context manager: typing indicator for the whole block."""
    stop_event = asyncio.Event()
    task = asyncio.create_task(keep_typing(bot, chat_id, stop_event))
    try:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        yield
    finally:
        stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
