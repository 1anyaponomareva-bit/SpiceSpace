"""Daily summary generation and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import anthropic

from claude_client import generate
from db import get_daily_summary, upsert_daily_summary
from prompts import DAILY_SUMMARY_PROMPT, ONBOARDING_SUMMARY_PROMPT

log = logging.getLogger("coach_bot")


def _sanitize_summary_task(task: str) -> str:
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
        )
    ):
        return ""
    if low.count("?") >= 2:
        return ""
    return t[:140]


def _parse_summary_json(text: str) -> dict | None:
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
                "task": _sanitize_summary_task(str(data.get("task", ""))),
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

    if get_daily_summary(user_id, today):
        return

    conv = _conversation_text(hist)
    if not conv.strip():
        return

    prompt = DAILY_SUMMARY_PROMPT.format(conversation=conv)

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
                log.info("daily_summary saved user=%s date=%s", user_id, today)
                return
        except (anthropic.RateLimitError, anthropic.NotFoundError):
            continue
        except Exception as e:
            log.warning("daily_summary model %s: %s", mid, e)
    log.warning("daily_summary failed user=%s", user_id)


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
    """After 3+ user messages today, generate summary once."""
    user_turns = sum(1 for t in hist if t.get("role") == "user")
    if user_turns < 3:
        return
    await asyncio.to_thread(
        save_summary_for_today, user_id, profile, hist, model_names
    )
