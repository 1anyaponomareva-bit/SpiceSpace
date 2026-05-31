"""Supabase persistence (PostgREST) with JSON fallback."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import httpx

log = logging.getLogger("coach_bot")

DATA_DIR = Path(__file__).resolve().parent
USER_PROFILES_PATH = DATA_DIR / "user_profiles.json"
SUBSCRIBERS_PATH = DATA_DIR / "subscribers.json"
DAILY_SUMMARIES_PATH = DATA_DIR / "daily_summaries.json"

_base_url = ""
_service_key = ""
_use_supabase = False
_UNSET = object()


def init_db() -> bool:
    global _base_url, _service_key, _use_supabase
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        log.info("Supabase не настроен — профили и summaries в локальных JSON")
        _use_supabase = False
        return False
    _base_url = url
    _service_key = key
    _use_supabase = True
    log.info("Supabase REST подключён")
    return True


def _headers() -> dict[str, str]:
    return {
        "apikey": _service_key,
        "Authorization": f"Bearer {_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _request(method: str, path: str, **kwargs) -> list[dict] | dict | None:
    if not _use_supabase:
        return None
    try:
        extra_headers = kwargs.pop("headers", {})
        merged_headers = {**_headers(), **extra_headers}
        url = f"{_base_url}/rest/v1/{path.lstrip('/')}"
        with httpx.Client(timeout=30.0) as client:
            r = client.request(
                method,
                url,
                headers=merged_headers,
                **kwargs,
            )
            if r.status_code >= 400:
                log.warning("Supabase %s %s -> %s %s", method, path, r.status_code, r.text[:200])
                return None
            if not r.content:
                return []
            data = r.json()
            return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    except Exception as e:
        log.exception("Supabase request %s %s: %s", method, path, e)
        return None


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def load_all_profiles() -> dict[str, dict]:
    if _use_supabase:
        rows = _request("GET", "user_profiles?select=*") or []
        out: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.pop("user_id", ""))
            if uid:
                out[uid] = _row_to_profile(row)
        if out:
            return out
    raw = _load_json(USER_PROFILES_PATH, {})
    return raw if isinstance(raw, dict) else {}


def get_profile(user_id: int | str) -> dict | None:
    key = str(user_id)
    if _use_supabase:
        rows = _request("GET", f"user_profiles?user_id=eq.{key}&limit=1") or []
        if rows and isinstance(rows[0], dict):
            row = dict(rows[0])
            row.pop("user_id", None)
            return _row_to_profile(row)
    profiles = _load_json(USER_PROFILES_PATH, {})
    p = profiles.get(key) if isinstance(profiles, dict) else None
    return p if isinstance(p, dict) else None


def delete_profile(user_id: int | str) -> None:
    key = str(user_id)
    if _use_supabase:
        _request("DELETE", f"user_profiles?user_id=eq.{key}")
        _request("DELETE", f"daily_summaries?user_id=eq.{key}")

    profiles = _load_json(USER_PROFILES_PATH, {})
    if isinstance(profiles, dict):
        profiles.pop(key, None)
        _save_json(USER_PROFILES_PATH, profiles)

    store = _load_json(DAILY_SUMMARIES_PATH, {})
    if isinstance(store, dict):
        store.pop(key, None)
        _save_json(DAILY_SUMMARIES_PATH, store)

    subs = _load_json(SUBSCRIBERS_PATH, [])
    if isinstance(subs, list):
        try:
            uid = int(key)
            subs = [x for x in subs if int(x) != uid]
            _save_json(SUBSCRIBERS_PATH, subs)
        except (TypeError, ValueError):
            pass


def update_profile(user_id: int | str, fields: dict) -> dict:
    """Merge fields into existing profile and persist."""
    key = str(user_id)
    profile = dict(get_profile(user_id) or {})
    profile.update(fields)
    upsert_profile(user_id, profile)
    return profile


def milestone_already_shown(profile: dict, days: int) -> bool:
    ms = profile.get("milestones_shown")
    if isinstance(ms, dict) and (ms.get(str(days)) or ms.get(days)):
        return True
    return bool(profile.get(f"milestone_shown_{days}"))


def mark_milestone_shown(user_id: int | str, days: int) -> dict:
    profile = dict(get_profile(user_id) or {})
    ms = profile.get("milestones_shown")
    if not isinstance(ms, dict):
        ms = {}
    ms = dict(ms)
    ms[str(days)] = True
    profile["milestones_shown"] = ms
    profile[f"milestone_shown_{days}"] = True
    upsert_profile(user_id, profile)
    return profile


def weekly_recap_sent_today(profile: dict, today: str) -> bool:
    if str(profile.get("last_weekly_recap_date") or "")[:10] == today:
        return True
    return bool(profile.get(f"weekly_sent_{today}"))


def mark_weekly_recap_sent(user_id: int | str, today: str) -> dict:
    profile = dict(get_profile(user_id) or {})
    profile["last_weekly_recap_date"] = today
    profile[f"weekly_sent_{today}"] = True
    upsert_profile(user_id, profile)
    return profile


def _milestones_shown_for_row(p: dict) -> dict:
    ms = p.get("milestones_shown")
    out: dict = {}
    if isinstance(ms, dict):
        for k, v in ms.items():
            if v:
                out[str(k)] = True
    for k, v in p.items():
        if k.startswith("milestone_shown_") and v:
            day = k[len("milestone_shown_") :]
            out[str(day)] = True
    return out


def _apply_milestones_shown_to_profile(p: dict) -> None:
    raw = p.pop("milestones_shown", None)
    if not isinstance(raw, dict):
        raw = {}
    p["milestones_shown"] = {str(k): bool(v) for k, v in raw.items() if v}
    for day, shown in p["milestones_shown"].items():
        if shown:
            p[f"milestone_shown_{day}"] = True


def upsert_profile(user_id: int | str, profile: dict) -> None:
    key = str(user_id)
    row = _profile_to_row(profile)
    row["user_id"] = int(key)

    if _use_supabase:
        _request(
            "POST",
            "user_profiles?on_conflict=user_id",
            json=row,
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    profiles = _load_json(USER_PROFILES_PATH, {})
    if not isinstance(profiles, dict):
        profiles = {}
    profiles[key] = profile
    _save_json(USER_PROFILES_PATH, profiles)


def load_subscribers() -> set[int]:
    if _use_supabase:
        rows = _request("GET", "user_profiles?daily_enabled=eq.true&select=user_id") or []
        ids = {int(r["user_id"]) for r in rows if isinstance(r, dict) and r.get("user_id")}
        if ids:
            return ids
    data = _load_json(SUBSCRIBERS_PATH, [])
    try:
        return {int(x) for x in data}
    except (TypeError, ValueError):
        return set()


def save_subscriber(user_id: int, enabled: bool) -> None:
    p = get_profile(user_id) or {}
    p["daily_enabled"] = enabled
    upsert_profile(user_id, p)

    subs = _load_json(SUBSCRIBERS_PATH, [])
    if not isinstance(subs, list):
        subs = []
    s = set()
    for x in subs:
        try:
            s.add(int(x))
        except (TypeError, ValueError):
            pass
    if enabled:
        s.add(user_id)
    else:
        s.discard(user_id)
    _save_json(SUBSCRIBERS_PATH, sorted(s))


def get_daily_summary(user_id: int | str, on_date: date) -> dict | None:
    key = str(user_id)
    d = on_date.isoformat()
    if _use_supabase:
        rows = (
            _request(
                "GET",
                f"daily_summaries?user_id=eq.{key}&summary_date=eq.{d}&limit=1",
            )
            or []
        )
        if rows and isinstance(rows[0], dict):
            return _row_to_summary(rows[0])

    store = _load_json(DAILY_SUMMARIES_PATH, {})
    if not isinstance(store, dict):
        return None
    user_days = store.get(key, {})
    if not isinstance(user_days, dict):
        return None
    row = user_days.get(d)
    return row if isinstance(row, dict) else None


def get_yesterday_summary(user_id: int | str, tz_name: str) -> dict | None:
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = datetime.now(tz).date() - timedelta(days=1)
    return get_daily_summary(user_id, yesterday)


def normalize_task_completed(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "да", "получилось"):
        return "true"
    if s in ("false", "0", "no", "нет", "не получилось"):
        return "false"
    if s in ("partial", "частично", "частич", "немного", "половина"):
        return "partial"
    return None


def upsert_daily_summary(
    user_id: int | str,
    on_date: date,
    *,
    summary: str,
    mood: str,
    key_detail: str,
    task: str = "",
    completed: bool | None = None,
    task_completed: str | None | object = _UNSET,
) -> None:
    key = str(user_id)
    d = on_date.isoformat()
    row = {
        "user_id": int(key),
        "summary_date": d,
        "summary": summary[:4000],
        "mood": mood[:200],
        "key_detail": key_detail[:500],
        "task": (task or "")[:500],
    }
    if completed is not None:
        row["completed"] = bool(completed)
    if task_completed is not _UNSET:
        tc = normalize_task_completed(task_completed) if task_completed is not None else None
        row["task_completed"] = tc

    if _use_supabase:
        _request(
            "POST",
            "daily_summaries?on_conflict=user_id,summary_date",
            json=row,
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    store = _load_json(DAILY_SUMMARIES_PATH, {})
    if not isinstance(store, dict):
        store = {}
    user_days = store.setdefault(key, {})
    if not isinstance(user_days, dict):
        user_days = {}
        store[key] = user_days
    entry = {"summary": summary, "mood": mood, "key_detail": key_detail, "task": task or ""}
    if completed is not None:
        entry["completed"] = bool(completed)
    if task_completed is not _UNSET:
        if task_completed is None:
            entry.pop("task_completed", None)
        else:
            entry["task_completed"] = normalize_task_completed(task_completed)
    user_days[d] = entry
    _save_json(DAILY_SUMMARIES_PATH, store)


def patch_daily_summary(
    user_id: int | str,
    on_date: date,
    **fields: Any,
) -> None:
    """Merge fields into today's summary (creates minimal row if missing)."""
    existing = get_daily_summary(user_id, on_date) or {}
    kwargs: dict[str, Any] = {
        "summary": str(fields.get("summary") or existing.get("summary") or ""),
        "mood": str(fields.get("mood") or existing.get("mood") or ""),
        "key_detail": str(fields.get("key_detail") or existing.get("key_detail") or ""),
        "task": str(fields.get("task") or existing.get("task") or ""),
    }
    if "completed" in fields:
        kwargs["completed"] = fields["completed"]
    if "task_completed" in fields:
        kwargs["task_completed"] = fields["task_completed"]
    else:
        kwargs["task_completed"] = _UNSET
    upsert_daily_summary(user_id, on_date, **kwargs)


