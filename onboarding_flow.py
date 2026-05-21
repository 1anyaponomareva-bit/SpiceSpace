"""4-step onboarding — живой разговор, без кнопок."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

import db
from summaries import save_onboarding_summary

if TYPE_CHECKING:
    pass

log = logging.getLogger("coach_bot")

OB_RETURNING = 0
OB_ASK_NAME = 1
OB_ASK_MORNING = 2
OB_MAIN_GOAL = 3
OB_ASK_TIME = 4

GREETING_NEW = """Я знаю — задач много, желаний ещё больше. Но в конце дня ощущение что ничего не сдвинулось. Ставишь цели, начинаешь — и где-то сливаешься.

Я создана на основе исследований Teresa Amabile & Steven Kramer (Harvard), BJ Fogg (Stanford) и Self-Determination Theory (Deci & Ryan). Доказано: люди достигают целей в 3 раза чаще когда видят прогресс и получают ежедневную подотчётность.

Я здесь чтобы это изменить. Меня зовут Спейс 🌶️

Как тебя зовут?"""

GOAL_SUGGESTIONS = """Может быть, что-то из этого откликается?

— Наладить режим (спорт, сон, питание)
— Увеличить заработок или найти доп доход
— Заняться чем-то важным (работа, проект, учёба)
— Найти баланс между всем (дети, работа, себя)
— Улучшить отношения (с семьёй, друзьями, партнёром)
— Начать что-то новое (хобби, навык, путешествие)
— Избавиться от выгорания
— Установить границы — начать говорить НЕТ
— Найти смысл и понять чего я реально хочу

