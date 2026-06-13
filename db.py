"""Supabase persistence (PostgREST) with JSON fallback."""

from __future__ import annotations

import json
import logging
import os
import re
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
_supabase_profile_columns: set[str] | None = None

# Fallback when schema introspection fails (no language_code / json blobs).
_SUPABASE_PROFILE_COLUMNS_FALLBACK = frozenset(
    {
        "user_id",
        "name",
        "morning_routine",
        "has_kids",
        "works",
        "main_goal",
        "vision",
        "daily_time",
        "evening_time",
        "timezone",
        "daily_enabled",
        "last_daily_sent_date",
        "last_morning_sent_date",
        "last_evening_sent_date",
        "streak",
        "current_week",
        "weekly_goal",
        "weekly_score",
        "time_per_day",
        "cycle_start_date",
        "last_weekly_recap_date",
        "morning_time",
        "last_user_message_date",
        "reengagement_sent_date",
        "is_premium",
        "subscription_end",
        "plan",
        "trial_start_date",
    }
)


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
    refresh_supabase_profile_columns()
    return True


def normalize_time_hhmm(raw: object) -> str | None:
    """Normalize DB/UI time to HH:MM (handles 09:30:00)."""
    s = str(raw or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def sync_profile_times(profile: dict) -> dict:
    """Keep morning_time and daily_time identical (daily_time is canonical in DB)."""
    daily = normalize_time_hhmm(profile.get("daily_time"))
    morning = normalize_time_hhmm(profile.get("morning_time"))
    canonical = daily or morning or "09:30"
    profile["daily_time"] = canonical
    profile["morning_time"] = canonical
    evening = normalize_time_hhmm(profile.get("evening_time")) or "21:00"
    profile["evening_time"] = evening
    return profile


def refresh_supabase_profile_columns() -> set[str]:
    """Load user_profiles column names from Supabase (one sample row)."""
    global _supabase_profile_columns
    if not _use_supabase:
        _supabase_profile_columns = set()
        return _supabase_profile_columns
    rows = _request("GET", "user_profiles?select=*&limit=1") or []
    if rows and isinstance(rows[0], dict):
        _supabase_profile_columns = set(rows[0].keys())
        log.info(
            "Supabase user_profiles columns: %s",
            ", ".join(sorted(_supabase_profile_columns)),
        )
    else:
        _supabase_profile_columns = set(_SUPABASE_PROFILE_COLUMNS_FALLBACK)
        log.warning(
            "Could not introspect user_profiles columns — using fallback set"
        )
    return _supabase_profile_columns


def _filter_row_for_supabase(row: dict) -> dict:
    cols = _supabase_profile_columns or refresh_supabase_profile_columns()
    allowed = cols if cols else _SUPABASE_PROFILE_COLUMNS_FALLBACK
    out: dict = {}
    for key, value in row.items():
        if key not in allowed:
            continue
        if isinstance(value, (dict, list)) and key not in allowed:
            continue
        out[key] = value
    return out


def _supabase_profile_exists(key: str) -> bool:
    rows = (
        _request(
            "GET",
            f"user_profiles?user_id=eq.{key}&select=user_id&limit=1",
        )
        or []
    )
    return bool(rows and isinstance(rows[0], dict))


def _write_profile_to_supabase(key: str, row: dict) -> bool:
    """PATCH existing row or INSERT minimal row; only sends columns present in DB."""
    patch_body = _filter_row_for_supabase({k: v for k, v in row.items() if k != "user_id"})
    if not patch_body:
        return False

    if _supabase_profile_exists(key):
        result = _request(
            "PATCH",
            f"user_profiles?user_id=eq.{key}",
            json=patch_body,
            headers={**_headers(), "Prefer": "return=representation"},
        )
        if result:
            return True
        # Retry without dict/list fields (milestones_shown, cycle_flags, …)
        scalar_body = {
            k: v
            for k, v in patch_body.items()
            if not isinstance(v, (dict, list))
        }
        if scalar_body and scalar_body != patch_body:
            result = _request(
                "PATCH",
                f"user_profiles?user_id=eq.{key}",
                json=scalar_body,
                headers={**_headers(), "Prefer": "return=representation"},
            )
            if result:
                return True

    insert_row = dict(patch_body)
    insert_row["user_id"] = int(key)
    result = _request(
        "POST",
        "user_profiles",
        json=insert_row,
        headers={**_headers(), "Prefer": "return=representation"},
    )
    if result:
        return True

    scalar_insert = {
        k: v for k, v in insert_row.items() if not isinstance(v, (dict, list))
    }
    if scalar_insert != insert_row:
        result = _request(
            "POST",
            "user_profiles",
            json=scalar_insert,
            headers={**_headers(), "Prefer": "return=representation"},
        )
        if result:
            return True

    log.error(
        "Supabase profile write failed uid=%s keys=%s",
        key,
        sorted(patch_body.keys()),
    )
    return False


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
            return sync_profile_times(_row_to_profile(row))
    profiles = _load_json(USER_PROFILES_PATH, {})
    p = profiles.get(key) if isinstance(profiles, dict) else None
    if isinstance(p, dict):
        return sync_profile_times(p)
    return None


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
    profile = sync_profile_times(dict(get_profile(user_id) or {}))
    profile.update(fields)
    sync_profile_times(profile)
    ok = upsert_profile(user_id, profile)
    fresh = get_profile(user_id)
    merged = fresh if isinstance(fresh, dict) else profile
    if _use_supabase and not ok:
        log.error("update_profile Supabase write failed uid=%s fields=%s", key, list(fields))
    return merged


def patch_profile_times(
    user_id: int | str,
    *,
    morning_time: str | None = None,
    evening_time: str | None = None,
) -> tuple[dict | None, str | None]:
    """Persist morning/evening times. Returns (profile, error_code)."""
    key = str(user_id)
    morning = normalize_time_hhmm(morning_time) if morning_time is not None else None
    evening = normalize_time_hhmm(evening_time) if evening_time is not None else None
    if morning_time is not None and not morning:
        return None, "invalid_morning_time"
    if evening_time is not None and not evening:
        return None, "invalid_evening_time"
    if morning is None and evening is None:
        return None, "no_times"

    profile = sync_profile_times(dict(get_profile(user_id) or {}))
    if morning:
        profile["morning_time"] = morning
        profile["daily_time"] = morning
    if evening:
        profile["evening_time"] = evening
    sync_profile_times(profile)

    if not _use_supabase:
        upsert_profile(user_id, profile)
        return profile, None

    cols = _supabase_profile_columns or refresh_supabase_profile_columns()
    body: dict[str, str] = {}
    if morning:
        body["daily_time"] = morning
        if "morning_time" in cols:
            body["morning_time"] = morning
    if evening:
        body["evening_time"] = evening

    result = _request(
        "PATCH",
        f"user_profiles?user_id=eq.{key}",
        json=body,
        headers={**_headers(), "Prefer": "return=representation"},
    )
    if not result and not _supabase_profile_exists(key):
        insert_body = dict(body)
        insert_body["user_id"] = int(key)
        if "name" in cols and not profile.get("name"):
            insert_body["name"] = "Friend"
        result = _request(
            "POST",
            "user_profiles",
            json=_filter_row_for_supabase(insert_body),
            headers={**_headers(), "Prefer": "return=representation"},
        )

    if not result:
        log.error("patch_profile_times failed uid=%s body=%s", key, body)
        return None, "save_failed"

    fresh = get_profile(user_id)
    out = sync_profile_times(fresh if isinstance(fresh, dict) else profile)
    profiles = _load_json(USER_PROFILES_PATH, {})
    if not isinstance(profiles, dict):
        profiles = {}
    profiles[key] = out
    _save_json(USER_PROFILES_PATH, profiles)
    log.info(
        "patch_profile_times ok uid=%s daily=%s evening=%s",
        key,
        out.get("daily_time"),
        out.get("evening_time"),
    )
    return out, None


def claim_send_slot(user_id: int | str, field: str, value: str) -> bool:
    """Check if slot is already claimed, then claim it."""
    key = str(user_id)
    if not _use_supabase:
        return True
    rows = _request(
        "GET", f"user_profiles?user_id=eq.{key}&select={field}&limit=1"
    ) or []
    log.info(
        "claim_send_slot uid=%s field=%s value=%s rows=%s",
        key,
        field,
        value,
        rows,
    )
    if not rows or not isinstance(rows[0], dict):
        log.info("claim_send_slot uid=%s — no profile, returning True", key)
        return True
    current = str(rows[0].get(field) or "").strip()
    log.info(
        "claim_send_slot uid=%s current=%s value=%s match=%s",
        key,
        current,
        value,
        current == value,
    )
    if current == value:
        return False
    result = _request(
        "PATCH",
        f"user_profiles?user_id=eq.{key}",
        json={field: value},
        headers={**_headers(), "Prefer": "return=minimal"},
    )
    if result is None:
        log.error(
            "claim_send_slot PATCH failed uid=%s field=%s value=%s",
            key,
            field,
            value,
        )
        return False
    return True


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


def _cycle_flags_for_row(p: dict) -> dict:
    cf = p.get("cycle_flags")
    out: dict = {}
    if isinstance(cf, dict):
        for k, v in cf.items():
            if v:
                out[str(k)] = True
    for k, v in p.items():
        if (
            k.startswith("weekly_sent_day_")
            or k.startswith("new_week_sent_day_")
            or k.startswith("weekly_sent_")
        ) and v:
            out[str(k)] = True
    return out


def _apply_cycle_flags_to_profile(p: dict) -> None:
    raw = p.pop("cycle_flags", None)
    if not isinstance(raw, dict):
        raw = {}
    p["cycle_flags"] = {str(k): bool(v) for k, v in raw.items() if v}
    for flag_key, shown in p["cycle_flags"].items():
        if shown:
            p[flag_key] = True


def cycle_flag_sent(profile: dict, flag_key: str) -> bool:
    cf = profile.get("cycle_flags")
    if isinstance(cf, dict) and cf.get(flag_key):
        return True
    return bool(profile.get(flag_key))


def mark_cycle_flag(user_id: int | str, flag_key: str) -> dict:
    profile = dict(get_profile(user_id) or {})
    cf = profile.get("cycle_flags")
    if not isinstance(cf, dict):
        cf = {}
    cf = dict(cf)
    cf[flag_key] = True
    profile["cycle_flags"] = cf
    profile[flag_key] = True
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


def upsert_profile(user_id: int | str, profile: dict) -> bool:
    """Persist profile. Returns False if Supabase write failed."""
    key = str(user_id)
    profile = sync_profile_times(dict(profile))
    row = _profile_to_row(profile)
    row["user_id"] = int(key)
    ok = True

    if _use_supabase:
        ok = _write_profile_to_supabase(key, row)

    profiles = _load_json(USER_PROFILES_PATH, {})
    if not isinstance(profiles, dict):
        profiles = {}
    profiles[key] = profile
    _save_json(USER_PROFILES_PATH, profiles)
    return ok


def _profile_daily_enabled(profile: dict) -> bool:
    """Treat null / missing as enabled; only explicit false disables sends."""
    v = profile.get("daily_enabled", True)
    if v is False or v == 0:
        return False
    if isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "off"):
        return False
    return True