def list_daily_summaries(user_id: int | str) -> list[dict]:
    key = str(user_id)
    out: list[dict] = []
    if _use_supabase:
        rows = (
            _request(
                "GET",
                f"daily_summaries?user_id=eq.{key}&select=summary_date,task_completed,completed&order=summary_date.asc",
            )
            or []
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            d = str(row.get("summary_date", ""))[:10]
            if not d:
                continue
            tc = row.get("task_completed")
            out.append({"date": d, "task_completed": normalize_task_completed(tc)})
        if out:
            return out
    store = _load_json(DAILY_SUMMARIES_PATH, {})
    user_days = store.get(key, {}) if isinstance(store, dict) else {}
    if not isinstance(user_days, dict):
        return []
    for d, row in sorted(user_days.items()):
        if not isinstance(row, dict):
            continue
        tc = row.get("task_completed")
        out.append(
            {
                "date": str(d)[:10],
                "task_completed": normalize_task_completed(tc),
            }
        )
    return out


def _profile_to_row(p: dict) -> dict:
    morning = p.get("morning_time") or p.get("daily_time") or "09:30"
    evening = p.get("evening_time") or "21:00"
    last_m = p.get("last_morning_sent_date") or p.get("last_daily_sent_date") or None
    last_e = p.get("last_evening_sent_date") or None
    return {
        "name": p.get("name"),
        "morning_routine": p.get("morning_routine"),
        "has_kids": p.get("has_kids"),
        "works": p.get("works"),
        "main_goal": p.get("main_goal"),
        "vision": p.get("vision"),
        "daily_time": morning,
        "morning_time": morning,
        "evening_time": evening,
        "timezone": p.get("timezone", "Asia/Ho_Chi_Minh"),
        "daily_enabled": p.get("daily_enabled", True),
        "last_daily_sent_date": last_m if last_m else None,
        "last_morning_sent_date": last_m if last_m else None,
        "last_evening_sent_date": last_e if last_e else None,
        "streak": p.get("streak", 0),
        "current_week": p.get("current_week", 1),
        "weekly_goal": p.get("weekly_goal"),
        "weekly_score": p.get("weekly_score", 0),
        "time_per_day": p.get("time_per_day"),
        "cycle_start_date": p.get("cycle_start_date"),
        "milestones_shown": _milestones_shown_for_row(p),
        "last_weekly_recap_date": p.get("last_weekly_recap_date") or None,
    }


def _row_to_profile(row: dict) -> dict:
    p = dict(row)
    _apply_milestones_shown_to_profile(p)
    for key in ("last_daily_sent_date", "last_morning_sent_date", "last_evening_sent_date"):
        if p.get(key):
            p[key] = str(p[key])[:10]
    if p.get("last_weekly_recap_date"):
        p["last_weekly_recap_date"] = str(p["last_weekly_recap_date"])[:10]
        p[f"weekly_sent_{p['last_weekly_recap_date']}"] = True
    if not p.get("morning_time") and p.get("daily_time"):
        p["morning_time"] = p["daily_time"]
    if not p.get("evening_time"):
        p["evening_time"] = "21:00"
    p.setdefault("daily_time", p.get("morning_time", "09:30"))
    p.setdefault("raw_goal", p.get("main_goal", ""))
    p.setdefault("final_goal", p.get("main_goal", ""))
    p.setdefault("goal_type", "qualitative")
    p.setdefault("goal_signals", [])
    p.setdefault("streak", 0)
    p.setdefault("weekly_score", 0)
    p.setdefault("completed_tasks", [])
    p.setdefault("missed_tasks", [])
    p.setdefault("current_week", 1)
    p.setdefault("vision", p.get("vision") or "")
    p.setdefault("weekly_goal", p.get("weekly_goal") or "")
    p.setdefault("time_per_day", p.get("time_per_day") or "")
    p.setdefault("cycle_start_date", p.get("cycle_start_date") or "")
    p.setdefault("last_weekly_recap_date", p.get("last_weekly_recap_date") or "")
    return p


def _row_to_summary(row: dict) -> dict:
    out = {
        "summary": row.get("summary") or "",
        "mood": row.get("mood") or "",
        "key_detail": row.get("key_detail") or "",
        "task": row.get("task") or "",
        "summary_date": str(row.get("summary_date", ""))[:10],
    }
    if row.get("created_at") is not None:
        out["created_at"] = str(row.get("created_at"))
    if row.get("updated_at") is not None:
        out["updated_at"] = str(row.get("updated_at"))
    if row.get("completed") is not None:
        out["completed"] = bool(row["completed"])
    if "task_completed" in row:
        out["task_completed"] = normalize_task_completed(row.get("task_completed"))
    return out


def save_history_turn(user_id: int | str, role: str, content: str) -> None:
    """Save one conversation turn to Supabase."""
    key = str(user_id)
    if not _use_supabase:
        return
    if not content or not content.strip():
        return
    _request(
        "POST",
        "conversation_history",
        json={
            "user_id": int(key),
            "role": role,
            "content": str(content)[:2000],
        },
        headers={**_headers(), "Prefer": "return=minimal"},
    )


def load_history(user_id: int | str, limit: int = 20) -> list[dict]:
    """Load last N conversation turns from Supabase."""
    key = str(user_id)
    if not _use_supabase:
        return []
    rows = _request(
        "GET",
        f"conversation_history?user_id=eq.{key}&order=created_at.desc&limit={limit}",
    ) or []
    turns: list[dict] = []
    for row in reversed(rows):
        if isinstance(row, dict) and row.get("role") and row.get("content"):
            turns.append(
                {
                    "role": row["role"],
                    "parts": [row["content"]],
                }
            )
    return turns


def delete_history(user_id: int | str) -> None:
    """Delete all conversation history for user."""
    key = str(user_id)
    if _use_supabase:
        _request("DELETE", f"conversation_history?user_id=eq.{key}")


def save_user_fact(user_id: int | str, fact: str, category: str = "general") -> None:
    """Save a new fact about the user."""
    key = str(user_id)
    if not _use_supabase or not fact or not fact.strip():
        return
    existing = _request("GET", f"user_facts?user_id=eq.{key}&select=fact") or []
    existing_facts = [
        str(r.get("fact", "")).lower() for r in existing if isinstance(r, dict)
    ]
    new_fact_lower = fact.strip().lower()
    for ef in existing_facts:
        if new_fact_lower in ef or ef in new_fact_lower:
            return
    _request(
        "POST",
        "user_facts",
        json={
            "user_id": int(key),
            "fact": fact.strip()[:500],
            "category": category[:50],
        },
        headers={**_headers(), "Prefer": "return=minimal"},
    )


def load_user_facts(user_id: int | str, limit: int = 20) -> list[str]:
    """Load facts about the user."""
    key = str(user_id)
    if not _use_supabase:
        return []
    rows = _request(
        "GET",
        f"user_facts?user_id=eq.{key}&order=created_at.desc&limit={limit}",
    ) or []
    return [
        str(r.get("fact", ""))
        for r in rows
        if isinstance(r, dict) and r.get("fact")
    ]


def delete_user_facts(user_id: int | str) -> None:
    """Delete all facts for user."""
    key = str(user_id)
    if _use_supabase:
        _request("DELETE", f"user_facts?user_id=eq.{key}")


def save_weekly_summary(
    user_id: int | str,
    week_number: int,
    week_start: str,
    summary: str,
    achievements: str = "",
    challenges: str = "",
    next_week_goal: str = "",
    score: int = 0,
) -> None:
    key = str(user_id)
    if not _use_supabase:
        return
    _request(
        "POST",
        "weekly_summaries?on_conflict=user_id,week_number",
        json={
            "user_id": int(key),
            "week_number": week_number,
            "week_start": week_start,
            "summary": summary[:4000],
            "achievements": achievements[:2000],
            "challenges": challenges[:2000],
            "next_week_goal": next_week_goal[:1000],
            "score": max(0, min(100, score)),
        },
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def load_weekly_summaries(user_id: int | str, limit: int = 4) -> list[dict]:
    key = str(user_id)
    if not _use_supabase:
        return []
    rows = _request(
        "GET",
        f"weekly_summaries?user_id=eq.{key}&order=week_number.desc&limit={limit}",
    ) or []
    return [r for r in rows if isinstance(r, dict)]


def load_last_weekly_summary(user_id: int | str) -> dict | None:
    rows = load_weekly_summaries(user_id, limit=1)
    return rows[0] if rows else None


def save_personality(user_id: int | str, fields: dict) -> None:
    """Upsert personality profile for user."""
    key = str(user_id)
    if not _use_supabase:
        return
    row = {
        "user_id": int(key),
        **{k: str(v)[:2000] for k, v in fields.items() if v},
    }
    row["updated_at"] = datetime.utcnow().isoformat()
    _request(
        "POST",
        "user_personality?on_conflict=user_id",
        json=row,
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def load_personality(user_id: int | str) -> dict | None:
    """Load personality profile for user."""
    key = str(user_id)
    if not _use_supabase:
        return None
    rows = _request("GET", f"user_personality?user_id=eq.{key}&limit=1") or []
    if rows and isinstance(rows[0], dict):
        return rows[0]
    return None


def delete_personality(user_id: int | str) -> None:
    key = str(user_id)
    if _use_supabase:
        _request("DELETE", f"user_personality?user_id=eq.{key}")
