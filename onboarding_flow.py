"""Онбординг SpiceSpace — 12-Week Year: мечта → цель → тактики → время."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes

import db
from claude_client import generate as claude_generate
from prompts import (
    CHANGE_WEEKLY_GOAL_SYSTEM,
    GOAL_DIALOG_SYSTEM,
    GOAL_POLISH_PROMPT,
    NAME_EXTRACT_PROMPT,
    VISION_DIALOG_SYSTEM,
    WEEKLY_TACTICS_DIALOG_SYSTEM,
)
from summaries import save_onboarding_summary

if TYPE_CHECKING:
    pass

log = logging.getLogger("coach_bot")

BOT_BUILD = "12week-change-goal-v1"

OB_RETURNING = 0
OB_NAME = 1
OB_VISION = 2
OB_VISION_DIALOG = 3
OB_GOAL_12W = 4
OB_GOAL_DIALOG = 5
OB_WEEKLY_TACTICS = 6
OB_MORNING_TIME = 7
OB_EVENING_TIME = 8
OB_DONE = 9
OB_CHANGE_WEEKLY = 10
OB_CHANGE_12W = 11

GOAL_TYPE_12W = "12-недельная цель"
GOAL_TYPE_WEEKLY = "цель на неделю"

_GOAL_CONFIRM_YES = (
    "да",
    "ок",
    "окей",
    "верно",
    "верна",
    "подходит",
    "соглас",
    "согласна",
    "yes",
    "ага",
    "угу",
    "именно",
    "точно",
    "всё верно",
    "все верно",
    "записывай",
    "запиши",
)

_GOAL_CONFIRM_NO = (
    "нет",
    "не верно",
    "не то",
    "не подходит",
    "измени",
    "перепиши",
    "другой",
    "другая",
    "не надо",
)

# Совместимость с main.py
OB_ASK_NAME = OB_NAME
OB_ASK_MORNING_TIME = OB_MORNING_TIME
OB_ASK_EVENING_TIME = OB_EVENING_TIME

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

MORNING_TIME_QUESTION = (
    "Когда тебе удобнее всего побыть наедине с собой — без детей, без работы, без суеты? 🌅\n\n"
    "В это время я буду писать тебе чтобы сосредоточиться на твоей цели. "
    "Лучше выбирать утро или первую половину дня."
)

EVENING_TIME_QUESTION = "И ещё — в какое время вечером мне спрашивать как прошёл день? 🌙"

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
    "беру",
    "возьму",
    "выбираю",
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


def _seed_from_profile(st: dict, profile: dict) -> None:
    st.setdefault("name", str(profile.get("name") or "подруга").strip())
    st.setdefault("main_goal", str(profile.get("main_goal") or profile.get("final_goal") or "").strip())
    st.setdefault("vision", str(profile.get("vision") or "").strip())
    st.setdefault("weekly_goal", str(profile.get("weekly_goal") or "").strip())
    st.setdefault("morning_time", profile.get("morning_time") or profile.get("daily_time") or "09:30")
    st.setdefault("evening_time", profile.get("evening_time") or "21:00")
    st.setdefault("timezone", profile.get("timezone") or _default_timezone())
    st.setdefault("time_per_day", profile.get("time_per_day") or "30 минут")
    if profile.get("has_kids") is not None:
        st["has_kids"] = profile.get("has_kids")


def change_weekly_opening(profile: dict) -> str:
    main = str(profile.get("main_goal") or profile.get("final_goal") or "твоя цель").strip()
    return (
        "Окей, давай переформулируем эту неделю.\n"
        f"Цель на 12 недель у нас: {main}\n"
        "Что хочешь сделать на этой неделе чтобы двигаться к ней?"
    )


def change_12w_choice_prompt() -> str:
    return (
        "Хочешь начать новый 12-недельный цикл с новой целью — "
        "или просто скорректировать текущую?"
    )


def change_12w_adjust_opening(main_goal: str) -> str:
    g = (main_goal or "твоя цель").strip()
    return (
        f"Окей, давай уточним. Сейчас твоя цель: {g}\n"
        "Что хочешь изменить?"
    )


def start_change_weekly(onboarding: dict[int, dict], cid: int, profile: dict) -> None:
    st: dict = {"step": OB_CHANGE_WEEKLY, "change_mode": "weekly_only", "weekly_turns": []}
    _seed_from_profile(st, profile)
    onboarding[cid] = st


def start_change_12w(onboarding: dict[int, dict], cid: int, profile: dict) -> None:
    st: dict = {"step": OB_CHANGE_12W, "change_12w_phase": "choice"}
    _seed_from_profile(st, profile)
    onboarding[cid] = st


def _wants_new_cycle_reply(raw: str) -> bool:
    low = (raw or "").strip().lower()
    return any(
        w in low
        for w in (
            "новый цикл",
            "новый",
            "заново",
            "сначала",
            "с нуля",
            "помечта",
            "мечта",
            "vision",
            "12 недель заново",
        )
    )


def _wants_adjust_reply(raw: str) -> bool:
    low = (raw or "").strip().lower()
    return any(
        w in low
        for w in (
            "скоррект",
            "уточн",
            "подправ",
            "изменить",
            "поменять",
            "текущ",
            "чуть",
            "немного",
            "поправ",
        )
    )


def message_vision(name: str) -> str:
    n = (name or "").strip() or "подруга"
    return (
        f"{n}, приятно познакомиться 💙\n\n"
        "Прежде чем ставить цели — давай помечтаем.\n\n"
        "Представь: прошло 3 месяца, и всё получилось именно так как ты хотела. "
        "Как выглядит твой день? Что изменилось в твоей жизни?"
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


def _last_assistant_reply(turns: list[dict]) -> str:
    for turn in reversed(turns):
        if turn.get("role") == "assistant":
            return str(turn.get("content", "")).strip()
    return ""


def _bool_flag(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "да")
    return False


def _parse_json_dialog(text: str, message_key: str = "message") -> dict | None:
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
    reply = str(
        data.get(message_key) or data.get("reply") or data.get("message") or ""
    ).strip()
    if not reply:
        return None
    return data


def _parse_vision_dialog_json(text: str) -> dict | None:
    data = _parse_json_dialog(text)
    if not data:
        return None
    reply = str(data.get("message") or data.get("reply") or "").strip()
    if not reply:
        return None
    return {
        "message": reply,
        "ready_for_goal": _bool_flag(data.get("ready_for_goal")),
    }


def _parse_weekly_change_json(text: str) -> dict | None:
    data = _parse_json_dialog(text)
    if not data:
        return None
    reply = str(data.get("message") or data.get("reply") or "").strip()
    if not reply:
        return None
    ready = _bool_flag(data.get("ready")) or _bool_flag(data.get("goal_ready"))
    return {
        "message": reply,
        "ready": ready,
        "weekly_goal": str(data.get("weekly_goal") or data.get("goal") or "").strip(),
    }


def _parse_goal_dialog_json(text: str) -> dict | None:
    data = _parse_json_dialog(text)
    if not data:
        return None
    reply = str(data.get("message") or data.get("reply") or "").strip()
    if not reply:
        return None
    ready = _bool_flag(data.get("ready")) or _bool_flag(data.get("goal_ready"))
    return {
        "message": reply,
        "ready": ready,
        "goal": str(data.get("goal", "")).strip(),
    }


def _collect_vision_from_turns(turns: list[dict]) -> str:
    parts = [
        str(t.get("content", "")).strip()
        for t in turns
        if t.get("role") == "user" and str(t.get("content", "")).strip()
    ]
    return "\n\n".join(parts)[:4000]


def _fallback_vision_reply(turns: list[dict]) -> dict:
    user_texts = [t["content"] for t in turns if t.get("role") == "user"]
    n = len(user_texts)
    if n >= 2:
        return {
            "message": (
                "Слышу тебя — картина уже вырисовывается 💙 "
                "Окей, из всего этого — что самое важное реализовать за эти 12 недель?"
            ),
            "ready_for_goal": True,
        }
    return {
        "message": "Расскажи подробнее — что в этом дне для тебя самое важное?",
        "ready_for_goal": False,
    }


def _format_dialog_history(turns: list[dict], *, exclude_last: bool = False) -> str:
    use_turns = turns[:-1] if exclude_last and turns else turns
    lines: list[str] = []
    for t in use_turns:
        if t.get("role") not in ("user", "assistant"):
            continue
        content = str(t.get("content", "")).strip()
        if not content:
            continue
        label = "Пользователь" if t.get("role") == "user" else "Спейс"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)[:4000] if lines else "Начало диалога"


def _fallback_goal_reply(turns: list[dict]) -> dict:
    user_texts = [t["content"] for t in turns if t["role"] == "user"]
    last = (user_texts[-1] if user_texts else "").strip()
    if len(user_texts) >= 3 and len(last) >= 20:
        return {
            "message": f"Получается твоя цель: {last}. Так?",
            "ready": False,
            "goal": "",
        }
    return {
        "message": "Расскажи подробнее — что именно хочешь изменить за эти 12 недель?",
        "ready": False,
        "goal": "",
    }


async def _extract_name(user_message: str, model_names: list[str]) -> str:
    raw = (user_message or "").strip()
    if not raw:
        return "подруга"
    prompt = NAME_EXTRACT_PROMPT.format(
        user_message=raw.replace('"', "'")[:500],
    )

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system="Верни только имя, одно слово или имя целиком.",
                    max_tokens=60,
                    cache_core=False,
                ).strip()
                if text:
                    name = text.strip().strip('"').strip("'")[:120]
                    if name:
                        return name
            except Exception as e:
                log.warning("extract_name %s: %s", mid, e)
        parts = [p for p in raw.split() if p.strip()]
        if parts:
            return parts[-1][:120]
        return "подруга"

    return await asyncio.to_thread(call)


async def _claude_vision_dialog(
    vision_turns: list[dict],
    model_names: list[str],
    *,
    extra_user_hint: str = "",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in vision_turns
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
                    system=VISION_DIALOG_SYSTEM,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_vision_dialog_json(text)
                if parsed:
                    return parsed
                log.warning("vision_dialog bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("vision_dialog %s: %s", mid, e, exc_info=True)
        return _fallback_vision_reply(vision_turns)

    return await asyncio.to_thread(call)


async def _claude_change_weekly_dialog(
    turns: list[dict],
    model_names: list[str],
    *,
    main_goal: str,
    extra_user_hint: str = "",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    system = CHANGE_WEEKLY_GOAL_SYSTEM.format(
        main_goal=main_goal or "не указана",
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    messages,
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_weekly_change_json(text)
                if parsed:
                    return parsed
                log.warning("change_weekly bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("change_weekly %s: %s", mid, e, exc_info=True)
        last_user = ""
        for t in reversed(turns):
            if t.get("role") == "user":
                last_user = str(t.get("content", "")).strip()
                break
        if len(last_user) >= 8:
            return {
                "message": "Записала — звучит конкретно.",
                "ready": True,
                "weekly_goal": last_user[:2000],
            }
        return {
            "message": "Что именно хочешь сделать на этой неделе — одним предложением?",
            "ready": False,
            "weekly_goal": "",
        }

    return await asyncio.to_thread(call)


def _is_goal_confirm_yes(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if low in _GOAL_CONFIRM_YES or low.startswith("да"):
        return True
    return any(w in low for w in ("верно", "подходит", "соглас", "записывай", "запиши"))


def _is_goal_confirm_no(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    return any(low.startswith(w) or w in low for w in _GOAL_CONFIRM_NO)


async def _polish_goal(
    raw_goal: str,
    goal_type: str,
    model_names: list[str],
) -> str:
    raw = (raw_goal or "").strip()
    if not raw:
        return ""
    prompt = GOAL_POLISH_PROMPT.format(
        raw_goal=raw.replace('"', "'")[:1500],
        goal_type=goal_type,
    )

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system="Верни только отшлифованную цель, без кавычек и пояснений.",
                    max_tokens=220,
                    cache_core=False,
                ).strip()
                if text:
                    return text.strip().strip('"').strip()[:2000]
            except Exception as e:
                log.warning("polish_goal %s: %s", mid, e)
        return raw[:2000]

    return await asyncio.to_thread(call)


async def _propose_goal_confirm(
    msg,
    st: dict,
    *,
    field: str,
    raw: str,
    goal_type: str,
    model_names: list[str],
    after: str,
) -> None:
    polished = await _polish_goal(raw, goal_type, model_names)
    if not polished.strip():
        polished = (raw or "").strip()[:2000]
    st["goal_confirm"] = {
        "field": field,
        "polished": polished,
        "raw": (raw or "").strip()[:2000],
        "after": after,
        "goal_type": goal_type,
    }
    await msg.reply_text(f"Записала ✨ {polished.strip()}. Верно?")


async def _finish_change_weekly(
    cid: int,
    new_weekly: str,
    user_profiles: dict[str, dict],
    onboarding: dict[int, dict],
) -> str:
    weekly = new_weekly.strip()
    profile = db.update_profile(
        cid,
        {
            "weekly_goal": weekly[:2000],
            "weekly_score": 0,
        },
    )
    user_profiles[str(cid)] = profile
    onboarding.pop(cid, None)
    return f"Записала 💙 Цель на эту неделю: {weekly}. Погнали."


async def _dispatch_goal_confirm_after(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cid: int,
    st: dict,
    onboarding: dict[int, dict],
    histories: dict[int, list],
    user_profiles: dict[str, dict],
    subscribers: set[int],
    model_names: list[str],
    after: str,
) -> None:
    msg = update.message
    if not msg:
        return

    if after == "main_to_weekly":
        st["step"] = OB_WEEKLY_TACTICS
        if st.get("weekly_turns"):
            return
        main = str(st.get("main_goal") or "").strip()
        await msg.reply_text(f"Отлично 🎯 Теперь первая неделя.")
        await _start_weekly_tactics_dialog(msg, st, model_names)
        return

    if after == "weekly_to_morning":
        st["step"] = OB_MORNING_TIME
        await msg.reply_text(MORNING_TIME_QUESTION)
        return

    if after == "finish_weekly":
        weekly = str(st.get("weekly_goal") or "").strip()
        done_msg = await _finish_change_weekly(
            cid, weekly, user_profiles, onboarding
        )
        await msg.reply_text(done_msg)
        return

    if after == "finish_12w":
        done_msg = await _finish_change_12w(cid, st, user_profiles, onboarding)
        await msg.reply_text(done_msg)
        return

    log.warning("unknown goal_confirm after=%s cid=%s", after, cid)


async def _finish_change_12w(
    cid: int,
    st: dict,
    user_profiles: dict[str, dict],
    onboarding: dict[int, dict],
) -> str:
    main_goal = str(st.get("main_goal") or "").strip()
    weekly_goal = str(st.get("weekly_goal") or "").strip()
    fields: dict = {
        "main_goal": main_goal[:2000],
        "weekly_goal": weekly_goal[:2000],
        "raw_goal": main_goal[:2000],
        "final_goal": main_goal[:2000],
        "current_week": 1,
        "weekly_score": 0,
    }
    vision = str(st.get("vision") or "").strip()
    if vision:
        fields["vision"] = vision[:4000]
    profile = db.update_profile(cid, fields)
    user_profiles[str(cid)] = profile
    onboarding.pop(cid, None)
    return (
        f"Записала ✨ Новая цель на 12 недель: {main_goal}\n"
        f"На эту неделю: {weekly_goal}\n"
        "Погнали с чистого листа 💙"
    )


async def _claude_goal_dialog(
    goal_turns: list[dict],
    model_names: list[str],
    *,
    vision: str = "",
    extra_user_hint: str = "",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in goal_turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    dialog_history = _format_dialog_history(goal_turns, exclude_last=True)
    system = GOAL_DIALOG_SYSTEM.format(
        vision=(vision or "не указана").strip()[:2000],
        dialog_history=dialog_history,
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    messages,
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_goal_dialog_json(text)
                if parsed:
                    return parsed
                log.warning("goal_dialog bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("goal_dialog %s: %s", mid, e, exc_info=True)
        return _fallback_goal_reply(goal_turns)

    return await asyncio.to_thread(call)


def _fallback_weekly_tactics_reply(
    turns: list[dict],
    *,
    main_goal: str,
    user_message: str,
) -> dict:
    last_user = (user_message or "").strip()
    if not last_user:
        g = (main_goal or "твоя цель")[:80]
        return {
            "message": (
                f"Что хочешь сделать на этой неделе чтобы приблизиться к «{g}»? "
                f"Можно: собрать первых тестировщиков / настроить оплату / запустить первый контент. "
                f"Или предложи своё."
            ),
            "ready": False,
            "weekly_goal": "",
        }
    if len(last_user) >= 12:
        return {
            "message": f"Окей, как поймёшь что «{last_user[:80]}» на этой неделе выполнено?",
            "ready": False,
            "weekly_goal": "",
        }
    return {
        "message": "Расскажи — что хочешь сделать на этой неделе?",
        "ready": False,
        "weekly_goal": "",
    }


async def _claude_weekly_tactics_dialog(
    weekly_turns: list[dict],
    model_names: list[str],
    *,
    main_goal: str,
    user_message: str,
    extra_user_hint: str = "",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in weekly_turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    # Claude needs at least one message — add system instruction as user message if empty
    if not messages:
        messages = [{"role": "user", "content": f"Предложи 2-3 варианта задач на первую неделю для цели: {main_goal}"}]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    dialog_history = _format_dialog_history(weekly_turns, exclude_last=True)
    if dialog_history == "Начало диалога":
        dialog_history = ""
    system = WEEKLY_TACTICS_DIALOG_SYSTEM.format(
        main_goal=(main_goal or "не указана").strip()[:2000],
        user_message=(user_message or "").strip()[:2000] or "",
        dialog_history=dialog_history,
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    messages,
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_weekly_change_json(text)
                if parsed:
                    return parsed
                log.warning("weekly_tactics_dialog bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("weekly_tactics_dialog %s: %s", mid, e, exc_info=True)
        log.error(
            "weekly_tactics_dialog: all models failed, using fallback. main_goal=%s",
            (main_goal or "")[:100],
        )
        return _fallback_weekly_tactics_reply(
            weekly_turns,
            main_goal=main_goal,
            user_message=user_message,
        )

    return await asyncio.to_thread(call)


async def _start_weekly_tactics_dialog(
    msg,
    st: dict,
    model_names: list[str],
) -> None:
    st["weekly_turns"] = []
    result = await _claude_weekly_tactics_dialog(
        [],
        model_names,
        main_goal=str(st.get("main_goal") or ""),
        user_message="",
    )
    reply = (result.get("message") or "Что хочешь сделать на этой неделе?").strip()
    st["weekly_turns"].append({"role": "assistant", "content": reply[:2000]})
    await msg.reply_text(reply)


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


def _is_weekly_goal_agreement(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if low in _WEEKLY_GOAL_AGREE or low.startswith("да"):
        return True
    return any(w in low for w in ("подходит", "давай", "соглас", "окей", "беру", "возьму"))


def _is_weekly_goal_disagreement(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    return any(low.startswith(w) or w in low for w in _WEEKLY_GOAL_DISAGREE)


def _pick_weekly_tactic_from_reply(raw: str, options: str) -> str:
    text = (raw or "").strip()
    low = text.lower()
    parts = [p.strip() for p in re.split(r"\s*/\s*", options or "") if p.strip()]
    if not parts:
        return text[:2000]
    if re.search(r"\b1\b|перв|вариант\s*1", low) and len(parts) >= 1:
        return parts[0][:2000]
    if re.search(r"\b2\b|втор|вариант\s*2", low) and len(parts) >= 2:
        return parts[1][:2000]
    if re.search(r"\b3\b|трет|вариант\s*3", low) and len(parts) >= 3:
        return parts[2][:2000]
    for part in parts:
        if part.lower() in low or low in part.lower():
            return part[:2000]
    if _is_weekly_goal_agreement(raw) and parts:
        return parts[0][:2000]
    return text[:2000]


def looks_like_time_update_request(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if any(
        p in low
        for p in (
            "обновить время",
            "изменить время",
            "поменять время",
            "сменить время",
            "утреннее время",
            "вечернее время",
            "время утр",
            "время вечер",
            "поменять утро",
            "поменять вечер",
        )
    ):
        return True
    if "время" in low and any(
        v in low for v in ("обнов", "измен", "помен", "смен", "настро", "постав")
    ):
        return True
    if ("утр" in low or "вечер" in low) and any(
        v in low for v in ("обнов", "измен", "помен", "смен", "время")
    ):
        return True
    return False


def _mini_app_reply_markup(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    url = str(context.bot_data.get("mini_app_url") or os.getenv("MINI_APP_URL") or "").strip().rstrip("/")
    if not url:
        url = "https://spicespace-production.up.railway.app/webapp"
    elif not url.endswith("/webapp"):
        url = f"{url}/webapp"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Открыть SpiceSpace", web_app=WebAppInfo(url=url))],
    ])


async def _reply_time_update_via_miniapp(
    msg,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await msg.reply_text(
        "Зайди в мини апп — там кнопка ✏️ Изменить рядом со временем. Меняется за секунду 👇",
        reply_markup=_mini_app_reply_markup(context),
    )


def looks_like_restart_onboarding(raw: str) -> bool:
    if looks_like_time_update_request(raw):
        return False
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
        "vision": str(st.get("vision", "")).strip()[:4000],
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
        "time_per_day": str(st.get("time_per_day") or "30 минут").strip()[:200],
    }
    try:
        tz = ZoneInfo(str(profile.get("timezone") or _default_timezone()))
    except Exception:
        tz = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    profile["cycle_start_date"] = datetime.now(tz).date().isoformat()
    db.upsert_profile(cid, profile)
    db.save_subscriber(cid, True)
    save_onboarding_summary(cid, profile, model_names)
    return profile


def start_new_onboarding(onboarding: dict[int, dict], cid: int) -> None:
    onboarding[cid] = {"step": OB_NAME}


def start_returning_choice(onboarding: dict[int, dict], cid: int) -> None:
    onboarding[cid] = {"step": OB_RETURNING}


def start_reonboarding(onboarding: dict[int, dict], cid: int, name: str) -> None:
    onboarding[cid] = {
        "step": OB_VISION_DIALOG,
        "name": name,
        "vision_turns": [],
    }


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
                f"[SpiceSpace] Онбординг: {name}, цель 12 нед — {main_goal}, "
                f"неделя 1 — {weekly_goal}, утро {mt}, вечер {et}."
            ],
        }
    ]

    progress_kb = None
    fn = context.bot_data.get("progress_reply_keyboard")
    if callable(fn):
        progress_kb = fn()

    await msg.reply_text(
        f"Всё запомнила ✨\n\n"
        f"Цель на 12 недель: {main_goal}\n"
        f"Первая неделя: {weekly_goal}\n\n"
        f"Буду писать утром в {mt} и вечером в {et}.\n\n"
        "Погнали 💙",
        reply_markup=progress_kb,
    )


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

    if looks_like_time_update_request(raw):
        onboarding.pop(cid, None)
        await _reply_time_update_via_miniapp(msg, context)
        return True

    if looks_like_restart_onboarding(raw):
        start_reonboarding(onboarding, cid, name)
        await msg.reply_text(message_vision(name))
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

    st = onboarding.setdefault(cid, {"step": OB_NAME})
    step = int(st.get("step") or OB_NAME)
    _note_kids_from_answer(st, raw)
    model_names = context.bot_data.get("claude_model_names") or []

    if st.get("goal_confirm"):
        confirm = st["goal_confirm"]
        if _is_goal_confirm_yes(raw):
            field = str(confirm.get("field") or "weekly_goal")
            st[field] = str(confirm.get("polished") or "")[:2000]
            after = str(confirm.get("after") or "")
            st.pop("goal_confirm", None)
            await _dispatch_goal_confirm_after(
                update,
                context,
                cid,
                st,
                onboarding,
                histories,
                user_profiles,
                subscribers,
                model_names,
                after,
            )
            return
        if _is_goal_confirm_no(raw):
            st.pop("goal_confirm", None)
            if confirm.get("goal_type") == GOAL_TYPE_12W:
                hint = "12-недельную цель"
            else:
                hint = "цель на эту неделю"
            await msg.reply_text(
                f"Окей, напиши {hint} своими словами — как хочешь чтобы звучало."
            )
            return
        await msg.reply_text('Напиши «да» / «верно» — или перепиши цель.')
        return

    if step == OB_CHANGE_WEEKLY:
        turns = st.setdefault("weekly_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        prev_reply = _last_assistant_reply(turns)
        result = await _claude_change_weekly_dialog(
            turns,
            model_names,
            main_goal=str(st.get("main_goal") or ""),
        )
        reply = (result.get("message") or "Расскажи подробнее?").strip()

        if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
            result = await _claude_change_weekly_dialog(
                turns,
                model_names,
                main_goal=str(st.get("main_goal") or ""),
                extra_user_hint="Задай другой вопрос или подтверди недельную цель (ready: true).",
            )
            reply = (result.get("message") or "").strip() or reply

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready") and result.get("weekly_goal"):
            await msg.reply_text(reply)
            await _propose_goal_confirm(
                msg,
                st,
                field="weekly_goal",
                raw=result["weekly_goal"],
                goal_type=GOAL_TYPE_WEEKLY,
                model_names=model_names,
                after="finish_weekly",
            )
        else:
            await msg.reply_text(reply)
        return

    if step == OB_CHANGE_12W:
        phase = str(st.get("change_12w_phase") or "choice")
        if phase == "choice":
            if _wants_new_cycle_reply(raw):
                st["change_mode"] = "new_12w"
                st["change_12w_phase"] = "vision"
                st["step"] = OB_VISION_DIALOG
                st["vision_turns"] = []
                name = str(st.get("name") or "подруга")
                await msg.reply_text(message_vision(name))
                return
            if _wants_adjust_reply(raw):
                st["change_mode"] = "adjust_12w"
                st["change_12w_phase"] = "goal"
                st["step"] = OB_GOAL_DIALOG
                st["goal_turns"] = []
                await msg.reply_text(change_12w_adjust_opening(str(st.get("main_goal") or "")))
                return
            await msg.reply_text(
                'Напиши «новый цикл» — начнём с мечты заново, '
                'или «скорректировать» — уточним текущую цель.'
            )
            return
        await msg.reply_text("Что-то сбилось. Напиши «поменять цель» ещё раз.")
        return

    if step == OB_NAME:
        name = (await _extract_name(raw, model_names)).strip()[:120] or "подруга"
        st["name"] = name
        st["step"] = OB_VISION_DIALOG
        st["vision_turns"] = []
        await msg.reply_text(message_vision(name))
        return

    if step == OB_VISION_DIALOG:
        turns = st.setdefault("vision_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        prev_reply = _last_assistant_reply(turns)
        result = await _claude_vision_dialog(turns, model_names)
        reply = (result.get("message") or "Расскажи ещё чуть-чуть?").strip()

        if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
            result = await _claude_vision_dialog(
                turns,
                model_names,
                extra_user_hint="Не повторяй прошлый ответ — отрази по-новому или переходи к цели на 12 недель.",
            )
            reply = (result.get("message") or "").strip() or reply

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready_for_goal"):
            st["vision"] = _collect_vision_from_turns(turns)
            st["step"] = OB_GOAL_DIALOG
            st["goal_turns"] = []
            await msg.reply_text(reply)
        else:
            await msg.reply_text(reply)
        return

    if step == OB_GOAL_DIALOG:
        turns = st.setdefault("goal_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        prev_reply = _last_assistant_reply(turns)
        result = await _claude_goal_dialog(
            turns,
            model_names,
            vision=str(st.get("vision") or ""),
        )
        reply = (result.get("message") or "Расскажи подробнее?").strip()

        if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
            result = await _claude_goal_dialog(
                turns,
                model_names,
                vision=str(st.get("vision") or ""),
                extra_user_hint=(
                    "Предложи конкретную формулировку цели («Получается твоя цель: … Так?») "
                    "или задай другой уточняющий вопрос. ready=true только после согласия пользователя."
                ),
            )
            reply = (result.get("message") or "").strip() or reply

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready") and result.get("goal"):
            await _propose_goal_confirm(
                msg,
                st,
                field="main_goal",
                raw=result["goal"],
                goal_type=GOAL_TYPE_12W,
                model_names=model_names,
                after="main_to_weekly",
            )
        else:
            await msg.reply_text(reply)
        return

    async def _complete_weekly_tactics_pick(weekly_goal: str) -> None:
        mode = str(st.get("change_mode") or "")
        after = (
            "finish_12w"
            if mode in ("adjust_12w", "new_12w")
            else "weekly_to_morning"
        )
        await _propose_goal_confirm(
            msg,
            st,
            field="weekly_goal",
            raw=weekly_goal,
            goal_type=GOAL_TYPE_WEEKLY,
            model_names=model_names,
            after=after,
        )

    if step == OB_MORNING_TIME:
        parsed = parse_time_nl(raw, "morning")
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла. Напиши, пожалуйста, в формате 09:30."
            )
            return
        st["morning_time"] = parsed
        st["step"] = OB_EVENING_TIME
        await msg.reply_text(EVENING_TIME_QUESTION)
        return

    if step == OB_EVENING_TIME:
        parsed = parse_time_nl(raw, "evening")
        if not parsed:
            await msg.reply_text(
                "Не совсем поняла. Напиши вечернее время как 21:00 или «в 9 вечера»."
            )
            return
        st["evening_time"] = parsed
        st["timezone"] = "pending"
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

    if step == OB_WEEKLY_TACTICS:
        turns = st.setdefault("weekly_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        prev_reply = _last_assistant_reply(turns)
        result = await _claude_weekly_tactics_dialog(
            turns,
            model_names,
            main_goal=str(st.get("main_goal") or ""),
            user_message=raw,
        )
        reply = (result.get("message") or "Расскажи подробнее?").strip()

        if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
            result = await _claude_weekly_tactics_dialog(
                turns,
                model_names,
                main_goal=str(st.get("main_goal") or ""),
                user_message=raw,
                extra_user_hint=(
                    "Не повторяй прошлый ответ. Учти что написал пользователь. "
                    "Если отверг твои варианты — работай только с её формулировкой."
                ),
            )
            reply = (result.get("message") or "").strip() or reply

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready") and result.get("weekly_goal"):
            await _complete_weekly_tactics_pick(result["weekly_goal"])
        else:
            await msg.reply_text(reply)
        return

    await msg.reply_text("Что-то сбилось. Нажми /start — начнём сначала.")