def load_subscribers() -> set[int]:
    ids: set[int] = set()
    if _use_supabase:
        rows = (
            _request("GET", "user_profiles?select=user_id,daily_enabled") or []
        )
        for row in rows:
            if not isinstance(row, dict) or not row.get("user_id"):
                continue
            if not _profile_daily_enabled(row):
                continue
            try:
                ids.add(int(row["user_id"]))
            except (TypeError, ValueError):
                pass
        if ids:
            log.info("load_subscribers from Supabase: %s users", len(ids))
            return ids
    data = _load_json(SUBSCRIBERS_PATH, [])
    try:
        ids = {int(x) for x in data}
    except (TypeError, ValueError):
        ids = set()
    if ids:
        log.info("load_subscribers from JSON: %s users", len(ids))
    return ids


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
                f"daily_summaries?user_id=eq.{key}"
                "&select=summary_date,task_completed,completed,summary,mood,key_detail"
                "&order=summary_date.asc",
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
            out.append(
                {
                    "date": d,
                    "task_completed": normalize_task_completed(tc),
                    "completed": row.get("completed"),
                    "summary": str(row.get("summary") or ""),
                    "mood": str(row.get("mood") or ""),
                    "key_detail": str(row.get("key_detail") or ""),
                }
            )
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
                "completed": row.get("completed"),
                "summary": str(row.get("summary") or ""),
                "mood": str(row.get("mood") or ""),
                "key_detail": str(row.get("key_detail") or ""),
            }
        )
    return out


