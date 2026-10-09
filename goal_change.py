"""Replace a 12-week goal from an existing chat, without restarting onboarding."""

from __future__ import annotations

# Matches onboarding_flow.OB_GOAL_DIALOG. Kept here so tests do not import Telegram.
GOAL_DIALOG_STEP = 5

ASK_RU = "Хорошо. Какую цель ты хочешь поставить на следующие 12 недель?"
ASK_EN = "Okay. What goal do you want to set for the next 12 weeks?"

_CHOICE_MARKERS = (
    "скорректировать",
    "новый 12-недельный цикл",
    "or adjust",
    "new 12-week cycle",
)
_GREETING_MARKERS = (
    "привет",
    "hello",
    "hi,",
    "nice to meet",
    "рада познакомиться",
    "как тебя зовут",
    "раздражает",
    "давит",
)
_PATCH_KEYS = (
    "main_goal",
    "weekly_goal",
    "raw_goal",
    "final_goal",
    "current_week",
    "weekly_score",
    "vision",
)


def ask_prompt(lang: str) -> str:
    if str(lang or "").lower().startswith("ru"):
        return ASK_RU
    return ASK_EN


def opening_restarts_onboarding(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _CHOICE_MARKERS + _GREETING_MARKERS)


def build_change_12w_state(profile: dict) -> tuple[dict, str]:
    """In-memory flow only. Does not touch chat history or the saved profile."""
    lang = str((profile or {}).get("language_code") or "en")
    opening = ask_prompt(lang)
    state: dict = {
        "step": GOAL_DIALOG_STEP,
        "change_mode": "new_12w",
        "change_12w_phase": "goal",
        "lang": lang,
        "language_code": lang,
        "goal_turns": [{"role": "assistant", "content": opening[:2000]}],
        "dont_know_streak": 0,
        "name": str((profile or {}).get("name") or "").strip(),
        "main_goal": str(
            (profile or {}).get("main_goal") or (profile or {}).get("final_goal") or ""
        ).strip(),
        "weekly_goal": str((profile or {}).get("weekly_goal") or "").strip(),
        "morning_time": (profile or {}).get("morning_time")
        or (profile or {}).get("daily_time")
        or "09:30",
        "evening_time": (profile or {}).get("evening_time") or "21:00",
        "timezone": str((profile or {}).get("timezone") or "").strip(),
        "vision": "",
    }
    if (profile or {}).get("has_kids") is not None:
        state["has_kids"] = profile.get("has_kids")
    return state, opening


def next_step_after_weekly(state: dict) -> str:
    if state.get("reengagement"):
        return "finish_reengagement"
    if str(state.get("change_mode") or "") in ("adjust_12w", "new_12w"):
        return "finish_12w"
    return "weekly_to_morning"


def profile_patch_for_new_cycle(state: dict) -> dict:
    """Fields written when the new 12-week goal and its week task are confirmed."""
    main_goal = str(state.get("main_goal") or "").strip()
    weekly_goal = str(state.get("weekly_goal") or "").strip()
    fields = {
        "main_goal": main_goal[:2000],
        "weekly_goal": weekly_goal[:2000],
        "raw_goal": main_goal[:2000],
        "final_goal": main_goal[:2000],
        "current_week": 1,
        "weekly_score": 0,
    }
    vision = str(state.get("vision") or "").strip()
    if vision:
        fields["vision"] = vision[:4000]
    return fields


def patch_touches_unrelated(fields: dict) -> bool:
    return any(key not in _PATCH_KEYS for key in fields)
