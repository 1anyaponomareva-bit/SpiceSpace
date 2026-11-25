"""Messages to people who went quiet (Anya, 21.11.2026: «как у неё — раз в несколько дней,
особо не напрягает»).

Three groups, each in the evening of her own time zone, never more than one a day:
- onb: started but did not finish onboarding - day 1, 3, 7, 14 after her last message,
  then once every 30 days. Fixed texts (approved by Anya), with her name and goal if known.
- silent: finished onboarding, has access, the old day 4/8/15 messages are done - once a
  week after day 15 (8 weeks), then monthly. The text is generated like the day 4/8/15 ones.
- lapsed: free days are over and she did not pay - once a week (8 weeks), then monthly;
  every second one says Space can be continued.
Never: after /stop, a blocked bot, under 18.
State per person lives in app_kv "winback": {uid: {"anchor", "n", "last"}}; "anchor" is
her last activity day, so when she writes again the count starts over.
Pure functions; main.py reads profiles and sends.
"""

from __future__ import annotations

import re
import names
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KV_KEY = "winback"
ONB_DAYS = (1, 3, 7, 14)
WEEKLY_TIMES = 8
MONTHLY = 30
SEND_FROM, SEND_TO = 19, 22  # local evening hours

ONB_NO_NAME_RU = (
    "Привет 🤍 Это Спейс. Ты заглянула ко мне, но мы так и не познакомились. Как тебя зовут? "
    "Дальше одна цель и маленький шаг в день, я помогу.",
    "Я тут 🤍 Если сейчас не до целей, это нормально. Когда будет минутка, напиши, как тебя "
    "зовут, и начнём с одного маленького шага.",
    "Неделя пролетела 🙂 Давай начнём с самого простого: напиши своё имя, а я спрошу, что тебе "
    "сейчас важнее всего.",
    "Привет, это Спейс 🤍 Если захочешь вернуться к своей цели, я здесь. Просто напиши мне что-нибудь.",
)
ONB_NO_NAME_EN = (
    "Hi 🤍 It's Space. You stopped by, but we never got to know each other. What's your name? "
    "Then one goal and one small step a day, I'll help.",
    "I'm here 🤍 If goals aren't on your mind right now, that's okay. When you have a minute, "
    "tell me your name and we'll start with one small step.",
    "A week flew by 🙂 Let's start with the easiest part: tell me your name, and I'll ask what "
    "matters most to you right now.",
    "Hi, it's Space 🤍 If you ever want to get back to your goal, I'm here. Just write me anything.",
)
ONB_NAME_RU = (
    "{name}, привет 🤍 Мы с тобой не договорили. Осталось совсем немного, и я дам тебе первый шаг. "
    "Напиши, когда будет минутка.",
    "{name}, я помню про тебя 🤍 Если сейчас не до этого, ничего страшного. Когда будешь готова, "
    "просто напиши, продолжим с того же места.",
    "{name}, неделя пролетела 🙂 Давай закончим знакомство? Это пара минут, а дальше я сама буду "
    "писать тебе каждый день.",
    "{name}, привет, это Спейс 🤍 Если захочешь вернуться к своей цели, я здесь. Просто напиши мне.",
)
ONB_NAME_EN = (
    "{name}, hi 🤍 We didn't finish our chat. Just a little left, and I'll give you your first "
    "step. Write me when you have a minute.",
    "{name}, I remember you 🤍 If now isn't the time, that's okay. When you're ready, just write "
    "and we'll pick up where we left off.",
    "{name}, a week flew by 🙂 Shall we finish getting to know each other? It takes a couple of "
    "minutes, and then I'll message you every day myself.",
    "{name}, hi, it's Space 🤍 If you ever want to get back to your goal, I'm here. Just write me.",
)
ONB_GOAL_FIRST_RU = (
    "{name}, привет 🤍 Ты хотела: «{goal}». Мы почти договорили: осталась пара вопросов, "
    "и я дам тебе первый шаг."
)
ONB_GOAL_FIRST_EN = (
    "{name}, hi 🤍 You wanted: «{goal}». We're almost there: a couple of questions left, "
    "and I'll give you your first step."
)
LAPSED_RU = (
    "{name}, привет 🤍 Давно не списывались. Как ты?{goal_q}",
    "{name}, я всё помню: и твою цель, и на чём мы остановились 🤍 Если захочешь продолжить, "
    "Спейс можно включить в любой момент, просто напиши мне.",
)
LAPSED_EN = (
    "{name}, hi 🤍 It's been a while. How are you?{goal_q}",
    "{name}, I remember everything: your goal and where we left off 🤍 If you want to continue, "
    "Space can be switched back on anytime, just write me.",
)


def _ru(lang: str) -> bool:
    return str(lang or "").lower().startswith("ru")