def _profile_to_row(p: dict) -> dict:
    p = sync_profile_times(dict(p))
    morning = p.get("daily_time") or p.get("morning_time") or "09:30"
    evening = p.get("evening_time") or "21:00"
    last_m = p.get("last_morning_sent_date") or p.get("last_daily_sent_date") or None
    last_e = p.get("last_evening_sent_date") or None
    row = {
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
        "last_user_message_date": p.get("last_user_message_date") or "",
        "reengagement_sent_date": p.get("reengagement_sent_date") or "",
        "is_premium": bool(p.get("is_premium")),
        "subscription_end": p.get("subscription_end") or "",
        "plan": p.get("plan") or "",
        "trial_start_date": p.get("trial_start_date") or "",
        "cycle_flags": _cycle_flags_for_row(p),
    }
    cols = _supabase_profile_columns or _SUPABASE_PROFILE_COLUMNS_FALLBACK
    if "language_code" in cols:
        row["language_code"] = str(p.get("language_code") or "en")[:16]
    return row


def _row_to_profile(row: dict) -> dict:
    p = dict(row)
    _apply_milestones_shown_to_profile(p)
    _apply_cycle_flags_to_profile(p)
    for key in ("last_daily_sent_date", "last_morning_sent_date", "last_evening_sent_date"):
        if p.get(key):
            p[key] = str(p[key])[:10]
    if p.get("last_weekly_recap_date"):
        p["last_weekly_recap_date"] = str(p["last_weekly_recap_date"])[:10]
        p[f"weekly_sent_{p['last_weekly_recap_date']}"] = True
    sync_profile_times(p)
    merged_goal = str(
        p.get("main_goal") or p.get("final_goal") or p.get("raw_goal") or ""
    ).strip()
    if merged_goal:
        p["main_goal"] = merged_goal
    p.setdefault("raw_goal", p.get("main_goal") or p.get("raw_goal") or "")
    p.setdefault("final_goal", p.get("main_goal") or p.get("final_goal") or "")
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
    p.setdefault("language_code", p.get("language_code") or "en")
    p.setdefault("last_user_message_date", p.get("last_user_message_date") or "")
    p.setdefault("reengagement_sent_date", p.get("reengagement_sent_date") or "")
    p.setdefault("is_premium", bool(p.get("is_premium")))
    p.setdefault("subscription_end", p.get("subscription_end") or "")
    p.setdefault("plan", p.get("plan") or "")
    p.setdefault("trial_start_date", p.get("trial_start_date") or "")
    if p.get("last_user_message_date"):
        p["last_user_message_date"] = str(p["last_user_message_date"])[:10]
    if p.get("reengagement_sent_date"):
        p["reengagement_sent_date"] = str(p["reengagement_sent_date"])[:10]
    if p.get("subscription_end"):
        p["subscription_end"] = str(p["subscription_end"])[:10]
    if p.get("trial_start_date"):
        p["trial_start_date"] = str(p["trial_start_date"])[:10]
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