Или напиши своими словами — пусть даже размыто."""

_VAGUE_GOAL = frozenset(
    {
        "не знаю",
        "не знаю.",
        "хз",
        "хз.",
        "не понимаю",
        "не понимаю.",
        "не уверена",
        "не уверен",
        "затрудняюсь",
        "?",
        "…",
        "...",
    }
)

_KIDS_HINTS = (
    "ребён",
    "ребен",
    "дети",
    "детей",
    "дочь",
    "дочк",
    "сын",
    "сынов",
    "малыш",
    "младен",
    "груднич",
    "садик",
    "сад ",
    "в сад",
    "школ",
    "няня",
    "нян",
    "подгуз",
    "коляск",
    "родитель",
    "мама ",
    "мамой",
)

_WORD_HOURS: dict[str, int] = {
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}


def greeting_returning(name: str) -> str:
    n = (name or "").strip() or "подруга"
    return (
        f"{n}, привет 🙂 Ты уже со мной.\n\n"
        "Хочешь обновить свой профиль или просто поговорить?"
    )


def _default_timezone() -> str:
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def _detect_kids_from_text(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _KIDS_HINTS)


def _note_kids_from_answer(st: dict, raw: str) -> None:
    if _detect_kids_from_text(raw):
        st["has_kids"] = True


def _is_vague_goal(raw: str) -> bool:
    t = (raw or "").strip().lower()
    if len(t) < 5:
        return True
    if t in _VAGUE_GOAL:
        return True
    for phrase in ("не знаю", "не понимаю", "не уверен", "хз"):
        if phrase in t:
            return True
    return False


def _parse_daily_time_strict(raw: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (raw or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _parse_daily_time_nl(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None

    strict = _parse_daily_time_strict(text)
    if strict:
        return strict

    m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi <= 59:
            return f"{h:02d}:{mi:02d}"

    m = re.search(r"(?:в\s+)?(\d{1,2})(?:\s*[:.]\s*(\d{2}))?\s*(?:утра|утром|час|часов)?", text)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    for word, hour in _WORD_HOURS.items():
        if re.search(rf"\b{word}\b", text):
            if "половин" in text and "девят" in text:
                return "09:30"
            if "половин" in text:
                return f"{hour:02d}:30"
            return f"{hour:02d}:00"

    if "полдевят" in text or "пол 9" in text:
        return "08:30"
    if "полдесят" in text or "пол 10" in text:
        return "09:30"

    return None


def looks_like_restart_onboarding(raw: str) -> bool:
    low = (raw or "").strip().lower()
    return any(
        w in low
        for w in (
            "обнов",
            "обновить",
            "заново",
            "сначала",
            "профиль",
            "перезап",
            "изменить профиль",
            "новый профиль",
        )
    )


def looks_like_just_chat(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if looks_like_restart_onboarding(raw):
        return False
    return any(
        w in low
        for w in (
            "поговор",
            "просто",
            "давай",
            "не надо",
            "не хочу обнов",
            "продолж",
            "поболта",
        )
    )


def persist_profile(cid: int, st: dict, model_names: list[str]) -> dict:
    profile = {
        "name": str(st.get("name", "")).strip() or "подруга",
        "morning_routine": str(st.get("morning_routine", "")).strip()[:500],
        "has_kids": st.get("has_kids"),
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


def start_new_onboarding(onboarding: dict[int, dict], cid: int) -> None:
    onboarding[cid] = {"step": OB_ASK_NAME}


def start_returning_choice(onboarding: dict[int, dict], cid: int) -> None:
    onboarding[cid] = {"step": OB_RETURNING}


def start_reonboarding(onboarding: dict[int, dict], cid: int, name: str) -> None:
    onboarding[cid] = {"step": OB_ASK_MORNING, "name": name}


async def handle_returning_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
    onboarding: dict[int, dict],
    user_profiles: dict[str, dict],
) -> bool:
    """Обрабатывает ответ после повторного /start. True = обработано."""
    cid = update.effective_chat.id
    msg = update.message
    if not msg:
        return True

    st = onboarding.get(cid) or {}
    if int(st.get("step") or 0) != OB_RETURNING:
        return False

    prof = user_profiles.get(str(cid)) or {}
    name = str(prof.get("name", "")).strip() or "подруга"

    if looks_like_restart_onboarding(raw):
        start_reonboarding(onboarding, cid, name)
        await msg.reply_text(
            f"{name}, приятно познакомиться 🙂\n\n"
            "Как начинается твоё утро — кофе в тишине, спорт, или дети раньше будильника?"
        )
        return True

    if looks_like_just_chat(raw):
        onboarding.pop(cid, None)
        await msg.reply_text("Хорошо, я здесь. Напиши что у тебя на душе.")
        return True

    await msg.reply_text(
        "Напиши «обновить профиль» или «поговорить» — так я пойму, что тебе нужно."
    )
    return True


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

    if await handle_returning_choice(update, context, raw, onboarding, user_profiles):
        return

    st = onboarding.setdefault(cid, {"step": OB_ASK_NAME})
    step = int(st.get("step") or OB_ASK_NAME)
    _note_kids_from_answer(st, raw)

    if step == OB_ASK_NAME:
        name = raw.strip()[:120] or "подруга"
        st["name"] = name
        st["step"] = OB_ASK_MORNING
        await msg.reply_text(
            f"{name}, приятно познакомиться 🙂\n\n"
            "Как начинается твоё утро — кофе в тишине, спорт, или дети раньше будильника?"
        )
        return

    if step == OB_ASK_MORNING:
        st["morning_routine"] = raw.strip()[:500] or "как получится"
        _note_kids_from_answer(st, raw)
        st["step"] = OB_MAIN_GOAL
        await msg.reply_text(
            "Давай составим цель на месяц. Чего ты хочешь достигнуть или изменить за эти 30 дней?"
        )
        return

    if step == OB_MAIN_GOAL:
        if _is_vague_goal(raw):
            await msg.reply_text(GOAL_SUGGESTIONS)
            return
        st["main_goal"] = raw.strip()[:2000]
        _note_kids_from_answer(st, raw)
        st["step"] = OB_ASK_TIME
        await msg.reply_text(
            "Когда мне написать тебе завтра утром? Во сколько ты обычно просыпаешься?"
        )
        return

    if step == OB_ASK_TIME:
        parsed = _parse_daily_time_nl(raw)
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла время. Напиши, пожалуйста, как 09:30 или «в 8 утра»."
            )
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
                    f"цель на месяц — {profile.get('main_goal')}, пишу в {parsed}."
                ],
            }
        ]

        await msg.reply_text(
            f"Всё, запомнила ✨\n\n"
            f"Завтра в {parsed} напишу тебе первой — и уже буду знать про тебя кое-что важное.\n\n"
            f"Если что-то случится сегодня и захочешь поговорить — я здесь."
        )
        return

    await msg.reply_text("Что-то сбилось. Нажми /start — начнём сначала.")
