"""Онбординг SpiceSpace — живой диалог, без кнопок."""

from __future__ import annotations

import asyncio
import json
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
    GOAL_DIALOG_SYSTEM,
    WEEKLY_GOAL_PROPOSAL_SYSTEM,
)
from summaries import save_onboarding_summary

if TYPE_CHECKING:
    pass

log = logging.getLogger("coach_bot")

# Для проверки деплоя: curl /health → build
BOT_BUILD = "goal-dialog-v5"

OB_RETURNING = 0
OB_ASK_NAME = 1
OB_GOAL_DIALOG = 2
OB_ASK_MORNING_TIME = 3
OB_ASK_EVENING_TIME = 4
OB_ASK_TIME_PER_DAY = 7
OB_WEEKLY_GOAL = 8

GREETING_NEW = "Привет! 👋 Меня зовут Спейс. Как тебя зовут?"

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

TIME_PER_DAY_QUESTION = (
    "Последний вопрос — сколько времени в день ты реально можешь уделять своей цели?\n"
    "Например: 30 минут, час, два часа."
)

_WEEKLY_GOAL_AGREE = (
    "да",
    "ок",
    "окей",
    "подходит",
    "давай",
    "соглас",
    "согласна",
    "верно",
    "хорошо",
    "угу",
    "yes",
    "ага",
    "конечно",
    "именно",
    "точно",
)

_WEEKLY_GOAL_DISAGREE = (
    "нет",
    "не подходит",
    "не то",
    "другой",
    "другая",
    "измени",
    "переделай",
    "не хочу",
    "не надо",
)


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


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _last_assistant_reply(goal_turns: list[dict]) -> str:
    for turn in reversed(goal_turns):
        if turn.get("role") == "assistant":
            return str(turn.get("content", "")).strip()
    return ""


def _is_vague_user_message(text: str) -> bool:
    low = (text or "").strip().lower()
    if len(low) < 4:
        return True
    if low in ("хз", "хз.", "?", "…", "..."):
        return True
    return any(x in low for x in ("хз", "не знаю", "не понимаю", "невнят", "не уверен"))


def _vague_user_streak(goal_turns: list[dict]) -> int:
    count = 0
    for turn in reversed(goal_turns):
        if turn.get("role") != "user":
            continue
        if _is_vague_user_message(turn.get("content", "")):
            count += 1
        else:
            break
    return count


def _last_substantive_user_message(goal_turns: list[dict]) -> str:
    for turn in reversed(goal_turns):
        if turn.get("role") != "user":
            continue
        content = str(turn.get("content", "")).strip()
        if content and not _is_vague_user_message(content):
            return content
    return ""


def _goal_ready_flag(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "да")
    return False


def _parse_goal_dialog_json(text: str) -> dict | None:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reply = str(data.get("reply", "")).strip()
    if not reply:
        return None
    return {
        "reply": reply,
        "goal_ready": _goal_ready_flag(data.get("goal_ready")),
        "goal": str(data.get("goal", "")).strip(),
    }


def _fallback_goal_reply(goal_turns: list[dict]) -> dict:
    """Только если Claude/JSON недоступны — разные ответы по ходу диалога."""
    user_texts = [t["content"] for t in goal_turns if t["role"] == "user"]
    last = (user_texts[-1] if user_texts else "").strip().lower()
    n = len(user_texts)

    if n <= 1 and ("не знаю" in last or len(last) < 5):
        return {
            "reply": "Что сейчас больше всего не устраивает в своей жизни?",
            "goal_ready": False,
            "goal": "",
        }
    if any(x in last for x in ("деньг", "зарабат", "доход", "буду зарабатывать")):
        return {
            "reply": (
                "Поняла, про деньги. Сколько в месяц хочешь выйти "
                "или что должно измениться, чтобы сказала — получилось?"
            ),
            "goal_ready": False,
            "goal": "",
        }
    return {
        "reply": "Расскажи конкретнее — как через месяц поймёшь, что цель достигнута?",
        "goal_ready": False,
        "goal": "",
    }


