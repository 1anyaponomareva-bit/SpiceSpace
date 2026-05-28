"""Daily summary generation and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timezone
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
            if "completed" in data:
                out["completed"] = bool(data.get("completed"))
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
                    completed=parsed.get("completed"),
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
                    completed=parsed.get("completed"),
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
        completed=False,
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