def _d(value: object) -> date | None:
    s = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def local_now(profile: dict, lang: str, now: datetime, default_tz: str) -> datetime:
    name = str(profile.get("timezone") or "").strip() or ("Europe/Moscow" if _ru(lang) else default_tz)
    try:
        zone = ZoneInfo(name)
    except Exception:
        zone = ZoneInfo("Europe/Moscow")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(zone)


def anchor_day(profile: dict) -> date | None:
    """Her last activity: last message, or the day she pressed start."""
    days = [d for d in (_d(profile.get("last_user_message_date")), _d(profile.get("created_at"))) if d]
    return max(days) if days else None


def _due_days(kind: str) -> list[int]:
    """Days after the anchor for each numbered message, before the monthly ones."""
    if kind == "onb":
        return list(ONB_DAYS)
    start = 15 if kind == "silent" else 0
    return [start + 7 * (i + 1) for i in range(WEEKLY_TIMES)]


def step(kind: str, state: dict, anchor: date, today: date) -> int:
    """Number of the next message. Someone who went quiet long ago (or before this
    started) gets only the message that fits today, not every one she missed."""
    n = int(state.get("n") or 0) if state.get("anchor") == anchor.isoformat() else 0
    days = (today - anchor).days
    passed = sum(1 for d in _due_days(kind) if d <= days)
    return max(n, passed - 1, 0)


def due(kind: str, state: dict, anchor: date, today: date) -> bool:
    """Is the next message for this group due today? state is reset when anchor moves."""
    same = state.get("anchor") == anchor.isoformat()
    last = _d(state.get("last")) if same else None
    if last == today:
        return False
    days = (today - anchor).days
    plan = _due_days(kind)
    n = step(kind, state, anchor, today)
    if n < len(plan):
        if days < plan[n]:
            return False
        # never two in a row: at least the planned gap since the previous one
        gap = plan[n] - plan[n - 1] if n else 0
        return last is None or (today - last).days >= gap
    return last is None or (today - last).days >= MONTHLY


def after_send(state: dict, anchor: date, today: date, kind: str = "onb") -> dict:
    n = step(kind, state, anchor, today)
    return {"anchor": anchor.isoformat(), "n": n + 1, "last": today.isoformat()}


_HARD_STOP = {"стоп", "stop", "хватит", "enough", "отмена", "cancel", "отстань", "отстаньте", "не пиши", "не пишите"}
CRISIS_DAYS = 30


def is_hard_stop(text: str) -> bool:
    low = re.sub(r"[^\w\s]", "", str(text or "").lower()).strip()
    words = low.split()
    return low in _HARD_STOP or (
        0 < len(words) <= 4 and any(w in ("стоп", "stop", "хватит", "отстань", "отстаньте") for w in words)
    )


def quiet_reason(turns: list[dict], now: datetime, last_message_date: object, detect_crisis) -> str:
    """Why not to write (23.11.2026): «crisis» in the last 30 days, or her last word was «стоп»."""
    users = [t for t in turns or [] if t.get("role") == "user"]

    def when(t: dict) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(t.get("ts") or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def text(t: dict) -> str:
        return str((t.get("parts") or [""])[0] or "")

    for t in users:
        at = when(t)
        if (at is None or now - at < timedelta(days=CRISIS_DAYS)) and detect_crisis(text(t)):
            return "crisis"
    if users and is_hard_stop(text(users[-1])):
        at, last = when(users[-1]), _d(last_message_date)
        # onboarding answers are not in the history: a later day means she came back
        if at is None or last is None or last <= at.date() + timedelta(days=1):
            return "stop"
    return ""


def clean_name(name: object) -> str:
    n = str(name or "").strip()
    return names.real_name(n)


def onb_text(n: int, lang: str, name: str = "", goal: str = "") -> str:
    ru = _ru(lang)
    name = clean_name(name)
    goal = (goal or "").strip()[:120]
    i = min(n, 3)
    if not name:
        return (ONB_NO_NAME_RU if ru else ONB_NO_NAME_EN)[i]
    if i == 0 and goal:
        return (ONB_GOAL_FIRST_RU if ru else ONB_GOAL_FIRST_EN).format(name=name, goal=goal)
    return (ONB_NAME_RU if ru else ONB_NAME_EN)[i].format(name=name)


def lapsed_text(n: int, lang: str, name: str = "", goal: str = "") -> str:
    ru = _ru(lang)
    goal = (goal or "").strip()[:120]
    goal_q = ""
    if goal:
        goal_q = f" Как дела с целью «{goal}»?" if ru else f" How's your goal «{goal}» going?"
    name = clean_name(name)
    text = (LAPSED_RU if ru else LAPSED_EN)[n % 2].format(name=name.strip() or "{name}", goal_q=goal_q)
    if not name.strip():
        # no name: drop the "{name}, " lead-in
        text = text.replace("{name}, ", "")
    return text