async def _claude_goal_dialog(
    goal_turns: list[dict],
    model_names: list[str],
    *,
    extra_user_hint: str = "",
) -> dict:
    """Мультитурновый диалог про цель → {"reply", "goal_ready", "goal"}."""
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in goal_turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    messages,
                    system=GOAL_DIALOG_SYSTEM,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_goal_dialog_json(text)
                if parsed:
                    log.info(
                        "goal_dialog ok model=%s ready=%s",
                        mid,
                        parsed["goal_ready"],
                    )
                    return parsed
                log.warning(
                    "goal_dialog bad JSON model=%s raw=%s",
                    mid,
                    text[:400],
                )
            except Exception as e:
                log.warning("goal_dialog %s: %s", mid, e, exc_info=True)
        log.error("goal_dialog: all models failed, using fallback")
        return _fallback_goal_reply(goal_turns)

    return await asyncio.to_thread(call)


def parse_time_nl(raw: str, context: str = "morning") -> str | None:
    """Parse natural-language time. context: 'morning' or 'evening'."""
    text = (raw or "").strip().lower()
    if not text:
        return None

    ctx = context if context in ("morning", "evening") else "morning"

    def fmt(h: int, mi: int = 0) -> str | None:
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
        return None

    def resolve_ambiguous(h: int, mi: int = 0) -> str | None:
        if ctx == "evening":
            if 1 <= h <= 11:
                return fmt(h + 12, mi)
            return fmt(h, mi)
        # morning: 1–12 without marker → AM (5–12 per spec; 1–4 also AM)
        if 1 <= h <= 12:
            return fmt(h, mi)
        return fmt(h, mi)

    def resolve_marked(h: int, mi: int, marker: str | None) -> str | None:
        mk = (marker or "").strip()
        if mk in ("утра", "утром"):
            return fmt(h, mi)
        if mk in ("вечера", "вечером", "ночи", "ночью"):
            if 1 <= h <= 11:
                return fmt(h + 12, mi)
            return fmt(h, mi)
        return resolve_ambiguous(h, mi)

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return fmt(h, mi)

    m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return fmt(h, mi)

    m = re.fullmatch(r"(\d{1,2})(?:\s*[:.]\s*(\d{2}))?", text)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        return resolve_ambiguous(h, mi)

    m = re.search(
        r"(?:в\s+)?(\d{1,2})(?:\s*[:.]\s*(\d{2}))?\s*(утра|утром|вечера|вечером|ночи|ночью|час|часов)?",
        text,
    )
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        marker = m.group(3)
        if marker in ("утра", "утром", "вечера", "вечером", "ночи", "ночью"):
            return resolve_marked(h, mi, marker)
        return resolve_ambiguous(h, mi)

    for word, hour in _WORD_HOURS.items():
        if re.search(rf"\b{word}\b", text):
            mi = 30 if "половин" in text else 0
            marker = None
            if re.search(r"(утра|утром)", text):
                marker = "утра"
            elif re.search(r"(вечера|вечером|ночи|ночью)", text):
                marker = "вечера"
            if "половин" in text and "девят" in text:
                return "09:30"
            if marker:
                return resolve_marked(hour, mi, marker)
            return resolve_ambiguous(hour, mi)

    if "полдевят" in text or "пол 9" in text:
        return "08:30"
    if "полдесят" in text or "пол 10" in text:
        return "09:30"

    return None


