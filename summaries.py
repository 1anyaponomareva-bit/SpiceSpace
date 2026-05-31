"""Daily summary generation and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic

from claude_client import generate
from db import get_daily_summary, patch_daily_summary, upsert_daily_summary
from prompts import DAILY_SUMMARY_PROMPT, ONBOARDING_SUMMARY_PROMPT

log = logging.getLogger("coach_bot")


def _normalize_goal_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _task_equals_weekly_goal(task: str, weekly_goal: str) -> bool:
    a = _normalize_goal_text(task)
    b = _normalize_goal_text(weekly_goal)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 12 and len(b) >= 12 and (a in b or b in a):
        return True
    return False


def _sanitize_summary_task(task: str, weekly_goal: str = "") -> str:
    t = (task or "").strip()
    if not t or len(t) > 120:
        return ""
    low = t.lower()
    if any(
        m in low
        for m in (
            "привет",
            "доброе утро",
            "добрый день",
            "давай начн",
            "продуктивн",
            "сколько времени",
            "мне кажется",
            "я думаю",
            "слушай",
            "кстати",
        )
    ):
        return ""
    if "?" in t and len(t) < 30:
        return ""
    if low.count("?") >= 2:
        return ""
    if weekly_goal and _task_equals_weekly_goal(t, weekly_goal):
        return ""
    return t[:140]


def _parse_summary_json(text: str, weekly_goal: str = "") -> dict | None:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if isinstance(data, dict) and data.get("summary"):
            out = {
                "summary": str(data.get("summary", ""))[:4000],
                "mood": str(data.get("mood", ""))[:200],
                "key_detail": str(data.get("key_detail", ""))[:500],
                "task": _sanitize_summary_task(
                    str(data.get("task", "")), weekly_goal=weekly_goal
                ),
            }
            return out
    except json.JSONDecodeError:
        pass
    return None


def _conversation_text(hist: list[dict], max_turns: int = 20) -> str:
    lines: list[str] = []
    for turn in hist[-max_turns:]:
        role = turn.get("role")
        parts = turn.get("parts") or []
        text = (parts[0] if parts else "").strip()
        if not text:
            continue
        who = "Она" if role == "user" else "SpiceSpace"
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def save_summary_for_today(
    user_id: int,
    profile: dict,
    hist: list[dict],
    model_names: list[str],
) -> None:
    tz_name = str(profile.get("timezone") or "Asia/Ho_Chi_Minh")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
    today = datetime.now(tz).date()

    existing = get_daily_summary(user_id, today)
    if existing:
        created_at = str(
            existing.get("created_at") or existing.get("updated_at") or ""
        ).strip()
        if created_at:
            try:
                last_update = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                now_utc = datetime.now(timezone.utc)
                if (now_utc - last_update).total_seconds() < 3600:
                    return
            except Exception:
                pass

    conv = _conversation_text(hist)
    if not conv.strip():
        return

    weekly_goal = str(profile.get("weekly_goal") or "").strip()
    prompt = DAILY_SUMMARY_PROMPT.format(
        conversation=conv,
        weekly_goal=weekly_goal or "не указана",
    )

    for mid in model_names:
        try:
            raw = generate(
                mid,
                [{"role": "user", "content": prompt}],
                system="Отвечай только валидным JSON.",
                max_tokens=400,
                cache_core=False,
            )
            parsed = _parse_summary_json(raw, weekly_goal=weekly_goal)
            if parsed:
                task = parsed.get("task", "")
                if not _sanitize_summary_task(task, weekly_goal=weekly_goal):
                    task = ""
                patch_daily_summary(
                    user_id,
                    today,
                    summary=parsed["summary"],
                    mood=parsed["mood"],
                    key_detail=parsed["key_detail"],
                    task=task if task else None,
                )
                log.info("daily_summary saved user=%s date=%s", user_id, today)
                return
        except (anthropic.RateLimitError, anthropic.NotFoundError):
            continue
        except Exception as e:
            log.warning("daily_summary model %s: %s", mid, e)
    log.warning("daily_summary failed user=%s", user_id)


FACTS_EXTRACT_PROMPT = """Из этого разговора извлеки важные факты о пользователе которые стоит запомнить надолго.
Только конкретные личные факты: привычки, страхи, достижения, важные события, отношения, предпочтения.
НЕ включай: временные состояния, общие фразы, то что уже есть в профиле (имя, цель).

