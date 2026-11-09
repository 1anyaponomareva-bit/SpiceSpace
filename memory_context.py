"""Long-range memory for the chat: which day each old message is from, how long the
user was away, and what happened on recent days. Pure functions, no API calls."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

CHAT_FACTS_LIMIT = 15

_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _ru(lang: str) -> bool:
    return str(lang or "").lower().startswith("ru")


def _fmt_day(d: date, lang: str) -> str:
    if _ru(lang):
        return f"{d.day} {_MONTHS_RU[d.month - 1]}"
    return f"{_MONTHS_EN[d.month - 1]} {d.day}"


def _tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Ho_Chi_Minh")


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def turn_local_date(turn: dict, tz_name: str) -> date | None:
    raw = str(turn.get("ts") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz(tz_name)).date()


def history_with_dates(hist: list[dict], today: date, tz_name: str, lang: str) -> list[dict]:
    """Copy of the history where user messages from earlier days carry their date,
    so the model does not treat last week's plans as today's."""
    out: list[dict] = []
    for turn in hist:
        d = turn_local_date(turn, tz_name)
        parts = turn.get("parts") or []
        text = str(parts[0] if parts else "")
        if d and d < today and turn.get("role") == "user" and text:
            label = f"(сообщение от {_fmt_day(d, lang)}) " if _ru(lang) else f"(message from {_fmt_day(d, lang)}) "
            out.append({**turn, "parts": [label + text]})
        else:
            out.append(turn)
    return out


def gap_note(hist: list[dict], today: date, tz_name: str, lang: str) -> str:
    """A rule for the model when the last talk was on an earlier day."""
    last = None
    for turn in reversed(hist):
        if turn.get("role") != "user":
            continue  # morning/evening messages from the bot are not a conversation
        last = turn_local_date(turn, tz_name)
        if last:
            break
    if not last or last >= today:
        return ""
    days = (today - last).days
    when = _fmt_day(last, lang)
    if _ru(lang):
        note = (
            f"Прошлый разговор был {when} ({days} дн. назад), сегодня {_fmt_day(today, lang)}. "
            "Всё из истории сообщений было тогда, а не сегодня: не спрашивай о прошедших "
            "событиях как о будущих. Если она что-то планировала на те дни, спроси, как прошло."
        )
        if days >= 2:
            note += " Её не было несколько дней: без упрёков, просто рада, что она пишет."
        return note
    note = (
        f"The last conversation was on {when} ({days} days ago); today is {_fmt_day(today, lang)}. "
        "Everything in the message history happened then, not today: do not ask about past "
        "events as if they were upcoming. If she planned something for those days, ask how it went."
    )
    if days >= 2:
        note += " She was away for a few days: no reproach, just glad she is writing."
    return note


def recent_days_block(summaries: list[dict], today: date, lang: str, *, days: int = 3) -> str:
    """Short notes from the last few days she talked to the bot (before yesterday too),
    so a gap of a few days does not wipe the context."""
    past = []
    for s in summaries:
        try:
            d = date.fromisoformat(str(s.get("date") or "")[:10])
        except ValueError:
            continue
        text = str(s.get("summary") or s.get("key_detail") or "").strip()
        if d < today and (today - d).days <= 30 and text:
            past.append((d, text))
    past.sort()
    past = past[-days:]
    if not past:
        return ""
    head = "Последние дни, когда вы общались:" if _ru(lang) else "Recent days you talked:"
    lines = [head]
    for d, text in past:
        lines.append(f"{_fmt_day(d, lang)}: {text[:300]}")
    return "\n".join(lines)
