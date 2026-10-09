"""Replace a 12-week goal from an existing chat, without restarting onboarding."""

from __future__ import annotations

import re

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


def format_recent_dialog(turns: list[dict] | None, *, limit: int = 6) -> str:
    """Short note from the existing chat. Not the active goal and not a longer history."""
    lines: list[str] = []
    for turn in (turns or [])[-limit:]:
        role = turn.get("role")
        parts = turn.get("parts") or []
        text = str(turn.get("content") or (parts[0] if parts else "") or "").strip()
        if not text:
            continue
        who = "user" if role == "user" else "assistant"
        lines.append(f"{who}: {text[:300]}")
    return "\n".join(lines)


def looks_like_confusion(text: str) -> bool:
    low = (text or "").lower()
    return any(
        phrase in low
        for phrase in (
            "не понимаю твоего вопроса",
            "не понимаю вопроса",
            "не понял вопрос",
            "не поняла вопрос",
            "не понимаю о чём ты",
            "не понимаю о чем ты",
            "don't understand the question",
            "do not understand the question",
            "what do you mean by that",
            "i don't understand",
        )
    )


def looks_like_emotion_or_obstacle(text: str) -> bool:
    low = (text or "").lower()
    return any(
        phrase in low
        for phrase in (
            "уныние",
            "вгоняет",
            "грустно",
            "расстраива",
            "бесит",
            "устал",
            "устала",
            "выгора",
            "тревог",
            "много баг",
            "много косяк",
            "bugs in",
            "bummed",
            "frustrated",
        )
    )


def _has_forward_intent(text: str) -> bool:
    low = (text or "").lower()
    low = low.replace("не хочу", " ")
    low = low.replace("don't want", " ").replace("do not want", " ")
    return any(
        phrase in low
        for phrase in (
            "хочу ",
            "моя цель",
            "цель —",
            "цель:",
            "запустить",
            "построить",
            "i want to",
            "my goal",
        )
    )


def is_refusal_constraint(text: str) -> bool:
    low = (text or "").lower()
    return any(
        phrase in low
        for phrase in (
            "не хочу",
            "не буду",
            "don't want",
            "do not want",
            "i won't",
        )
    )


def can_lock_as_goal(text: str) -> bool:
    """A feeling, a bug report, a refusal, or a confused reply is not a 12-week goal."""
    raw = (text or "").strip()
    if looks_like_confusion(raw):
        return False
    if _has_forward_intent(raw):
        return True
    if looks_like_emotion_or_obstacle(raw) or is_refusal_constraint(raw):
        return False
    return len(raw) >= 8


def next_dialog_move(text: str) -> str:
    if looks_like_confusion(text):
        return "repair_question"
    if looks_like_emotion_or_obstacle(text) or is_refusal_constraint(text):
        if not _has_forward_intent(text):
            return "listen"
    if can_lock_as_goal(text) and _has_forward_intent(text):
        return "may_confirm_goal"
    return "continue"


def build_change_12w_state(
    profile: dict,
    recent_turns: list[dict] | None = None,
) -> tuple[dict, str]:
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
        "prior_dialog": format_recent_dialog(recent_turns),
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


def _norm_fact(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _fact_is_negated(text: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[\s,])(?:не|нет|don't|do not|not|no longer)(?:$|[\s,])",
            _norm_fact(text),
        )
    )


def fact_update_plan(existing: list[str], new: str) -> dict:
    """Decide whether a new fact is a duplicate, a replacement, or an addition.

    A newer line replaces an older one only when one contains the other:
    the polarity flipped, or the new line is the more specific version.
    Unrelated facts stay. Storage columns do not change.
    """
    new_clean = re.sub(r"\s+", " ", (new or "").strip())
    new_key = _norm_fact(new_clean)
    if not new_key:
        return {"action": "skip", "drop": []}
    drop: list[str] = []
    for old in existing:
        old_clean = re.sub(r"\s+", " ", (old or "").strip())
        old_key = _norm_fact(old_clean)
        if not old_key:
            continue
        if old_key == new_key:
            return {"action": "skip", "drop": []}
        if old_key not in new_key and new_key not in old_key:
            continue
        flipped = _fact_is_negated(old_clean) != _fact_is_negated(new_clean)
        if flipped or len(new_key) > len(old_key):
            drop.append(old_clean)
            continue
        return {"action": "skip", "drop": []}
    return {"action": "insert", "drop": drop}


def should_store_fact(text: str) -> bool:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if len(raw) < 12 or looks_like_confusion(raw):
        return False
    if is_refusal_constraint(raw):
        return True
    if looks_like_emotion_or_obstacle(raw):
        return False
    return True


def constraint_from_message(text: str) -> str | None:
    """One explicit refusal from the user text. No model call."""
    for part in re.split(r"[.!?\n]+", text or ""):
        piece = re.sub(r"\s+", " ", part).strip(" ,;:-")
        if should_store_fact(piece) and is_refusal_constraint(piece):
            return piece[:200]
    return None
