"""Length of the trial (Anya, 23.11.2026): 3 days (onboarding day + 2) for people whose trial
starts on 24.11.2026 or later by their own calendar; everyone before keeps the 4 days they
were promised. Pure, no imports from the bot."""

from __future__ import annotations

NEW_FROM = "2026-11-24"
NEW_DAYS = 3
OLD_DAYS = 4


def days_for(start: object) -> int:
    """start: trial_start_date (ISO text or date); unknown = a new person."""
    raw = str(start or "").strip()[:10]
    return OLD_DAYS if raw and raw < NEW_FROM else NEW_DAYS


def days_of_profile(profile: dict | None) -> int:
    p = profile if isinstance(profile, dict) else {}
    return days_for(p.get("trial_start_date") or p.get("cycle_start_date"))
