"""One question to people whose trial ended without a payment (Anya, 25.11.2026).

Texts are Anya's, Russian only. Once per person, in her daytime, never together with a pause
message, no price and no payment button. Sent only in the window Anya approved: 26.11.2026, 12-16
MSK, to people whose trial was over by 25.11, 16 at most. SURVEY_SEND=0 stops it.
State: app_kv "survey": {uid: {"sent", "answer", "answer_at", "text_at"}}; "sent" is written
before the message goes out, so a failure can never lead to a second one.
Pure functions; main.py reads the data and sends."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KV_KEY = "survey"
FROM_HOUR, TO_HOUR = 12, 17
# Anya's «yes» of 25.11.2026 covers this day and this many people, nothing else
WINDOW = (datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc), datetime(2026, 11, 26, 13, 0, tzinfo=timezone.utc))
ENDED_BY = date(2026, 11, 25)
MAX_SENT = 16
TEXT_DAYS = 7  # an answer in words is taken this long after the question

TEXT = (
    "привет 🤍 это Спейс. пробные дни закончились, и мне важно понять, чего не хватило. "
    "нажми одно, это пара секунд"
)
THANKS = (
    "спасибо, что сказала 🤍 если хочешь, напиши одной фразой, что было бы для тебя полезно. "
    "этот ответ прочитает команда Спейс"
)
THANKS_TEXT = "спасибо, сохранила 🤍"
OPTIONS = (
    ("forgot", "забыла про меня"),
    ("steps", "шаги были не про меня"),
    ("many", "много сообщений"),
    ("price", "дорого"),
    ("other", "нужно было что-то другое"),
)
CODES = tuple(c for c, _ in OPTIONS)


def window_open(now: datetime) -> bool:
    return WINDOW[0] <= now < WINDOW[1]


def left_to_send(state: object) -> int:
    used = len(state) if isinstance(state, dict) else 0
    return max(0, MAX_SENT - used)


def _dt(raw: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def replied_day_2_4(times: list[str], start: date, zone: ZoneInfo) -> bool:
    """She wrote on day 2, 3 or 4 of the trial, by her own calendar."""
    for raw in times or []:
        dt = _dt(raw)
        if dt is not None and 1 <= (dt.astimezone(zone).date() - start).days <= 3:
            return True
    return False


def _minutes(hm: object) -> int | None:
    try:
        h, m = str(hm or "").strip()[:5].split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def good_moment(local: datetime, morning_time: object) -> bool:
    """Daytime, and not within an hour of the morning slot (the pause messages go there)."""
    if not FROM_HOUR <= local.hour < TO_HOUR:
        return False
    slot = _minutes(morning_time)
    now = local.hour * 60 + local.minute
    return slot is None or abs(now - slot) > 60


def waits_for_text(entry: object, now: datetime) -> bool:
    """The question went out lately and she has not answered in words yet."""
    if not isinstance(entry, dict) or entry.get("text_at") or entry.get("failed"):
        return False
    sent = _dt(entry.get("sent"))
    return sent is not None and now - sent <= timedelta(days=TEXT_DAYS)


def stats(state: object) -> dict:
    """Counts only, for /api/stats/daily."""
    rows = [e for e in (state or {}).values() if isinstance(e, dict)] if isinstance(state, dict) else []
    sent = [e for e in rows if e.get("sent") and not e.get("failed")]
    return {
        "sent": len(sent),
        "answered": sum(1 for e in sent if e.get("answer")),
        "wrote_text": sum(1 for e in sent if e.get("text_at")),
        "answers": {c: sum(1 for e in sent if e.get("answer") == c) for c in CODES},
    }