Формат ответа — JSON массив строк, максимум 3 факта:
["факт 1", "факт 2", "факт 3"]

Если нет важных фактов — верни пустой массив: []

Разговор:
{conversation}"""


WEEKLY_SUMMARY_PROMPT = """Подведи итог недели пользователя. Ответь JSON:
{{
    "summary": "3-5 предложений о том как прошла неделя",
    "achievements": "что получилось и чем можно гордиться",
    "challenges": "что было сложно или не получилось",
    "next_week_goal": "один конкретный фокус на следующую неделю",
    "score": число от 0 до 100 (насколько продуктивной была неделя)
}}

НЕ упоминай номер недели в тексте summary. Пиши про конкретные достижения и факты.

Профиль:
- Имя: {name}
- Цель на 12 недель: {main_goal}
- Цель этой недели: {weekly_goal}
- Неделя: {week_number} из 12

Daily summaries за эту неделю:
{daily_summaries_text}"""


def generate_weekly_summary(
    user_id: int,
    profile: dict,
    model_names: list[str],
) -> None:
    from db import get_daily_summary, save_weekly_summary

    tz_name = str(profile.get("timezone") or "Asia/Ho_Chi_Minh")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")

    today = datetime.now(tz).date()
    week_number = int(profile.get("current_week") or 1)

    daily_lines: list[str] = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        summ = get_daily_summary(user_id, d)
        if summ and summ.get("summary"):
            daily_lines.append(f"{d.isoformat()}: {summ['summary']}")
            if summ.get("task"):
                completed = summ.get("task_completed")
                status = "✅" if completed == "true" else "❌" if completed == "false" else "—"
                daily_lines.append(f"  Задача: {summ['task']} {status}")

    if not daily_lines:
        log.info("weekly_summary: no daily data for user=%s", user_id)
        return

    daily_summaries_text = "\n".join(daily_lines)
    prompt = WEEKLY_SUMMARY_PROMPT.format(
        name=profile.get("name", ""),
        main_goal=profile.get("main_goal", "не указана"),
        weekly_goal=profile.get("weekly_goal", "не указана"),
        week_number=week_number,
        daily_summaries_text=daily_summaries_text,
    )

    for mid in model_names:
        try:
            raw = generate(
                mid,
                [{"role": "user", "content": prompt}],
                system="Отвечай только валидным JSON.",
                max_tokens=600,
                cache_core=False,
            )
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                continue
            data = json.loads(m.group(0))
            if not isinstance(data, dict) or not data.get("summary"):
                continue
            save_weekly_summary(
                user_id,
                week_number=week_number,
                week_start=(today - timedelta(days=6)).isoformat(),
                summary=str(data.get("summary", "")),
                achievements=str(data.get("achievements", "")),
                challenges=str(data.get("challenges", "")),
                next_week_goal=str(data.get("next_week_goal", "")),
                score=int(data.get("score") or 0),
            )
            log.info("weekly_summary saved user=%s week=%s", user_id, week_number)
            return
        except Exception as e:
            log.warning("weekly_summary model %s: %s", mid, e)


PERSONALITY_EXTRACT_PROMPT = """Проанализируй переписку и обнови профиль личности пользователя.
Ответь JSON — заполняй только те поля о которых есть реальные данные в переписке, остальные оставь пустой строкой:
{{
    "communication_style": "как предпочитает общаться (коротко/подробно, формально/неформально)",
    "motivation_triggers": "что мотивирует и заряжает",
    "procrastination_patterns": "когда и почему откладывает дела",
    "best_time_of_day": "когда наиболее продуктивна",
    "response_to_pressure": "как реагирует на давление и дедлайны",
    "personal_values": "что важно в жизни",
    "blockers": "что мешает двигаться вперёд",
    "strengths": "сильные стороны которые проявляются",
    "raw_insights": "другие важные наблюдения о личности"
}}

ВАЖНО: пиши только то что реально видно из переписки. Не додумывай.

