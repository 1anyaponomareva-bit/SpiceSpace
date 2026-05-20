"""5-step conversational onboarding — живой разговор, без кнопок."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from telegram import Update
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


def _parse_kids(raw: str) -> bool | None:
    raw = raw.strip().lower()
    yes_words = ["да", "есть", "двое", "трое", "один", "одна", "ребёнок", "дети", "дочь", "сын", "малыш"]
    no_words = ["нет", "нету", "без детей", "пока нет"]
    for w in yes_words:
        if w in raw:
            return True
    for w in no_words:
        if w in raw:
            return False
    return None


def _parse_works(raw: str) -> str | None:
    raw = raw.strip().lower()
    own_words = ["своё", "свой", "своя", "фриланс", "бизнес", "предприниматель", "сама", "самозанятая"]
    yes_words = ["да", "работаю", "офис", "найм"]
    no_words = ["нет", "не работаю", "декрет", "дома"]
    for w in own_words:
        if w in raw:
            return "own"
    for w in yes_words:
        if w in raw:
            return "yes"
    for w in no_words:
        if w in raw:
            return "no"
    return None


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
            f"{name}, привет 🙂\n\n"
            f"Как начинается твоё утро — кофе в тишине, спорт, или дети раньше будильника?"
        )
        return

    if step == OB_ASK_MORNING:
        st["morning_routine"] = raw.strip()[:500] or "как получится"
        st["step"] = OB_ASK_KIDS
        await msg.reply_text("Дети есть?")
        return

    if step == OB_ASK_KIDS:
        parsed = _parse_kids(raw)
        if parsed is None:
            await msg.reply_text("Напиши да или нет — есть дети?")
            return
        st["has_kids"] = parsed
        st["step"] = OB_ASK_WORKS
        await msg.reply_text("Работаешь? Найм, своё дело, или сейчас нет?")
        return

    if step == OB_ASK_WORKS:
        parsed = _parse_works(raw)
        if parsed is None:
            await msg.reply_text("Напиши как — работаю, своё дело, или сейчас нет")
            return
        st["works"] = parsed
        st["step"] = OB_MAIN_GOAL
        await msg.reply_text(
            "И последнее — что сейчас хочешь изменить?\n\n"
            "Не цель, а ощущение. Например: «хочу перестать чувствовать что не успеваю» "
            "или «хочу снова чувствовать себя собой»."
        )
        return

    if step == OB_MAIN_GOAL:
        text = raw.strip()[:2000]
        if len(text) < 5:
            await msg.reply_text("Напиши своими словами — что хочешь изменить, пусть даже размыто.")
            return
        st["main_goal"] = text
        st["step"] = OB_ASK_TIME
        await msg.reply_text(
            "В какое время написать тебе завтра утром?\n"
            "Напиши в формате 09:30"
        )
        return

    if step == OB_ASK_TIME:
        parsed = _parse_daily_time(raw)
        if not parsed:
            await msg.reply_text("Напиши время как 09:30 или 08:00")
            return
        st["daily_time"] = parsed
        st["timezone"] = _default_timezone()
        model_names = context.bot_data.get("claude_model_names") or []
        profile = await asyncio.to_thread(persist_profile, cid, st, model_names)
        onboarding.pop(cid, None)
        user_profiles[str(cid)] = profile

        name = profile.get("name", "")
        histories[cid] = [
            {
                "role": "user",
                "parts": [
                    f"[SpiceSpace] Онбординг завершён: {name}, "
                    f"утро — {profile.get('morning_routine')}, "
                    f"ощущение — {profile.get('main_goal')}, "
                    f"пишу в {parsed}."
                ],
            }
        ]

        await msg.reply_text(
            f"Всё, запомнила ✨\n\n"
            f"Завтра в {parsed} напишу — и уже буду знать про тебя кое-что важное.\n\n"
            f"Если что-то случится сегодня и захочешь поговорить — я здесь."
        )
        return

    await msg.reply_text("Что-то сбилось. Нажми /start — начнём знакомство сначала.")
