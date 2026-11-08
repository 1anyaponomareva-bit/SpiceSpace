"""Optional plain-text dialog log for the TEST bot only.

Off unless LOG_DIALOG_TEXT=1. Never enable in production: it stores what users write.
Incoming messages and button presses are logged by a group -1 handler; outgoing
messages by an ExtBot subclass, so no send call in the bot needs to change.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, ContextTypes, ExtBot, TypeHandler

_LOCK = threading.Lock()


def enabled() -> bool:
    return os.getenv("LOG_DIALOG_TEXT", "").strip() == "1"


def _path() -> Path:
    default = Path(__file__).resolve().parent / "dialog.log"
    return Path(os.getenv("DIALOG_LOG_PATH", "").strip() or default)


def write(chat_id: object, direction: str, text: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {chat_id} {direction} {text}".rstrip()
    with _LOCK:
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(line.replace("\r", "") + "\n\n")


class DialogLogBot(ExtBot):
    async def send_message(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id", args[0] if args else "?")
        text = kwargs.get("text", args[1] if len(args) > 1 else "")
        write(chat_id, "БОТ:", str(text))
        return await super().send_message(*args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        text = kwargs.get("text", args[0] if args else "")
        write(kwargs.get("chat_id", "?"), "БОТ (правка):", str(text))
        return await super().edit_message_text(*args, **kwargs)


async def _log_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    cid = chat.id if chat else "?"
    if update.message and update.message.text:
        write(cid, "ОНА:", update.message.text)
    elif update.message and update.message.caption:
        write(cid, "ОНА (фото):", update.message.caption)
    elif update.message and update.message.photo:
        write(cid, "ОНА:", "[фото]")
    elif update.callback_query:
        write(cid, "ОНА (кнопка):", str(update.callback_query.data or ""))


def build_application(token: str) -> Application:
    """Application with dialog logging when enabled, the usual one otherwise."""
    if not enabled():
        return Application.builder().token(token).build()
    app = Application.builder().bot(DialogLogBot(token)).build()
    app.add_handler(TypeHandler(Update, _log_incoming), group=-1)
    return app