Переписка:
{conversation}"""


def update_personality_profile(
    user_id: int,
    profile: dict,
    hist: list[dict],
    model_names: list[str],
) -> None:
    from db import load_personality, save_personality

    conv = _conversation_text(hist, max_turns=30)
    if not conv.strip() or len(conv) < 200:
        return
    prompt = PERSONALITY_EXTRACT_PROMPT.format(conversation=conv)
    for mid in model_names:
        try:
            raw = generate(
                mid,
                [{"role": "user", "content": prompt}],
                system="Отвечай только валидным JSON.",
                max_tokens=500,
                cache_core=False,
            )
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                continue
            data = json.loads(m.group(0))
            if not isinstance(data, dict):
                continue
            fields = {k: v for k, v in data.items() if v and str(v).strip()}
            if fields:
                existing = load_personality(user_id) or {}
                merged = {}
                for key in (
                    "communication_style",
                    "motivation_triggers",
                    "procrastination_patterns",
                    "best_time_of_day",
                    "response_to_pressure",
                    "personal_values",
                    "blockers",
                    "strengths",
                    "raw_insights",
                ):
                    new_val = str(fields.get(key, "")).strip()
                    old_val = str(existing.get(key) or "").strip()
                    merged[key] = new_val if new_val else old_val
                save_personality(user_id, merged)
                log.info(
                    "personality updated user=%s fields=%s",
                    user_id,
                    list(fields.keys()),
                )
            return
        except Exception as e:
            log.warning("personality_extract model %s: %s", mid, e)


def extract_and_save_facts(
    user_id: int,
    profile: dict,
    hist: list[dict],
    model_names: list[str],
) -> None:
    from db import save_user_fact

    conv = _conversation_text(hist, max_turns=20)
    if not conv.strip() or len(conv) < 100:
        return
    prompt = FACTS_EXTRACT_PROMPT.format(conversation=conv)
    for mid in model_names:
        try:
            raw = generate(
                mid,
                [{"role": "user", "content": prompt}],
                system="Отвечай только валидным JSON массивом.",
                max_tokens=200,
                cache_core=False,
            )
            raw = raw.strip()
            m = re.search(r"\[[\s\S]*\]", raw)
            if not m:
                return
            facts = json.loads(m.group(0))
            if not isinstance(facts, list):
                return
            for fact in facts[:3]:
                if isinstance(fact, str) and len(fact.strip()) > 10:
                    save_user_fact(user_id, fact.strip())
            log.info("facts extracted user=%s count=%s", user_id, len(facts))
            return
        except Exception as e:
            log.warning("extract_facts model %s: %s", mid, e)


def save_onboarding_summary(
    user_id: int,
    profile: dict,
    model_names: list[str],
) -> None:
    tz_name = str(profile.get("timezone") or "Asia/Ho_Chi_Minh")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
    today = datetime.now(tz).date()

    prompt = ONBOARDING_SUMMARY_PROMPT.format(
        name=profile.get("name", ""),
        vision=profile.get("vision", ""),
        main_goal=profile.get("main_goal", ""),
        morning_time=profile.get("morning_time") or profile.get("daily_time", "09:30"),
        evening_time=profile.get("evening_time", "21:00"),
    )

    for mid in model_names:
        try:
            raw = generate(
                mid,
                [{"role": "user", "content": prompt}],
                system="Отвечай только валидным JSON.",
                max_tokens=400,
                cache_core=False,
            )
            parsed = _parse_summary_json(raw)
            if parsed:
                upsert_daily_summary(
                    user_id,
                    today,
                    summary=parsed["summary"],
                    mood=parsed["mood"],
                    key_detail=parsed["key_detail"],
                    task=parsed.get("task", ""),
                )
                return
        except (anthropic.RateLimitError, anthropic.NotFoundError):
            continue
        except Exception as e:
            log.warning("onboarding_summary %s: %s", mid, e)

    upsert_daily_summary(
        user_id,
        today,
        summary=f"Знакомство: {profile.get('name')}. Цель 12 нед: {profile.get('main_goal')}.",
        mood="начало",
        key_detail=str(profile.get("vision") or profile.get("main_goal", ""))[:500],
        task="",
    )


async def maybe_save_daily_summary(
    user_id: int,
    profile: dict,
    hist: list[dict],
    model_names: list[str],
) -> None:
    """After 2+ user messages today, generate/update summary."""
    user_turns = sum(1 for t in hist if t.get("role") == "user")
    if user_turns < 2:
        return
    await asyncio.to_thread(
        save_summary_for_today, user_id, profile, hist, model_names
    )
    if user_turns % 5 == 0:
        await asyncio.to_thread(
            extract_and_save_facts, user_id, profile, hist, model_names
        )
    if user_turns % 10 == 0:
        await asyncio.to_thread(
            update_personality_profile, user_id, profile, hist, model_names
        )
