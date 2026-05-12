"""
SpiceSpace Telegram bot: онбординг (measurable/qualitative цели), индивидуальное время
ежедневного сообщения (по TIMEZONE в .env), Gemini-диалог, HTTP API для Mini App.

Secrets: TELEGRAM_BOT_TOKEN, GEMINI_API_KEY in .env

Optional .env:
  TIMEZONE=Asia/Ho_Chi_Minh
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_FALLBACK_MODELS=gemini-1.5-flash-8b,gemini-2.0-flash-lite
  PORT=8080
  MINIAPP_ORIGINS=https://spicespace-miniapp.vercel.app,http://localhost:5173
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import google.generativeai as genai
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from google.api_core.exceptions import NotFound, ResourceExhausted
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DATA_DIR = Path(__file__).resolve().parent
load_dotenv(DATA_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("coach_bot")
SUBSCRIBERS_PATH = DATA_DIR / "subscribers.json"
USER_PROFILES_PATH = DATA_DIR / "user_profiles.json"

# Состояния онбординга.
OB_ASK_NAME = 1
OB_ASK_GENDER = 2
OB_ASK_PAIN = 3
OB_ASK_SITUATION = 4
OB_RAW_GOAL = 5
OB_ASK_GOAL_TYPE = 6
OB_ASK_AMOUNT = 7
OB_ASK_DEADLINE = 8
OB_ASK_SIGNALS = 9
OB_ASK_TIMEFRAME = 10
OB_ASK_TIME = 11
OB_FIRST_NEXT = 12

GENDER_ROWS: list[tuple[str, str]] = [
    ("male", "Он"),
    ("female", "Она"),
    ("neutral", "Без разницы"),
]

PAIN_ROWS: list[tuple[str, str]] = [
    ("money", "Хочу больше денег, потому что сейчас не хватает"),
    ("job", "Мне не нравится моя работа"),
    ("own", "Хочу начать что-то своё, но не делаю"),
    ("stuck", "Чувствую, что стою на месте"),
    ("fitness", "Хочу привести себя в форму, но постоянно сливаюсь"),
]

SITUATION_ROWS: list[tuple[str, str]] = [
    ("hire", "Работаю в найме"),
    ("self", "Работаю на себя"),
    ("business", "Есть свой бизнес"),
    ("none", "Пока не зарабатываю"),
    ("transition", "В переходе / не понимаю"),
]

# Признаки прогресса для неизмеримых (qualitative) целей.
SIGNAL_ROWS: list[tuple[str, str]] = [
    ("energy", "Больше энергии утром"),
    ("anxiety", "Меньше тревоги"),
    ("sleep", "Лучше сон"),
    ("stability", "Больше стабильности в делах"),
    ("joy", "Больше удовольствия от дня"),
]

TIMEFRAME_ROWS: list[tuple[str, str]] = [
    ("7", "7 дней"),
    ("14", "14 дней"),
    ("30", "30 дней"),
]

# Ключевые слова для эвристического классификатора.
_MEASURABLE_UNITS = (
    "₽", "$", "€", "руб", "долл", "евро", "тыс", "млн", "тысяч", "к/мес",
    "кг", "кило", "грамм",
    "клиент", "подписчик", "лид", "сделк", "продаж",
    "пост", "reels", "видео", "ролик", "статьи", "статья", "контент",
    "тренировк", "пробеж", "занятий", "занятия", "часа", "часов",
    "штук", "раз в", "раза в", "× ", "x ",
    "проц",
)

_QUALITATIVE_KEYWORDS = (
    "чувств", "состояни", "ощущ",
    "найти себя", "понять себя", "найти призван",
    "энерг", "тревог", "спокойств", "стабильн", "уверенност",
    "ясност", "наладить режим", "режим",
    "хочу больше", "сливаться", "вернуть интерес",
    "удовольств", "счастлив", "интерес к жизни",
    "перестать", "научиться чувствовать",
    "разобрать", "понять чего хочу", "понять чего я хочу",
)

_MALE_NAME_EXCEPTIONS = frozenset(
    {
        "илья", "никита", "фома", "кузьма", "миша", "дима", "паша",
        "коля", "ваня", "ванька", "петя", "лёша", "леша", "костя",
        "вова", "шура",
    }
)
_AMBIGUOUS_NAMES = frozenset({"женя", "саша"})

SYSTEM_INSTRUCTION = """Ты ведёшь диалог на русском как живой человек: просто, по делу, без шаблонов и «мотивационных» речей.
Тон: мягкий вход, дальше конкретика. Запрещено спрашивать «как настроение», «как спалось», «представь что уже есть»,
длинные восторженные абзацы, инфоцыганство, сухой коучинг.

