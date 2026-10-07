"""Cheap gates around Claude: one call per user turn, no scheduler retries."""

from __future__ import annotations

import json
import re
from typing import Any

DYNAMIC_MARKER = "\n---DYNAMIC---\n"
CHAT_HISTORY_LIMIT = 12
_EMPTY_DIALOG = {"нет предыдущих сообщений", "no previous messages"}
PLAIN_CHAT_CLAUDE_CALLS = 1
PHOTO_CLAUDE_CALLS = 1
BUTTON_DONE_CLAUDE_CALLS = 0
REMINDER_CLAUDE_CALLS = 0

_USAGE: dict[str, dict[str, int]] = {}


def record_usage(
    feature: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> None:
    bucket = _USAGE.setdefault(
        feature or "unspecified",
        {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    )
    bucket["calls"] += 1
    bucket["input_tokens"] += int(input_tokens or 0)
    bucket["output_tokens"] += int(output_tokens or 0)
    bucket["cache_read_input_tokens"] += int(cache_read_input_tokens or 0)
    bucket["cache_creation_input_tokens"] += int(cache_creation_input_tokens or 0)


def usage_snapshot() -> dict[str, dict[str, int]]:
    return {name: dict(stats) for name, stats in _USAGE.items()}


def usage_report() -> str:
    lines = ["CLAUDE DAILY USAGE"]
    if not _USAGE:
        lines.append("(no calls yet)")
        return "\n".join(lines)
    for name in sorted(_USAGE):
        stats = _USAGE[name]
        lines.append(
            f"{name}: {stats['calls']} calls / "
            f"{stats['input_tokens']} input / {stats['output_tokens']} output / "
            f"cache_read {stats['cache_read_input_tokens']} / "
            f"cache_write {stats['cache_creation_input_tokens']}"
        )
    return "\n".join(lines)


def is_billing_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "credit balance" in text or "too low to access" in text


def is_telegram_block_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "bot was blocked" in text or ("forbidden" in text and "403" in text)


def profile_is_blocked(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    flag = profile.get("telegram_blocked")
    if flag is True or flag == 1:
        return True
    if isinstance(flag, str) and flag.strip().lower() in ("true", "1", "yes"):
        return True
    flags = profile.get("cycle_flags")
    if isinstance(flags, dict) and flags.get("telegram_blocked"):
        return True
    return False


def dialog_has_user_line(rendered: str) -> bool:
    text = (rendered or "").strip()
    if not text or text in _EMPTY_DIALOG:
        return False
    return text.startswith("Пользователь:") or text.startswith("User:") or "\nПользователь:" in text or "\nUser:" in text


def spoke_on_or_after(last_user_date: str, boundary: str) -> bool:
    raw = (last_user_date or "")[:10]
    edge = (boundary or "")[:10]
    return bool(raw and edge and raw >= edge)


def morning_needs_model(last_user_date: str, yesterday: str, rendered: str) -> bool:
    """Claude only if she wrote yesterday or today. A silent day stays a template."""
    if spoke_on_or_after(last_user_date, yesterday):
        return True
    if not (last_user_date or "").strip() and dialog_has_user_line(rendered):
        return True
    return False


def evening_needs_model(last_user_date: str, today: str) -> bool:
    """Evening Claude only if she wrote today. Otherwise the opening template is enough."""
    return (last_user_date or "")[:10] == (today or "")[:10]


def recent_history(hist: list[dict] | None, limit: int = CHAT_HISTORY_LIMIT) -> list[dict]:
    if not hist:
        return []
    return list(hist)[-limit:]


def claim_day_slot(store: dict[str, str], key: str, today: str) -> bool:
    """In-memory stand-in for an atomic day claim. False if this key already has today."""
    if store.get(key) == today:
        return False
    store[key] = today
    return True


def _nullish(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none"):
        return True
    return False


def parse_structured_reply(raw: str) -> dict[str, Any]:
    """Split a chat completion into user-facing text and optional state updates.

    If the model did not return JSON, the whole text is the reply and every
    state field stays empty. That is still one Claude call.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    data: object = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict) or "reply" not in data:
        return {
            "reply": (raw or "").strip(),
            "state_updates": {
                "task_completed": None,
                "new_task": None,
                "new_goal": None,
                "weekly_goal_update": None,
                "important_fact": None,
            },
        }
    updates = data.get("state_updates")
    if not isinstance(updates, dict):
        updates = {}
    fact = updates.get("important_fact")
    fact_text = None
    fact_category = "personal"
    if isinstance(fact, dict):
        fact_text = str(fact.get("fact") or "").strip()
        fact_category = str(fact.get("category") or "personal").strip() or "personal"
    elif isinstance(fact, str):
        fact_text = fact.strip()
    if _nullish(fact_text):
        fact_text = None
    completed = updates.get("task_completed")
    if completed is True or (isinstance(completed, str) and completed.strip().lower() == "true"):
        completed_out: bool | None = True
    elif completed is False or (isinstance(completed, str) and completed.strip().lower() == "false"):
        completed_out = False
    else:
        completed_out = None

    def text_or_none(value: object) -> str | None:
        if _nullish(value):
            return None
        return str(value).strip()

    return {
        "reply": str(data.get("reply") or "").strip(),
        "state_updates": {
            "task_completed": completed_out,
            "new_task": text_or_none(updates.get("new_task")),
            "new_goal": text_or_none(updates.get("new_goal")),
            "weekly_goal_update": text_or_none(updates.get("weekly_goal_update")),
            "important_fact": fact_text,
            "important_fact_category": fact_category,
        },
    }


def _blank_if_null(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "null", "none"}:
        return ""
    return text


def parse_evening_bundle(raw: str) -> dict[str, str]:
    """One evening call can return the Telegram text and the day's summary."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    data: object = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    empty = {"reply": (raw or "").strip(), "summary": "", "mood": "", "key_detail": ""}
    if not isinstance(data, dict):
        return empty
    reply = str(data.get("reply") or "").strip()
    if not reply:
        return empty
    return {
        "reply": reply,
        "summary": _blank_if_null(data.get("summary")),
        "mood": _blank_if_null(data.get("mood")),
        "key_detail": _blank_if_null(data.get("key_detail")),
    }


def parse_evening_bundle(raw: str) -> dict[str, str]:
    """One evening call can return the Telegram text and the day's summary."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    data: object = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    empty = {"reply": (raw or "").strip(), "summary": "", "mood": "", "key_detail": ""}
    if not isinstance(data, dict):
        return empty
    reply = str(data.get("reply") or "").strip()
    if not reply:
        return empty

    def clean(value: object) -> str:
        if value is None:
            return ""
        text_value = str(value).strip()
        if text_value.lower() == "null":
            return ""
        return text_value

    return {
        "reply": reply,
        "summary": clean(data.get("summary")),
        "mood": clean(data.get("mood")),
        "key_detail": clean(data.get("key_detail")),
    }


CHAT_OUTPUT_RULE_RU = """Формат ответа — один JSON-объект, без markdown:
{"reply":"текст пользователю","state_updates":{"task_completed":null,"new_task":null,"new_goal":null,"weekly_goal_update":null,"important_fact":null}}
reply — обычный текст Спейс, 2-3 предложения.
task_completed — true/false только если пользователь явно сказал, что задача дня сделана или нет. Иначе null.
new_task — короткая задача, только если в этой реплике о ней договорились. Иначе null.
new_goal — новая цель на 12 недель, только если пользователь явно заменяет текущую. Иначе null.
weekly_goal_update — новая цель недели, только если пользователь явно её назвал. Иначе null.
important_fact — строка, только если это устойчивый факт о человеке (ограничение, предпочтение, работа, семья). Иначе null.
Обычный рассказ про усталость, еду, спорт или настроение — все поля state_updates остаются null."""

CHAT_OUTPUT_RULE_EN = """Reply format — one JSON object, no markdown:
{"reply":"text for the user","state_updates":{"task_completed":null,"new_task":null,"new_goal":null,"weekly_goal_update":null,"important_fact":null}}
reply is the Space message, 2-3 sentences.
task_completed is true/false only if she clearly said today's task was done or not. Otherwise null.
new_task is a short task only if this turn agreed on one. Otherwise null.
new_goal is a new 12-week goal only if she explicitly replaces the current one. Otherwise null.
weekly_goal_update only if she explicitly names a new weekly goal. Otherwise null.
important_fact is a durable fact (constraint, preference, work, family). Otherwise null.
A story about being tired, food, sport, or mood keeps every state_updates field null."""
