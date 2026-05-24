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


def upsert_daily_summary(
    user_id: int | str,
    on_date: date,
    *,
    summary: str,
    mood: str,
    key_detail: str,
    task: str = "",
    completed: bool | None = None,
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
    user_days[d] = entry
    _save_json(DAILY_SUMMARIES_PATH, store)


def patch_daily_summary(
    user_id: int | str,
    on_date: date,
    **fields: Any,
) -> None:
    """Merge fields into today's summary (creates minimal row if missing)."""
    existing = get_daily_summary(user_id, on_date) or {}
    upsert_daily_summary(
        user_id,
        on_date,
        summary=str(fields.get("summary") or existing.get("summary") or ""),
        mood=str(fields.get("mood") or existing.get("mood") or ""),
        key_detail=str(fields.get("key_detail") or existing.get("key_detail") or ""),
        task=str(fields.get("task") or existing.get("task") or ""),
        completed=fields["completed"] if "completed" in fields else existing.get("completed"),
    )


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
    }


def _row_to_profile(row: dict) -> dict:
    p = dict(row)
    for key in ("last_daily_sent_date", "last_morning_sent_date", "last_evening_sent_date"):
        if p.get(key):
            p[key] = str(p[key])[:10]
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
    return p


def _row_to_summary(row: dict) -> dict:
    out = {
        "summary": row.get("summary") or "",
        "mood": row.get("mood") or "",
        "key_detail": row.get("key_detail") or "",
        "task": row.get("task") or "",
        "summary_date": str(row.get("summary_date", ""))[:10],
    }
    if row.get("completed") is not None:
        out["completed"] = bool(row["completed"])
    return out