Если даёшь шаг — один, маленький, выполнимый. Не выдавай себя за врача; медицины не давай.
Учитывай контекст профиля пользователя (цель, боль, ситуация, тип цели — measurable или qualitative), когда это уместно — коротко.
Для qualitative-целей не требуй конкретных цифр и сроков, говори про состояние и наблюдаемые признаки прогресса."""

MORNING_PROMPT = """Коротко (до 6 предложений), по-человечески. Напомни цель из контекста. Без «как настроение».
Не выдавай конкретную задачу на день в этом сообщении — только настрой и якорь на цель. Конец: приглашение написать, когда удобно."""

FIRST_TASK_AFTER_ONBOARD_PROMPT = """Пользователь только закончил короткое знакомство и нажал «Понимаю» — говорит, что примерно понимает, что делать дальше.
Дай один конкретный первый шаг (на сегодня или на ближайшие 1–2 дня): коротко, по делу. Без «мотивации», без опросов про настроение.
Если цель qualitative (про состояние) — шаг должен быть мягким наблюдательным действием, а не «выполни N раз».
Контекст профиля ниже."""

OPTIONS_AFTER_ONBOARD_PROMPT = """Пользователь только закончил знакомство и нажал «Пока нет» — не понимает, что делать дальше.
Предложи 2–3 конкретных варианта действий (маркированный список или короткие пункты), опираясь на профиль. Без длинных вступлений и без воды.
Если цель qualitative — варианты должны быть про мягкую наблюдательность за состоянием, а не KPI.
Контекст профиля ниже."""


def _load_subscribers() -> set[int]:
    if not SUBSCRIBERS_PATH.exists():
        return set()
    try:
        data = json.loads(SUBSCRIBERS_PATH.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except (json.JSONDecodeError, OSError, ValueError):
        return set()


def _save_subscribers(ids: set[int]) -> None:
    SUBSCRIBERS_PATH.write_text(
        json.dumps(sorted(ids), ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def _load_user_profiles() -> dict[str, dict]:
    if not USER_PROFILES_PATH.exists():
        return {}
    try:
        data = json.loads(USER_PROFILES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_user_profiles(profiles: dict[str, dict]) -> None:
    USER_PROFILES_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def gender_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"gender:{key}")]
        for key, label in GENDER_ROWS
    ]
    return InlineKeyboardMarkup(rows)


def pain_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"pain:{key}")]
        for key, label in PAIN_ROWS
    ]
    return InlineKeyboardMarkup(rows)


def situation_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"sit:{key}")]
        for key, label in SITUATION_ROWS
    ]
    return InlineKeyboardMarkup(rows)


def goal_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Измеримая (есть число)", callback_data="gt:measurable")],
            [InlineKeyboardButton("Про состояние", callback_data="gt:qualitative")],
        ]
    )


def signals_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in SIGNAL_ROWS:
        prefix = "✓ " if key in selected else ""
        rows.append([InlineKeyboardButton(prefix + label, callback_data=f"sig:{key}")])
    confirm = "Дальше →" if selected else "Выбери 1–2 признака"
    rows.append([InlineKeyboardButton(confirm, callback_data="sig:done")])
    return InlineKeyboardMarkup(rows)


def timeframe_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"tf:{key}")]
        for key, label in TIMEFRAME_ROWS
    ]
    return InlineKeyboardMarkup(rows)


def first_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Понимаю", callback_data="onboard_next:yes")],
            [InlineKeyboardButton("Пока нет", callback_data="onboard_next:no")],
        ]
    )


def _guess_gender_from_name(name: str) -> str | None:
    if not name or not str(name).strip():
        return None
    w = str(name).strip().split()[0].lower()
    if w in _AMBIGUOUS_NAMES:
        return None
    if w in _MALE_NAME_EXCEPTIONS:
        return "male"
    if len(w) < 2:
        return None
    if w.endswith(("ия",)) or (len(w) >= 3 and w.endswith("а") and w[-2] not in "йь"):
        return "female"
    if w.endswith("я"):
        return "female"
    return "male"


def _pain_label(key: str) -> str:
    return dict(PAIN_ROWS).get(key, key)


def _sit_label(key: str) -> str:
    return dict(SITUATION_ROWS).get(key, key)


def _signal_label(key: str) -> str:
    return dict(SIGNAL_ROWS).get(key, key)


def _has_digit(s: str) -> bool:
    return bool(re.search(r"\d", s))


def _classify_goal_type_heuristic(raw_goal: str) -> str:
    """Быстрая эвристика. Возвращает 'measurable', 'qualitative' или 'unclear'."""
    text = (raw_goal or "").lower()
    if not text:
        return "unclear"

    has_number = bool(re.search(r"\d", text))
    qual_hit = any(kw in text for kw in _QUALITATIVE_KEYWORDS)
    unit_hit = any(u in text for u in _MEASURABLE_UNITS)

    if qual_hit and not has_number and not unit_hit:
        return "qualitative"
    if has_number and unit_hit:
        return "measurable"
    if has_number and not qual_hit:
        return "measurable"
    if qual_hit and has_number:
        return "unclear"
    if unit_hit and not qual_hit:
        return "measurable"
    return "unclear"


async def _classify_goal_type(raw_goal: str, model_names: list[str]) -> str:
    """measurable / qualitative / ask_user — если ни эвристика, ни Gemini не уверены."""
    heuristic = _classify_goal_type_heuristic(raw_goal)
    if heuristic != "unclear":
        return heuristic

    prompt = (
        "Классифицируй цель пользователя. Ответь СТРОГО одним словом без пояснений: "
        "'measurable' — если цель имеет численные показатели (деньги, кг, количество, частота, срок). "
        "'qualitative' — если цель про состояние, ощущения, ясность, энергию, поиск себя, отношения с собой.\n\n"
        f"Цель: «{raw_goal.strip()}»\n\n"
        "Ответ:"
    )

    def call() -> str:
        for mid in model_names:
            try:
                m = genai.GenerativeModel(mid)
                r = m.generate_content(prompt)
                text = (getattr(r, "text", "") or "").strip().lower()
                if "qual" in text:
                    return "qualitative"
                if "meas" in text:
                    return "measurable"
            except (ResourceExhausted, NotFound):
                continue
            except Exception as e:
                log.warning("classify Gemini error on %s: %s", mid, e)
                continue
        return "ask_user"

    return await asyncio.to_thread(call)


def _signals_text(signals: list[str]) -> str:
    if not signals:
        return ""
    parts = [_signal_label(s).lower() for s in signals if s in dict(SIGNAL_ROWS)]
    return ", ".join(parts)


def _morning_template(profile: dict) -> str:
    name = str(profile.get("name", "")).strip() or "ты"
    gender = profile.get("gender", "neutral")
    if gender == "female":
        tail = "Напиши, когда будешь готова — подберу мягкое действие."
        tail_m = "Напиши, когда будешь готова — дам задачу."
    elif gender == "male":
        tail = "Напиши, когда будешь готов — подберу мягкое действие."
        tail_m = "Напиши, когда будешь готов — дам задачу."
    else:
        tail = "Напиши, когда будешь на связи — подберу мягкое действие."
        tail_m = "Напиши, когда будешь на связи — дам задачу."

    goal_type = str(profile.get("goal_type", "")).strip().lower()
    raw_goal = str(profile.get("raw_goal", "")).strip()
    final_goal = str(profile.get("final_goal", "")).strip()

    if goal_type == "qualitative":
        focus = raw_goal or "твоё состояние"
        return (
            f"Доброе утро, {name} ✨\n\n"
            f"Сегодня не надо становиться идеальной версией себя.\n"
            f"Держим фокус на твоём состоянии: {focus}.\n\n"
            "Один маленький шаг — и уже не ноль.\n\n"
            f"{tail}"
        )

    goal = final_goal or raw_goal or "свою цель"
    return (
        f"Доброе утро, {name} ✨\n\n"
        f"У тебя есть цель — {goal}.\n"
        "Сегодня нужен один маленький шаг.\n\n"
        f"{tail_m}"
    )


subscribers: set[int] = _load_subscribers()
user_profiles: dict[str, dict] = _load_user_profiles()
histories: dict[int, list[dict]] = {}
pending_morning: dict[int, str] = {}
onboarding: dict[int, dict[str, object]] = {}


def _select_gemini_model_id() -> str:
    preferred = os.getenv("GEMINI_MODEL", "").strip()
    available: list[str] = []
    try:
        for m in genai.list_models():
            methods = list(getattr(m, "supported_generation_methods", ()) or ())
            if "generateContent" not in methods:
                continue
            raw = getattr(m, "name", "") or ""
            short = raw.rsplit("/", 1)[-1] if raw else ""
            if short:
                available.append(short)
    except Exception as e:
        log.warning("list_models не удался (%s); пробуем модель по умолчанию", e)

    avail = set(available)
    preference_order = (
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-preview-05-20",
        "gemini-1.5-flash-8b",
        "gemini-1.5-flash-latest",
    )

    if preferred and preferred in avail:
        return preferred
    if preferred and avail:
        log.warning(
            "GEMINI_MODEL=%r нет среди доступных для этого ключа; подбираем другую",
            preferred,
        )

    if avail:
        for mid in preference_order:
            if mid in avail:
                return mid
        for x in sorted(avail):
            if "flash" in x.lower():
                return x
        return sorted(avail)[0]

    if preferred and preferred not in {
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-pro",
    }:
        return preferred
    return "gemini-2.0-flash"


def _build_model_chain(primary: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for part in [primary] + [
        x.strip() for x in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if x.strip()
    ]:
        if part not in seen:
            names.append(part)
            seen.add(part)
    for mid in (
        "gemini-2.5-flash",
        "gemini-2.5-flash-preview-05-20",
        "gemini-2.0-flash",
        "gemini-1.5-flash-8b",
        "gemini-1.5-flash-latest",
        "gemini-2.0-flash-lite-preview",
        "gemini-2.0-flash-lite",
    ):
        if mid not in seen:
            names.append(mid)
            seen.add(mid)
    return names


def _get_timezone() -> ZoneInfo:
    name = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("Invalid TIMEZONE=%r, using Asia/Ho_Chi_Minh", name)
        return ZoneInfo("Asia/Ho_Chi_Minh")


def _configure_genai() -> None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("В .env нужен GEMINI_API_KEY")
    genai.configure(api_key=key)


def _profile_snippet(chat_id: int) -> str:
    p = user_profiles.get(str(chat_id))
    if not p:
        return ""
    daily_time = p.get("daily_time", "09:30")
    g = p.get("gender", "neutral")
    gt = p.get("goal_type") or _classify_goal_type_heuristic(str(p.get("raw_goal", "")))
    signals = _signals_text(list(p.get("goal_signals") or []))
    parts = [
        f"Имя: {p.get('name', '')}.",
        f"Обращение: {g}.",
        f"Боль: {_pain_label(str(p.get('problem_type', '')))}.",
        f"Ситуация: {_sit_label(str(p.get('income_type', '')))}.",
        f"Тип цели: {gt}.",
        f"Цель (как писал человек): {p.get('raw_goal', '')}.",
        f"Итоговая цель: {p.get('final_goal', '')}.",
    ]
    if signals:
        parts.append(f"Признаки прогресса: {signals}.")
    parts.append(f"Время ежедневного сообщения: {daily_time}.")
    return " ".join(parts)


def _history_context_snippet(chat_id: int, max_turns: int = 14, max_chars: int = 700) -> str:
    hist = histories.get(chat_id) or []
    if not hist:
        return ""
    lines: list[str] = []
    for turn in hist[-max_turns:]:
        role = turn.get("role")
        parts = turn.get("parts") or []
        text = (parts[0] if parts else "")[:max_chars]
        who = "Она" if role == "user" else "Ты"
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


async def _generate_morning_question(model_names: list[str], chat_id: int) -> str:
    raw = user_profiles.get(str(chat_id))
    prof = raw if isinstance(raw, dict) else {}
    if prof.get("final_goal") or prof.get("goal_type") == "qualitative":
        return _morning_template(prof)

    profile = _profile_snippet(chat_id)
    ctx = _history_context_snippet(chat_id)
    bits: list[str] = []
    if profile:
        bits.append("Контекст профиля:\n" + profile)
    if ctx:
        bits.append("Недавняя переписка:\n" + ctx)
    extra = ("\n\n" + "\n\n".join(bits)) if bits else ""
    full_prompt = MORNING_PROMPT + extra

    def call() -> str:
        for mid in model_names:
            try:
                m = genai.GenerativeModel(mid, system_instruction=SYSTEM_INSTRUCTION)
                r = m.generate_content(full_prompt)
                text = (r.text or "").strip()
                if text:
                    return text
            except (ResourceExhausted, NotFound):
                continue
        name = prof.get("name", "")
        g = str(prof.get("final_goal") or prof.get("raw_goal") or "цель")
        return (
            f"Доброе утро, {name or '…'} ✨\n\n"
            f"Сегодня держим в голове: {g}\n\n"
            "Один маленький шаг — уже движение. Напиши, когда будешь готов продолжить."
        )

    return await asyncio.to_thread(call)


def _gemini_response_text(response: object) -> str:
    try:
        t = getattr(response, "text", None)
        if t and str(t).strip():
            return str(t).strip()
    except Exception:
        pass
    cands = getattr(response, "candidates", None) or ()
    if not cands:
        fb = getattr(response, "prompt_feedback", None)
        if fb and getattr(fb, "block_reason", None):
            return (
                "Модель не смогла ответить на эту формулировку (ограничение безопасности). "
                "Переформулируй короче или без личных оценок — продолжим."
            )
        return "Пустой ответ модели. Напиши ещё раз одним-двумя предложениями."

    c0 = cands[0]
    reason = getattr(c0, "finish_reason", None)
    if reason is not None:
        rname = str(reason)
        if rname and "STOP" not in rname and rname not in ("1", "FinishReason.STOP"):
            return f"Ответ оборвался ({reason}). Спроси иначе или короче — я на связи."
    return "Расскажи чуть подробнее — я слушаю."


async def _coach_reply(chat_id: int, user_text: str, model_names: list[str]) -> str:
    if chat_id in pending_morning:
        q = pending_morning.pop(chat_id)
        user_text = (
            f"(Пользователь отвечает на ежедневное напоминание: «{q}». "
            "Ответь по делу: один следующий шаг или уточнение — без опроса «как настроение» и без менторских речей.)\n\n"
            f"{user_text}"
        )

    hist = histories.setdefault(chat_id, [])
    history_prefixes: list[list[dict]] = [list(hist)]
    if len(hist) > 20:
        history_prefixes.append(hist[-20:])

    last_err: BaseException | None = None

    def try_models(hist_prefix: list[dict]) -> str | None:
        nonlocal last_err
        contents = list(hist_prefix) + [{"role": "user", "parts": [user_text]}]
        for mid in model_names:
            try:
                m = genai.GenerativeModel(mid, system_instruction=SYSTEM_INSTRUCTION)
                r = m.generate_content(contents)
                log.info("Gemini ответ через модель %s", mid)
                return _gemini_response_text(r)
            except ResourceExhausted as e:
                last_err = e
                log.warning("Gemini 429 (квота) на модели %s", mid)
                continue
            except NotFound:
                log.warning("Gemini 404 для модели %s", mid)
                continue
        return None

    reply: str | None = None
    for prefix in history_prefixes:
        reply = await asyncio.to_thread(try_models, prefix)
        if reply is not None:
            break

    if reply is None:
        if isinstance(last_err, ResourceExhausted):
            raise last_err
        raise RuntimeError("Ни одна модель Gemini не ответила")

    hist.append({"role": "user", "parts": [user_text]})
    hist.append({"role": "model", "parts": [reply]})

    max_turns = 40
    if len(hist) > max_turns:
        histories[chat_id] = hist[-max_turns:]

    return reply


def _parse_daily_time(raw: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _build_final_goal_for_measurable(amount: str, deadline: str) -> str:
    a = amount.strip()
    d = deadline.strip()
    if a and d:
        return f"{a} за {d}"
    return a or d


def _build_final_goal_for_qualitative(raw_goal: str, signals: list[str], timeframe: str) -> str:
    base = (raw_goal or "").strip().rstrip(".")
    sig_text = _signals_text(signals)
    tf = (timeframe or "").strip()
    if base and sig_text and tf:
        return f"{base} через {sig_text} за {tf} дней"
    if base and sig_text:
        return f"{base} через {sig_text}"
    return base


def _persist_onboarding_profile(cid: int, st: dict[str, object]) -> None:
    """Сохраняет профиль и сид истории для коуча. Поддерживает старый и новый формат."""
    name = str(st.get("name", "")).strip() or "друг"
    gender = str(st.get("gender", "neutral"))
    problem_type = str(st.get("problem_type", ""))
    income_type = str(st.get("income_type", ""))
    raw_goal = str(st.get("raw_goal", "")).strip()
    goal_type = str(st.get("goal_type", "")).strip().lower() or "measurable"
    amount = str(st.get("amount", "")).strip()
    deadline = str(st.get("deadline", "")).strip()
    method = str(st.get("method", "")).strip()
    goal_signals = list(st.get("goal_signals") or [])
    final_goal = str(st.get("final_goal", "")).strip()
    daily_time = str(st.get("daily_time", "09:30"))

    existing = user_profiles.get(str(cid)) or {}
    streak = int(existing.get("streak", 0) or 0)
    weekly_score = int(existing.get("weekly_score", 0) or 0)
    completed_tasks = list(existing.get("completed_tasks") or [])
    missed_tasks = list(existing.get("missed_tasks") or [])
    current_week = int(existing.get("current_week", 1) or 1)

    user_profiles[str(cid)] = {
        "name": name,
        "gender": gender,
        "problem_type": problem_type,
        "income_type": income_type,
        "raw_goal": raw_goal,
        "goal_type": goal_type,
        "amount": amount,
        "deadline": deadline,
        "method": method,
        "goal_signals": goal_signals,
        "final_goal": final_goal,
        "daily_time": daily_time,
        "streak": streak,
        "weekly_score": weekly_score,
        "completed_tasks": completed_tasks,
        "missed_tasks": missed_tasks,
        "current_week": current_week,
        "last_daily_sent_date": existing.get("last_daily_sent_date", ""),
        "onboarding_needs_first_next": True,
    }
    _save_user_profiles(user_profiles)

    blurb_extra = ""
    if goal_type == "qualitative" and goal_signals:
        blurb_extra = f" Признаки прогресса: {_signals_text(goal_signals)}."
    blurb = (
        f"[SpiceSpace] Имя: {name}, род обращения: {gender}. Боль: {_pain_label(problem_type)}. "
        f"Где сейчас: {_sit_label(income_type)}. Тип цели: {goal_type}. "
        f"Цель (сырой текст): {raw_goal}. Фиксация: {final_goal}.{blurb_extra} "
        f"Ежедневное сообщение в {daily_time}."
    )
    histories[cid] = [{"role": "user", "parts": [blurb]}]


async def _gemini_post_onboarding_reply(
    chat_id: int, model_names: list[str], mode: str
) -> str:
    profile = _profile_snippet(chat_id)
    intro = FIRST_TASK_AFTER_ONBOARD_PROMPT if mode == "task" else OPTIONS_AFTER_ONBOARD_PROMPT
    full_prompt = intro + ("\n\n" + profile if profile else "")

    p = user_profiles.get(str(chat_id)) or {}
    is_qual = str(p.get("goal_type", "")).strip().lower() == "qualitative"

    def call() -> str:
        for mid in model_names:
            try:
                m = genai.GenerativeModel(mid, system_instruction=SYSTEM_INSTRUCTION)
                r = m.generate_content(full_prompt)
                text = _gemini_response_text(r)
                if text and str(text).strip():
                    return str(text).strip()
            except (ResourceExhausted, NotFound):
                continue
        if mode == "task":
            if is_qual:
                return (
                    "Один первый шаг сегодня — мягко понаблюдать за собой: "
                    "запиши одно ощущение или мысль, которая повторяется. Этого достаточно."
                )
            return (
                "Один первый шаг: выбери сегодня одно действие по цели и запиши его одним предложением — "
                "когда и что именно сделаешь."
            )
        if is_qual:
            return (
                "Можно начать так:\n"
                "• 10 минут утром без телефона — отметить, что чувствуешь;\n"
                "• записать в заметках одно, что сегодня порадовало;\n"
                "• выбрать один признак из «отслеживаем» и обратить на него внимание."
            )
        return (
            "Можно начать так:\n"
            "• записать цель ещё раз своими словами и повесить на видное место;\n"
            "• выделить 20 минут без телефона на первый микрошаг;\n"
            "• найти одного человека, у кого можно спросить совет по теме."
        )

    return await asyncio.to_thread(call)


def _ensure_onboarding_first_next_from_profile(cid: int) -> None:
    if cid in onboarding:
        return
    p = user_profiles.get(str(cid))
    if isinstance(p, dict) and p.get("onboarding_needs_first_next"):
        onboarding[cid] = {"step": OB_FIRST_NEXT}


def _clear_onboarding_first_next_flag(cid: int) -> None:
    p = user_profiles.get(str(cid))
    if isinstance(p, dict) and p.pop("onboarding_needs_first_next", None) is not None:
        _save_user_profiles(user_profiles)


async def _begin_measurable_branch(msg, st: dict, raw_goal: str) -> None:
    st["goal_type"] = "measurable"
    if _has_digit(raw_goal):
        st["amount"] = raw_goal
        st["step"] = OB_ASK_DEADLINE
        await msg.reply_text("Окей. За какой срок хочешь? Любой ориентир: 7 дней, месяц, до конца лета.")
        return
    st["step"] = OB_ASK_AMOUNT
    await msg.reply_text(
        "Окей, цель измеримая. Сколько именно? Любое конкретное число — "
        "например 500$, 3 кг, 10 клиентов."
    )


async def _begin_qualitative_branch(msg, st: dict) -> None:
    st["goal_type"] = "qualitative"
    st["goal_signals"] = list(st.get("goal_signals") or [])
    st["step"] = OB_ASK_SIGNALS
    await msg.reply_text(
        "Окей. Это нормальная цель. Тут не всегда нужна цифра.\n"
        "Давай поймём, по каким признакам ты заметишь, что стало лучше.\n\n"
        "Выбери 1–2 признака:",
        reply_markup=signals_keyboard([]),
    )


async def handle_onboarding_turn(
    update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str
) -> None:
    cid = update.effective_chat.id
    msg = update.message
    st = onboarding.setdefault(cid, {"step": OB_ASK_NAME})
    step = int(st.get("step") or 0)

    if step == OB_ASK_NAME:
        name = raw.strip()[:120] or "друг"
        st["name"] = name
        guessed = _guess_gender_from_name(name)
        if guessed:
            st["gender"] = guessed
            st["step"] = OB_ASK_PAIN
            await msg.reply_text(
                "Давай по-честному. Что сейчас больше всего бесит?",
                reply_markup=pain_keyboard(),
            )
        else:
            st["step"] = OB_ASK_GENDER
            await msg.reply_text(
                "Как к тебе обращаться?",
                reply_markup=gender_keyboard(),
            )
        return

    if step == OB_ASK_GENDER:
        await msg.reply_text("Выбери вариант кнопкой ниже 👇")
        return

    if step == OB_ASK_PAIN:
        await msg.reply_text("Выбери пункт кнопкой — так быстрее 👇")
        return

    if step == OB_ASK_SITUATION:
        await msg.reply_text("Тоже кнопкой, пожалуйста 👇")
        return

    if step == OB_RAW_GOAL:
        raw_goal_text = raw.strip()[:4000]
        if not raw_goal_text:
            await msg.reply_text("Напиши как есть — одной-двумя строками. Любая формулировка.")
            return
        st["raw_goal"] = raw_goal_text

        model_names = context.bot_data.get("gemini_model_names") or []
        try:
            goal_type = await _classify_goal_type(raw_goal_text, model_names)
        except Exception as e:
            log.warning("classify failed: %s", e)
            goal_type = "ask_user"

        if goal_type == "measurable":
            await _begin_measurable_branch(msg, st, raw_goal_text)
            return
        if goal_type == "qualitative":
            await _begin_qualitative_branch(msg, st)
            return

        st["step"] = OB_ASK_GOAL_TYPE
        await msg.reply_text(
            "Уточню — эта цель скорее про конкретное число или про состояние?",
            reply_markup=goal_type_keyboard(),
        )
        return

    if step == OB_ASK_GOAL_TYPE:
        await msg.reply_text("Выбери кнопкой ниже 👇")
        return

    if step == OB_ASK_AMOUNT:
        amount = raw.strip()
        if not amount:
            await msg.reply_text(
                "Любое конкретное число — даже примерно. Например 500$, 3 кг, 10 клиентов."
            )
            return
        st["amount"] = amount[:500]
        st["step"] = OB_ASK_DEADLINE
        await msg.reply_text("За какой срок? 7 дней, месяц, до конца лета — любой ориентир.")
        return

    if step == OB_ASK_DEADLINE:
        dl = raw.strip()
        if not dl:
            await msg.reply_text(
                "Любой ориентир. Если совсем не понятно — давай возьмём 30 дней."
            )
            return
        st["deadline"] = dl[:500]
        amt = str(st.get("amount", "")).strip()
        st["final_goal"] = _build_final_goal_for_measurable(amt, dl)
        st["step"] = OB_ASK_TIME
        await msg.reply_text(
            "Во сколько писать каждый день?\nФормат HH:MM — например 09:30 или 18:00"
        )
        return

    if step == OB_ASK_SIGNALS:
        await msg.reply_text("Выбери 1–2 признака кнопками выше 👆")
        return

    if step == OB_ASK_TIMEFRAME:
        await msg.reply_text("Выбери срок кнопкой ниже 👇", reply_markup=timeframe_keyboard())
        return

    if step == OB_ASK_TIME:
        parsed = _parse_daily_time(raw)
        if parsed is None:
            await msg.reply_text(
                "Напиши время в формате HH:MM — например 09:30 или 18:00"
            )
            return
        st["daily_time"] = parsed
        _persist_onboarding_profile(cid, st)
        st["step"] = OB_FIRST_NEXT
        fg = str(st.get("final_goal", "")).strip() or str(st.get("raw_goal", ""))
        gt = str(st.get("goal_type", "")).strip().lower()
        if gt == "qualitative":
            sig_text = _signals_text(list(st.get("goal_signals") or []))
            fixation = fg or st.get("raw_goal", "")
            await msg.reply_text(
                "Окей.\n\n"
                f"Фиксируем:\n\n{fixation}\n\n"
                + (f"Отслеживаем: {sig_text}\n\n" if sig_text else "")
                + "С этим уже можно работать.\n\n"
                "Ты примерно понимаешь, что делать дальше или пока нет?",
                reply_markup=first_next_keyboard(),
            )
        else:
            await msg.reply_text(
                "Окей.\n\n"
                f"Фиксируем:\n\n{fg}\n\n"
                "С этим уже можно работать.\n\n"
                "Давай не откладывать.\n\n"
                "Ты примерно понимаешь, что делать дальше\nили пока нет?",
                reply_markup=first_next_keyboard(),
            )
        await msg.reply_text("/stop — выключить ежедневные сообщения.")
        return

    if step == OB_FIRST_NEXT:
        await msg.reply_text(
            "Тут лучше нажми кнопку под предыдущим сообщением — так я пойму, с чего начать."
        )
        return

    log.warning("onboarding: неизвестный step=%s chat_id=%s", step, cid)
    await msg.reply_text("Что-то пошло не так с анкетой. Нажми /start и пройди её с начала.")


async def onboarding_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    cid = q.message.chat.id
    st = onboarding.get(cid)
    if not st:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    data = q.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2:
        return
    kind, key = parts
    step = int(st.get("step") or 0)

    if kind == "gender" and step == OB_ASK_GENDER and key in dict(GENDER_ROWS):
        st["gender"] = key
        st["step"] = OB_ASK_PAIN
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text(
            "Давай по-честному. Что сейчас больше всего бесит?",
            reply_markup=pain_keyboard(),
        )
        return

    if kind == "pain" and step == OB_ASK_PAIN and key in dict(PAIN_ROWS):
        st["problem_type"] = key
        st["step"] = OB_ASK_SITUATION
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text(
            "Где ты сейчас?",
            reply_markup=situation_keyboard(),
        )
        return

    if kind == "sit" and step == OB_ASK_SITUATION and key in dict(SITUATION_ROWS):
        st["income_type"] = key
        st["step"] = OB_RAW_GOAL
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text("Напиши как есть — чего хочешь")
        return


async def onboarding_goal_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    cid = q.message.chat.id
    st = onboarding.get(cid)
    if not st or int(st.get("step") or 0) != OB_ASK_GOAL_TYPE:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    data = q.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != "gt":
        return
    key = parts[1]
    if key not in ("measurable", "qualitative"):
        return

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    raw_goal_text = str(st.get("raw_goal", ""))
    if key == "measurable":
        await _begin_measurable_branch(q.message, st, raw_goal_text)
    else:
        await _begin_qualitative_branch(q.message, st)


async def onboarding_signals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    cid = q.message.chat.id
    st = onboarding.get(cid)
    if not st or int(st.get("step") or 0) != OB_ASK_SIGNALS:
        await q.answer()
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    data = q.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != "sig":
        await q.answer()
        return
    key = parts[1]

    selected = list(st.get("goal_signals") or [])

    if key == "done":
        if not selected:
            await q.answer("Выбери хотя бы один признак", show_alert=False)
            return
        await q.answer()
        st["goal_signals"] = selected
        st["step"] = OB_ASK_TIMEFRAME
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text(
            "На какой срок берём первый этап?",
            reply_markup=timeframe_keyboard(),
        )
        return

    if key in dict(SIGNAL_ROWS):
        if key in selected:
            selected.remove(key)
            await q.answer()
        else:
            if len(selected) >= 2:
                await q.answer("Достаточно одного-двух — нажми «Дальше»", show_alert=False)
                return
            selected.append(key)
            await q.answer()
        st["goal_signals"] = selected
        try:
            await q.edit_message_reply_markup(reply_markup=signals_keyboard(selected))
        except Exception:
            pass
        return

    await q.answer()


async def onboarding_timeframe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    cid = q.message.chat.id
    st = onboarding.get(cid)
    if not st or int(st.get("step") or 0) != OB_ASK_TIMEFRAME:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    data = q.data or ""
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != "tf":
        return
    key = parts[1]
    if key not in dict(TIMEFRAME_ROWS):
        return

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    st["deadline"] = f"{key} дней"
    raw_goal_text = str(st.get("raw_goal", ""))
    st["final_goal"] = _build_final_goal_for_qualitative(
        raw_goal_text, list(st.get("goal_signals") or []), key
    )
    st["step"] = OB_ASK_TIME
    await q.message.reply_text(
        "Во сколько писать каждый день?\nФормат HH:MM — например 09:30 или 18:00"
    )


async def onboarding_first_next_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    q = update.callback_query
    if not q or not q.message:
        return
    await q.answer()
    cid = q.message.chat.id
    _ensure_onboarding_first_next_from_profile(cid)
    data = q.data or ""
    parts = data.split(":", 1)
    key = parts[1] if len(parts) == 2 else ""
    st = onboarding.get(cid)
    if not st or int(st.get("step") or 0) != OB_FIRST_NEXT or key not in ("yes", "no"):
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    onboarding.pop(cid, None)
    _clear_onboarding_first_next_flag(cid)
    model_names: list[str] = context.bot_data["gemini_model_names"]
    mode = "task" if key == "yes" else "options"

    user_note = (
        "После онбординга: нажал «Понимаю» — хочу конкретный первый шаг."
        if key == "yes"
        else "После онбординга: нажал «Пока нет» — нужны варианты, с чего начать."
    )

    try:
        reply = await _gemini_post_onboarding_reply(cid, model_names, mode)
    except ResourceExhausted:
        log.exception("Gemini quota after onboarding branch")
        reply = (
            "С Google AI сейчас лимит запросов. Подожди минуту и напиши сюда одним сообщением — подскажу шаг."
            if key == "yes"
            else "С Google AI сейчас лимит запросов. Подожди минуту и напиши — набросаю варианты."
        )
    except Exception as e:
        log.exception("Gemini error post-onboarding: %s", e)
        reply = (
            "Сейчас не получилось сгенерировать ответ. Напиши одним сообщением — продолжим с шага."
            if key == "yes"
            else "Сейчас не получилось набросать варианты. Напиши одним сообщением — продолжим."
        )

    hist = histories.setdefault(cid, [])
    hist.append({"role": "user", "parts": [user_note]})
    hist.append({"role": "model", "parts": [reply]})
    max_turns = 40
    if len(hist) > max_turns:
        histories[cid] = hist[-max_turns:]

    await q.message.reply_text(reply)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    cid = update.effective_chat.id
    subscribers.add(cid)
    _save_subscribers(subscribers)
    onboarding[cid] = {"step": OB_ASK_NAME}
    prof = user_profiles.get(str(cid))
    if isinstance(prof, dict) and prof.pop("onboarding_needs_first_next", None) is not None:
        _save_user_profiles(user_profiles)
    await update.message.reply_text(
        "Привет ✨ Есть ощущение, что у тебя может получиться сильно больше. Давай попробуем. Как тебя зовут?"
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    cid = update.effective_chat.id
    subscribers.discard(cid)
    pending_morning.pop(cid, None)
    _save_subscribers(subscribers)
    await update.message.reply_text("Утренние напоминания выключены. Напиши /start, чтобы снова включить.")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    cid = update.effective_chat.id
    histories.pop(cid, None)
    pending_morning.pop(cid, None)
    onboarding.pop(cid, None)
    prof = user_profiles.get(str(cid))
    if isinstance(prof, dict) and prof.pop("onboarding_needs_first_next", None) is not None:
        _save_user_profiles(user_profiles)
    await update.message.reply_text(
        "Контекст диалога сброшен. Можем начать с чистого листа. Чтобы пройти знакомство снова — /start."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.text:
        return
    cid = update.effective_chat.id
    raw = update.message.text.strip()
    if not raw:
        return

    _ensure_onboarding_first_next_from_profile(cid)
    st_ob = onboarding.get(cid)
    if st_ob and int(st_ob.get("step") or 0) > 0:
        await handle_onboarding_turn(update, context, raw)
        return

    log.info("incoming text chat_id=%s len=%s", cid, len(raw))
    model_names: list[str] = context.bot_data["gemini_model_names"]

    try:
        reply = await _coach_reply(cid, raw, model_names)
    except ResourceExhausted:
        log.exception("Gemini quota exhausted")
        await update.message.reply_text(
            "У Google AI сейчас лимит бесплатных запросов (ошибка 429): слишком частые сообщения "
            "или дневная квота исчерпана. Подожди 1–2 минуты и напиши снова.\n\n"
            "Если так постоянно: зайди в Google AI Studio → проверь ключ и лимиты "
            "https://ai.google.dev/gemini-api/docs/rate-limits — для проекта иногда нужно "
            "включить биллинг или выбрать другую модель в .env (GEMINI_MODEL)."
        )
        return
    except Exception as e:
        log.exception("Gemini error: %s", e)
        await update.message.reply_text(
            "Сейчас не получилось связаться с моделью. Попробуй ещё раз через минуту."
        )
        return

    await update.message.reply_text(reply)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_message:
        return
    cid = update.effective_chat.id
    _ensure_onboarding_first_next_from_profile(cid)
    st_ob = onboarding.get(cid)
    if st_ob:
        step = int(st_ob.get("step") or 0)
        if step == OB_FIRST_NEXT:
            await update.effective_message.reply_text(
                "Сейчас выбери вариант кнопкой под сообщением с вопросом."
            )
            return
        if step > 0:
            await update.effective_message.reply_text(
                "Давай до конца анкету текстом — голос чуть позже 💛"
            )
            return
    await update.effective_message.reply_text(
        "Голосовые сообщения пока не расшифровываю — напиши текстом, так диалог стабильнее."
    )


# --------------------------- HTTP API for Mini App ---------------------------

def _allowed_origins() -> set[str]:
    raw = os.getenv(
        "MINIAPP_ORIGINS",
        "https://spicespace-miniapp.vercel.app,http://localhost:5173,http://localhost:3000",
    )
    return {x.strip() for x in raw.split(",") if x.strip()}


def _extract_init_data(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("tma "):
        return auth[len("tma "):].strip()
    return (request.query.get("initData") or "").strip()


def _validate_init_data(init_data: str, max_age_seconds: int = 24 * 60 * 60) -> dict | None:
    if not init_data:
        return None

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None

    pairs.pop("signature", None)

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return None

    auth_date_str = pairs.get("auth_date", "")
    try:
        auth_date = int(auth_date_str)
        if (datetime.now().timestamp() - auth_date) > max_age_seconds:
            return None
    except (ValueError, TypeError):
        return None

    user_raw = pairs.get("user", "")
    if not user_raw:
        return None
    try:
        return json.loads(user_raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _enrich_profile_for_api(profile: dict) -> dict:
    """Старые профили без новых полей получают разумные дефолты при отдаче в Mini App."""
    p = dict(profile)
    if not p.get("goal_type"):
        p["goal_type"] = "measurable" if p.get("amount") or _has_digit(str(p.get("raw_goal", ""))) else "qualitative"
    p.setdefault("goal_signals", [])
    p.setdefault("method", "")
    p.setdefault("streak", 0)
    p.setdefault("weekly_score", 0)
    p.setdefault("completed_tasks", [])
    p.setdefault("missed_tasks", [])
    p.setdefault("current_week", 1)
    return p


@web.middleware
async def _cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    allowed = _allowed_origins()
    origin = request.headers.get("Origin", "")
    allow = origin if (origin and origin in allowed) else ""

    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as http_exc:
            resp = http_exc

    if allow:
        resp.headers["Access-Control-Allow-Origin"] = allow
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


async def _api_options(request: web.Request) -> web.Response:
    return web.Response(status=204)


async def _api_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _api_profile(request: web.Request) -> web.Response:
    init_data = _extract_init_data(request)
    user_obj = _validate_init_data(init_data) if init_data else None
    if not user_obj:
        return web.json_response({"error": "unauthorized"}, status=401)
    tid = str(user_obj.get("id", ""))
    if not tid:
        return web.json_response({"error": "invalid user"}, status=400)
    profile = user_profiles.get(tid)
    return web.json_response(
        {
            "telegram_id": tid,
            "profile": _enrich_profile_for_api(profile) if profile else None,
            "user": {
                "first_name": user_obj.get("first_name"),
                "username": user_obj.get("username"),
                "photo_url": user_obj.get("photo_url"),
            },
        }
    )


async def _start_api_server(port: int) -> web.AppRunner | None:
    try:
        api = web.Application(middlewares=[_cors_middleware])
        api.router.add_get("/health", _api_health)
        api.router.add_get("/api/profile", _api_profile)
        api.router.add_route("OPTIONS", "/{tail:.*}", _api_options)
        runner = web.AppRunner(api)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=port)
        await site.start()
        log.info("API server listening on :%s (origins=%s)", port, sorted(_allowed_origins()))
        return runner
    except Exception as e:
        log.exception("Failed to start API server: %s", e)
        return None


# --------------------------- Application bootstrap ---------------------------

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("В .env нужен TELEGRAM_BOT_TOKEN")

    _configure_genai()
    model_name = _select_gemini_model_id()
    model_chain = _build_model_chain(model_name)
    gemini_model = genai.GenerativeModel(
        model_name=model_chain[0],
        system_instruction=SYSTEM_INSTRUCTION,
    )

    tz = _get_timezone()
    scheduler = AsyncIOScheduler(
        timezone=tz,
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    api_port = int(os.getenv("PORT", "8080"))

    async def post_init(application: Application) -> None:
        scheduler.start()
        log.info("Scheduler started: daily_check each minute via IntervalTrigger (%s)", tz)
        runner = await _start_api_server(api_port)
        application.bot_data["api_runner"] = runner

    async def post_shutdown(application: Application) -> None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        runner = application.bot_data.get("api_runner")
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                pass

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["gemini_model"] = gemini_model
    app.bot_data["gemini_model_names"] = model_chain

    async def daily_check_job() -> None:
        try:
            now = datetime.now(tz).strftime("%H:%M")
            today = datetime.now(tz).strftime("%Y-%m-%d")
            for cid in list(subscribers):
                key = str(cid)
                raw_p = user_profiles.get(key)
                if not isinstance(raw_p, dict):
                    raw_p = {}
                    user_profiles[key] = raw_p
                profile = raw_p
                daily_time = profile.get("daily_time", "09:30")
                if daily_time != now:
                    continue
                if profile.get("last_daily_sent_date") == today:
                    continue
                try:
                    question = await _generate_morning_question(model_chain, cid)
                    pending_morning[cid] = question
                    await app.bot.send_message(chat_id=cid, text=question)
                    profile["last_daily_sent_date"] = today
                    _save_user_profiles(user_profiles)
                except Exception as e:
                    log.warning("Daily message failed for %s: %s", cid, e)
        except Exception as e:
            log.exception("daily_check_job crashed: %s", e)

    scheduler.add_job(
        daily_check_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="daily_check",
        replace_existing=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(
        CallbackQueryHandler(
            onboarding_first_next_callback,
            pattern=r"^onboard_next:(yes|no)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            onboarding_goal_type_callback,
            pattern=r"^gt:(measurable|qualitative)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            onboarding_signals_callback,
            pattern=r"^sig:(energy|anxiety|sleep|stability|joy|done)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            onboarding_timeframe_callback,
            pattern=r"^tf:(7|14|30)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            onboarding_inline_callback,
            pattern=r"^(gender|pain|sit):(male|female|neutral|money|job|own|stuck|fitness|hire|self|business|none|transition)$",
        )
    )
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Starting bot, Gemini primary=%s chain=%s", model_chain[0], model_chain[:5])
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Бот остановлен (Ctrl+C).")