def parse_time_per_day(raw: str) -> str | None:
    """Свободный ответ: «час», «30 минут», «2 часа», «полчаса»."""
    text = (raw or "").strip().lower()
    if not text or len(text) < 2:
        return None

    if "полчас" in text or "пол час" in text:
        return "30 минут"

    m = re.search(r"(\d+)\s*(мин|минут|минуты|мин\.?|м\b)", text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 480:
            return f"{n} минут"

    m = re.search(r"(\d+)\s*(час|часа|часов|ч\b|h\b)", text)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 12:
            word = "час" if n == 1 else "часа" if 2 <= n <= 4 else "часов"
            return f"{n} {word}"

    for word, hour in _WORD_HOURS.items():
        if re.search(rf"\b{word}\b", text) and "час" in text:
            w = "час" if hour == 1 else "часа" if 2 <= hour <= 4 else "часов"
            return f"{hour} {w}"

    if re.fullmatch(r"час[аом]?", text) or text in ("час", "часик", "hour"):
        return "1 час"
    if "два час" in text or "2 час" in text:
        return "2 часа"
    if "три час" in text or "3 час" in text:
        return "3 часа"

    if any(x in text for x in ("мин", "час", "часов", "hour", "min")):
        return (raw or "").strip()[:200]

    return None


def _is_weekly_goal_agreement(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if low in _WEEKLY_GOAL_AGREE or low.startswith("да"):
        return True
    return any(w in low for w in ("подходит", "давай", "соглас", "окей", "ок "))


def _is_weekly_goal_disagreement(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if any(low.startswith(w) or w in low for w in _WEEKLY_GOAL_DISAGREE):
        return True
    return False


def _extract_weekly_goal_from_proposal(proposal: str) -> str:
    text = (proposal or "").strip()
    m = re.search(
        r"предлагаю:\s*(.+?)(?:\.\s*подходит|\.\s*$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()[:2000]
    return text[:2000]


async def _claude_weekly_goal_proposal(
    main_goal: str,
    time_per_day: str,
    model_names: list[str],
) -> str:
    user_msg = (
        f"Цель на месяц: {main_goal or 'не указана'}\n"
        f"Время в день: {time_per_day or 'не указано'}"
    )

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": user_msg}],
                    system=WEEKLY_GOAL_PROPOSAL_SYSTEM,
                    max_tokens=200,
                    cache_core=False,
                ).strip()
                if text:
                    return text
            except Exception as e:
                log.warning("weekly_goal_proposal %s: %s", mid, e)
        return (
            f"На первую неделю предлагаю: сделать первый конкретный шаг к цели "
            f"«{(main_goal or 'твоя цель')[:80]}» — по {time_per_day or '30 минут'} в день. Подходит?"
        )

    return await asyncio.to_thread(call)


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
        "weekly_goal": str(st.get("weekly_goal", "")).strip()[:2000],
        "time_per_day": str(st.get("time_per_day", "")).strip()[:200],
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
        "goal_turns": [],
    }


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


async def _complete_onboarding(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cid: int,
    st: dict,
    onboarding: dict[int, dict],
    histories: dict[int, list],
    user_profiles: dict[str, dict],
    subscribers: set[int],
    model_names: list[str],
) -> None:
    msg = update.message
    if not msg:
        return

    profile = await asyncio.to_thread(persist_profile, cid, st, model_names)
    onboarding.pop(cid, None)
    user_profiles[str(cid)] = profile
    subscribers.add(cid)

    name = profile.get("name", "")
    mt = profile.get("morning_time", "09:30")
    et = profile.get("evening_time", "21:00")
    main_goal = str(profile.get("main_goal", ""))
    weekly_goal = str(profile.get("weekly_goal", ""))

    histories[cid] = [
        {
            "role": "user",
            "parts": [
                f"[SpiceSpace] Онбординг: {name}, цель месяца — {main_goal}, "
                f"неделя — {weekly_goal}, утро {mt}, вечер {et}."
            ],
        }
    ]

    progress_kb = None
    fn = context.bot_data.get("progress_reply_keyboard")
    if callable(fn):
        progress_kb = fn()

    await msg.reply_text(
        f"Всё, запомнила ✨\n\n"
        f"Цель месяца: {main_goal}\n"
        f"Цель этой недели: {weekly_goal}\n\n"
        f"Буду писать тебе утром в {mt} и вечером в {et}.",
        reply_markup=progress_kb,
    )

    first_q = await _first_question_after_onboard(name, main_goal, model_names)
    await msg.reply_text(first_q, reply_markup=progress_kb)
    histories[cid].append({"role": "model", "parts": [first_q]})


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
        st["goal_turns"] = []
        await msg.reply_text(message_after_name(name))
        return

    if step == OB_GOAL_DIALOG:
        turns = st.setdefault("goal_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        if _vague_user_streak(turns) >= 2:
            substantive = _last_substantive_user_message(turns)
            if substantive:
                goal_text = f"{substantive} (уточним в процессе)"
                st["main_goal"] = goal_text[:2000]
                st["step"] = OB_ASK_MORNING_TIME
                await msg.reply_text(
                    f"Окей, зафиксирую так: {goal_text} — по ходу уточним детали."
                )
                await msg.reply_text(MORNING_TIME_QUESTION)
                return

        prev_reply = _last_assistant_reply(turns)
        result = await _claude_goal_dialog(turns, model_names)
        reply = (result.get("reply") or "Расскажи подробнее?").strip()

        if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
            log.warning("goal_dialog: repeated reply, retrying")
            result = await _claude_goal_dialog(
                turns,
                model_names,
                extra_user_hint=(
                    "Твой прошлый ответ повторяется. Задай ДРУГОЙ вопрос "
                    "или зафиксируй цель (goal_ready: true, goal: текст)."
                ),
            )
            reply = (result.get("reply") or "").strip()
            if not reply or _normalize_text(reply) == _normalize_text(prev_reply):
                result = _fallback_goal_reply(turns)
                reply = result["reply"]

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("goal_ready") and result.get("goal"):
            st["main_goal"] = result["goal"][:2000]
            st["step"] = OB_ASK_MORNING_TIME
            await msg.reply_text(reply)
            await msg.reply_text(MORNING_TIME_QUESTION)
        else:
            await msg.reply_text(reply)
        return

    if step == OB_ASK_MORNING_TIME:
        parsed = parse_time_nl(raw, "morning")
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
        parsed = parse_time_nl(raw, "evening")
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла. Напиши вечернее время как 21:00 или «в 9 вечера»."
            )
            return
        st["evening_time"] = parsed
        st["timezone"] = "pending"
        st["step"] = OB_ASK_TIME_PER_DAY
        await msg.reply_text(TIME_PER_DAY_QUESTION)
        return

    if step == OB_ASK_TIME_PER_DAY:
        parsed = parse_time_per_day(raw)
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла. Напиши, например: 30 минут, час или два часа."
            )
            return
        st["time_per_day"] = parsed
        st["step"] = OB_WEEKLY_GOAL
        st["weekly_goal_phase"] = "proposal_sent"
        proposal = await _claude_weekly_goal_proposal(
            str(st.get("main_goal", "")),
            parsed,
            model_names,
        )
        st["weekly_goal_proposal"] = proposal
        await msg.reply_text(proposal)
        return

    if step == OB_WEEKLY_GOAL:
        phase = str(st.get("weekly_goal_phase") or "proposal_sent")

        if phase == "awaiting_custom":
            custom = raw.strip()[:2000]
            if len(custom) < 3:
                await msg.reply_text("Напиши коротко — что хочешь сделать за первую неделю.")
                return
            st["weekly_goal"] = custom
            await _complete_onboarding(
                update,
                context,
                cid,
                st,
                onboarding,
                histories,
                user_profiles,
                subscribers,
                model_names,
            )
            return

        if _is_weekly_goal_agreement(raw):
            proposal = str(st.get("weekly_goal_proposal", "")).strip()
            st["weekly_goal"] = _extract_weekly_goal_from_proposal(proposal) or proposal
            await _complete_onboarding(
                update,
                context,
                cid,
                st,
                onboarding,
                histories,
                user_profiles,
                subscribers,
                model_names,
            )
            return

        if _is_weekly_goal_disagreement(raw):
            st["weekly_goal_phase"] = "awaiting_custom"
            await msg.reply_text("Окей — напиши свой вариант цели на первую неделю.")
            return

        custom = raw.strip()[:2000]
        if len(custom) >= 8:
            st["weekly_goal"] = custom
            await _complete_onboarding(
                update,
                context,
                cid,
                st,
                onboarding,
                histories,
                user_profiles,
                subscribers,
                model_names,
            )
            return

        await msg.reply_text(
            'Напиши «да» / «подходит» или свой вариант цели на первую неделю.'
        )
        return

    await msg.reply_text("Что-то сбилось. Нажми /start — начнём сначала.")