def save_task(task: dict) -> None:
    """Save or update task in Supabase."""
    if not _use_supabase:
        return
    row = {
        "id": str(task.get("id", "")),
        "telegram_id": int(task.get("telegram_id", 0)),
        "title": str(task.get("title", ""))[:500],
        "description": str(task.get("description", ""))[:2000],
        "date": str(task.get("date", "")),
        "time": str(task.get("time", "")),
        "timezone": str(task.get("timezone", "UTC")),
        "repeat": str(task.get("repeat", "none")),
        "days_of_week": task.get("days_of_week") or [],
        "remind_before_minutes": int(task.get("remind_before_minutes") or 0),
        "status": str(task.get("status", "active")),
        "done": bool(task.get("done", False)),
        "last_sent_at": str(task.get("last_sent_at") or ""),
        "snooze_until": str(task.get("snooze_until") or ""),
    }
    created = str(task.get("created_at") or "").strip()
    if created:
        row["created_at"] = created
    _request(
        "POST",
        "tasks?on_conflict=id",
        json=row,
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
    )


def load_tasks(telegram_id: int | str) -> list[dict]:
    """Load all active tasks for user from Supabase."""
    key = str(telegram_id)
    if not _use_supabase:
        return []
    rows = _request(
        "GET",
        f"tasks?telegram_id=eq.{key}&status=eq.active&done=eq.false&order=created_at.asc",
    ) or []
    return [r for r in rows if isinstance(r, dict)]


def load_all_tasks() -> list[dict]:
    """Load all active tasks from Supabase for reminder job."""
    if not _use_supabase:
        return []
    rows = _request(
        "GET",
        "tasks?status=eq.active&done=eq.false&order=created_at.asc",
    ) or []
    return [r for r in rows if isinstance(r, dict)]


def update_task(task_id: str, patch: dict) -> None:
    """Update specific task fields in Supabase."""
    if not _use_supabase:
        return
    _request(
        "PATCH",
        f"tasks?id=eq.{task_id}",
        json=patch,
        headers={**_headers(), "Prefer": "return=minimal"},
    )


def delete_task_db(task_id: str, telegram_id: int | str) -> bool:
    """Delete task from Supabase."""
    if not _use_supabase:
        return False
    result = _request(
        "DELETE",
        f"tasks?id=eq.{task_id}&telegram_id=eq.{telegram_id}",
    )
    return result is not None


def delete_all_tasks(telegram_id: int | str) -> None:
    """Delete all tasks for user."""
    key = str(telegram_id)
    if _use_supabase:
        _request("DELETE", f"tasks?telegram_id=eq.{key}")
