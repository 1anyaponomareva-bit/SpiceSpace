"""5-step conversational onboarding (not a form)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import db
from summaries import save_onboarding_summary

if TYPE_CHECKING:
    from telegram import Message

log = logging.getLogger("coach_bot")

OB_ASK_NAME = 1
OB_ASK_MORNING = 2
OB_ASK_KIDS = 3
OB_ASK_WORKS = 4
OB_MAIN_GOAL = 5
OB_ASK_TIME = 6


def kids_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да", callback_data="ob:kids:yes"),
                InlineKeyboardButton("Нет", callback_data="ob:kids:no"),
            ]
        ]
    )


def works_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да", callback_data="ob:works:yes"),
                InlineKeyboardButton("Нет", callback_data="ob:works:no"),
            ],
            [InlineKeyboardButton("Своё дело", callback_data="ob:works:own")],
        ]
    )


def _default_timezone() -> str:
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def _parse_daily_time(raw: str) -> str | None:
    import re

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (raw or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def persist_profile(cid: int, st: dict, model_names: list[str]) -> dict:
    profile = {
        "name": str(st.get("name", "")).strip() or "подруга",
        "morning_routine": str(st.get("morning_routine", "")).strip()[:500],
        "has_kids": st.get("has_kids"),
        "works": str(st.get("works", "")).strip(),
        "main_goal": str(st.get("main_goal", "")).strip()[:2000],
        "daily_time": str(st.get("daily_time", "09:30")),
        "timezone": str(st.get("timezone") or _default_timezone()),
        "daily_enabled": True,
        "last_daily_sent_date": "",
        "raw_goal": str(st.get("main_goal", "")).strip()[:2000],
        "final_goal": str(st.get("main_goal", "")).strip()[:2000],
        "goal_type": "qualitative",
        "goal_signals": [],
        "streak": 0,
        "weekly_score": 0,
        "completed_tasks": [],
        "missed_tasks": [],
        "current_week": 1,
    }
    db.upsert_profile(cid, profile)
    db.save_subscriber(cid, True)
    save_onboarding_summary(cid, profile, model_names)
    return profile


async def handle_onboarding_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
    onboarding: dict[int, dict],
    histories: dict[int, list],
    user_profiles: dict[str, dict],
    subscribers: set[int],
) -> None:
    cid = update.effective_chat.id
    msg = update.message
    if not msg:
        return

    st = onboarding.setdefault(cid, {"step": OB_ASK_NAME})
    step = int(st.get("step") or OB_ASK_NAME)

    if step == OB_ASK_NAME:
        name = raw.strip()[:120] or "подруга"
        st["name"] = name
        st["step"] = OB_ASK_MORNING
        await msg.reply_text(
            f"Приятно, {name} ☀️\n\n"
            "Как начинается твоё утро? Кофе и тишина, спорт, или всё сразу в хаосе?"
        )
        return

    if step == OB_ASK_MORNING:
        st["morning_routine"] = raw.strip()[:500] or "как получится"
        st["step"] = OB_ASK_KIDS
        await msg.reply_text("Дети есть?", reply_markup=kids_keyboard())
        return

    if step == OB_ASK_KIDS:
        await msg.reply_text("Нажми кнопку — да или нет 👇", reply_markup=kids_keyboard())
        return

    if step == OB_ASK_WORKS:
        await msg.reply_text("Выбери вариант 👇", reply_markup=works_keyboard())
        return

    if step == OB_MAIN_GOAL:
        text = raw.strip()[:2000]
        if len(text) < 5:
            await msg.reply_text(
                "Напиши своими словами — ощущение, не цель. "
                "Например: «хочу перестать чувствовать что не успеваю»."
            )
            return
        st["main_goal"] = text
        st["step"] = OB_ASK_TIME
        await msg.reply_text(
            "В какое время тебе написать завтра утром?\nФормат HH:MM — например 09:30"
        )
        return

    if step == OB_ASK_TIME:
        parsed = _parse_daily_time(raw)
        if not parsed:
            await msg.reply_text("Напиши время как 09:30 или 18:00")
            return
        st["daily_time"] = parsed
        st["timezone"] = _default_timezone()
        model_names = context.bot_data.get("claude_model_names") or []
        profile = await asyncio.to_thread(persist_profile, cid, st, model_names)
        onboarding.pop(cid, None)
        user_profiles[str(cid)] = profile
        subscribers.add(cid)

        name = profile.get("name", "")
        histories[cid] = [
            {
                "role": "user",
                "parts": [
                    f"[SpiceSpace] Онбординг: {name}, утро — {profile.get('morning_routine')}, "
                    f"ощущение — {profile.get('main_goal')}, пишу в {parsed}."
                ],
            }
        ]

        await msg.reply_text(
            f"Записала ✨ Буду писать тебе в {parsed}.\n\n"
            f"Завтра утром напишу первым — и уже буду помнить про тебя.\n\n"
            "/stop — если захочешь выключить утренние сообщения."
        )
        return

    await msg.reply_text("Что-то сбилось. Нажми /start — пройдём знакомство сначала.")


async def onboarding_context_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    onboarding: dict[int, dict],
) -> None:
    q = update.callback_query
    if not q or not q.data or not q.message:
        return

    cid = q.message.chat_id
    st = onboarding.get(cid)
    if not st:
        await q.answer()
        return

    step = int(st.get("step") or 0)
    parts = q.data.split(":")
    if len(parts) != 3 or parts[0] != "ob":
        await q.answer()
        return

    kind, val = parts[1], parts[2]

    if kind == "kids" and step == OB_ASK_KIDS and val in ("yes", "no"):
        st["has_kids"] = val == "yes"
        st["step"] = OB_ASK_WORKS
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text("Работаешь?", reply_markup=works_keyboard())
        return

    if kind == "works" and step == OB_ASK_WORKS and val in ("yes", "no", "own"):
        st["works"] = val
        st["step"] = OB_MAIN_GOAL
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text(
            "Что сейчас не так? Не цель — а ощущение.\n"
            "Например: «хочу перестать чувствовать что я не успеваю» "
            "или «хочу снова чувствовать себя собой»."
        )
        return

    await q.answer()
