"""Онбординг SpiceSpace — живой диалог, без кнопок."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

import db
from claude_client import generate as claude_generate
from prompts import (
    FIRST_QUESTION_AFTER_ONBOARD,
    GOAL_CLARIFY_PROMPT,
    GOAL_DISCOMFORT_PROMPT,
    GOAL_FIXED_CLARIFY,
    GOAL_SUGGESTIONS,
)
from summaries import save_onboarding_summary

if TYPE_CHECKING:
    pass

log = logging.getLogger("coach_bot")

OB_RETURNING = 0
OB_ASK_NAME = 1
OB_GOAL_DIALOG = 2
OB_ASK_MORNING_TIME = 3
OB_ASK_EVENING_TIME = 4

GREETING_NEW = "Привет! 👋 Меня зовут Спейс. Как тебя зовут?"

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


def message_after_name(name: str) -> str:
    n = (name or "").strip() or "подруга"
    return (
        f"{n}, приятно познакомиться 🙂\n\n"
        "Я создана чтобы помочь тебе реально двигаться к тому что важно — "
        "не просто ставить цели, а достигать их.\n\n"
        "Это основано на исследованиях Teresa Amabile (Harvard) и BJ Fogg (Stanford) — "
        "люди достигают целей в 3 раза чаще когда есть ежедневная поддержка и видимый прогресс.\n\n"
        "Давай составим цель на месяц 🎯 Чего ты хочешь достигнуть или изменить за эти 30 дней?\n\n"
        'Если пока не знаешь — напиши "не знаю" и разберёмся вместе.'
    )


MORNING_TIME_QUESTION = (
    "Когда тебе удобнее всего побыть наедине с собой — без детей, без работы, без суеты? 🌅\n\n"
    "В это время я буду писать тебе чтобы сосредоточиться на твоей цели. "
    "Лучше выбирать утро или первую половину дня."
)

EVENING_TIME_QUESTION = "И ещё — в какое время вечером мне спрашивать как прошёл день? 🌙"


def greeting_returning(name: str) -> str:
    n = (name or "").strip() or "подруга"
    return (
        f"Привет, {n} 🙂 Ты уже со мной.\n\n"
        "Хочешь обновить профиль или просто поговорить?"
    )


def _default_timezone() -> str:
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def _detect_kids_from_text(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _KIDS_HINTS)


def _note_kids_from_answer(st: dict, raw: str) -> None:
    if _detect_kids_from_text(raw):
        st["has_kids"] = True


def _is_dont_know(raw: str) -> bool:
    t = (raw or "").strip().lower()
    if len(t) < 5:
        return True
    if t in _VAGUE_GOAL:
        return True
    for phrase in ("не знаю", "не понимаю", "не уверен", "хз"):
        if phrase in t:
            return True
    return False


def _is_vague_goal(raw: str) -> bool:
    return _is_dont_know(raw)


def _combined_goal_text(st: dict, latest: str = "") -> str:
    parts = list(st.get("goal_messages") or [])
    if latest.strip():
        parts.append(latest.strip())
    return " → ".join(parts) if parts else (latest or "").strip()


def _needs_goal_clarification(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 12:
        return True
    if _is_dont_know(t):
        return True
    low = t.lower()
    has_metric = bool(re.search(r"\d", low)) or any(
        x in low for x in ("кг", "руб", "$", "€", "раз в", "ежеднев", "недел", "минут", "часов")
    )
    fuzzy = any(x in low for x in ("хочу", "хотел", "больше", "меньше", "лучше", "перестать", "начать"))
    vague_topics = ("похуд", "зарабат", "спорт", "баланс", "выгор", "отношен", "смысл")
    if fuzzy and not has_metric:
        return True
    if any(p in low for p in vague_topics) and not has_metric and len(t) < 70:
        return True
    return False


def parse_time_nl(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi <= 59:
            return f"{h:02d}:{mi:02d}"

    m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi <= 59:
            return f"{h:02d}:{mi:02d}"

    m = re.search(r"(?:в\s+)?(\d{1,2})(?:\s*[:.]\s*(\d{2}))?\s*(?:утра|утром|вечера|вечером|час|часов)?", text)
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
    morning = str(st.get("morning_time", "09:30"))
    evening = str(st.get("evening_time", "21:00"))
    profile = {
        "name": str(st.get("name", "")).strip() or "подруга",
        "main_goal": str(st.get("main_goal", "")).strip()[:2000],
        "morning_time": morning,
        "evening_time": evening,
        "daily_time": morning,
        "timezone": str(st.get("timezone") or _default_timezone()),
        "daily_enabled": True,
        "last_morning_sent_date": "",
        "last_evening_sent_date": "",
        "last_daily_sent_date": "",
        "has_kids": st.get("has_kids"),
        "raw_goal": str(st.get("main_goal", "")).strip()[:2000],
        "final_goal": str(st.get("main_goal", "")).strip()[:2000],
        "goal_type": "qualitative",
        "goal_signals": [],
        "streak": int(st.get("streak") or 0),
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
    onboarding[cid] = {
        "step": OB_GOAL_DIALOG,
        "name": name,
        "goal_messages": [],
        "goal_phase": None,
    }


async def _goal_clarify_question(goal_text: str, model_names: list[str]) -> str:
    prompt = GOAL_CLARIFY_PROMPT.format(goal_text=goal_text[:1500])

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system="Ты Спейс. Только один короткий вопрос.",
                    max_tokens=120,
                    cache_core=False,
                ).strip()
                if text:
                    return text
            except Exception as e:
                log.warning("goal clarify %s: %s", mid, e)
        return "А как ты поймёшь что достигла этого? Что конкретно изменится?"

    return await asyncio.to_thread(call)


async def _first_question_after_onboard(
    name: str, main_goal: str, model_names: list[str]
) -> str:
    prompt = FIRST_QUESTION_AFTER_ONBOARD.format(
        name=name or "подруга",
        main_goal=main_goal or "цель",
    )

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system="Ты Спейс. Один вопрос, 1-2 предложения.",
                    max_tokens=120,
                    cache_core=False,
                ).strip()
                if text:
                    return text
            except Exception as e:
                log.warning("first question %s: %s", mid, e)
        return "Расскажи — с чего для тебя логичнее начать прямо сейчас?"

    return await asyncio.to_thread(call)


async def handle_returning_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
    onboarding: dict[int, dict],
    user_profiles: dict[str, dict],
) -> bool:
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
        await msg.reply_text(message_after_name(name))
        return True

    if looks_like_just_chat(raw):
        onboarding.pop(cid, None)
        await msg.reply_text("Хорошо, я здесь. Напиши что у тебя на душе.")
        return True

    await msg.reply_text(
        'Напиши «обновить профиль» или «поговорить» — так я пойму, что тебе нужно.'
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
    model_names = context.bot_data.get("claude_model_names") or []

    if step == OB_ASK_NAME:
        name = raw.strip()[:120] or "подруга"
        st["name"] = name
        st["step"] = OB_GOAL_DIALOG
        st["goal_messages"] = []
        st["goal_phase"] = None
        await msg.reply_text(message_after_name(name))
        return

    if step == OB_GOAL_DIALOG:
        msgs = st.setdefault("goal_messages", [])
        msgs.append(raw.strip()[:2000])
        combined = _combined_goal_text(st)

        if _is_dont_know(raw):
            phase = st.get("goal_phase")
            if phase is None:
                st["goal_phase"] = "discomfort_asked"
                await msg.reply_text(GOAL_DISCOMFORT_PROMPT)
                return
            if phase == "discomfort_asked":
                st["goal_phase"] = "suggestions_shown"
                await msg.reply_text(GOAL_SUGGESTIONS)
                return

        if _needs_goal_clarification(combined):
            clarifies = int(st.get("goal_clarify_count") or 0)
            st["goal_clarify_count"] = clarifies + 1
            if clarifies == 0:
                await msg.reply_text(GOAL_FIXED_CLARIFY)
            else:
                question = await _goal_clarify_question(combined, model_names)
                await msg.reply_text(question)
            return

        st["main_goal"] = combined[:2000]
        st["step"] = OB_ASK_MORNING_TIME
        await msg.reply_text(MORNING_TIME_QUESTION)
        return

    if step == OB_ASK_MORNING_TIME:
        parsed = parse_time_nl(raw)
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла. Напиши, пожалуйста, в формате 09:30."
            )
            return
        st["morning_time"] = parsed
        st["step"] = OB_ASK_EVENING_TIME
        await msg.reply_text(EVENING_TIME_QUESTION)
        return

    if step == OB_ASK_EVENING_TIME:
        parsed = parse_time_nl(raw)
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла. Напиши вечернее время как 21:00 или «в 9 вечера»."
            )
            return
        st["evening_time"] = parsed
        st["timezone"] = _default_timezone()

        profile = await asyncio.to_thread(persist_profile, cid, st, model_names)
        onboarding.pop(cid, None)
        user_profiles[str(cid)] = profile
        subscribers.add(cid)

        name = profile.get("name", "")
        mt = profile.get("morning_time", "09:30")
        et = profile.get("evening_time", "21:00")
        histories[cid] = [
            {
                "role": "user",
                "parts": [
                    f"[SpiceSpace] Онбординг: {name}, цель — {profile.get('main_goal')}, "
                    f"утро {mt}, вечер {et}."
                ],
            }
        ]

        await msg.reply_text(
            f"Всё, запомнила ✨\n\n"
            f"Буду писать тебе утром в {mt} и вечером в {et}."
        )

        first_q = await _first_question_after_onboard(
            name, str(profile.get("main_goal", "")), model_names
        )
        await msg.reply_text(first_q)
        histories[cid].append({"role": "model", "parts": [first_q]})
        return

    await msg.reply_text("Что-то сбилось. Нажми /start — начнём сначала.")
