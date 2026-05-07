"""
SpiceSpace Telegram bot: онбординг, индивидуальное время ежедневного сообщения (по TIMEZONE в .env), Gemini-диалог.
Secrets: TELEGRAM_BOT_TOKEN, GEMINI_API_KEY in .env

Optional .env:
  TIMEZONE=Asia/Ho_Chi_Minh
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_FALLBACK_MODELS=gemini-1.5-flash-8b,gemini-2.0-flash-lite   # через запятую, при 429/квоте
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import google.generativeai as genai
from google.api_core.exceptions import NotFound, ResourceExhausted
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
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
# Явный путь: иначе при запуске `python spicespace-bot\main.py` из родительской папки
# load_dotenv() ищет .env в cwd и не находит токен.
load_dotenv(DATA_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("coach_bot")
SUBSCRIBERS_PATH = DATA_DIR / "subscribers.json"
USER_PROFILES_PATH = DATA_DIR / "user_profiles.json"

# Онбординг MVP + время
OB_ASK_NAME = 1
OB_ASK_GENDER = 2
OB_ASK_PAIN = 3
OB_ASK_SITUATION = 4
OB_RAW_GOAL = 5
OB_ASK_AMOUNT = 6
OB_ASK_DEADLINE = 7
OB_ASK_TIME = 8
OB_FIRST_NEXT = 9

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

_MALE_NAME_EXCEPTIONS = frozenset(
    {
        "илья",
        "никита",
        "фома",
        "кузьма",
        "миша",
        "дима",
        "паша",
        "коля",
        "ваня",
        "ванька",
        "петя",
        "лёша",
        "леша",
        "костя",
        "вова",
        "шура",
    }
)
_AMBIGUOUS_NAMES = frozenset({"женя", "саша"})

SYSTEM_INSTRUCTION = """Ты ведёшь диалог на русском как живой человек: просто, по делу, без шаблонов и «мотивационных» речей.
Тон: мягкий вход, дальше конкретика. Запрещено спрашивать «как настроение», «как спалось», «представь что уже есть»,
длинные восторженные абзацы, инфоцыганство, сухой коучинг.

Если даёшь шаг — один, маленький, выполнимый. Не выдавай себя за врача; медицины не давай.
Учитывай контекст профиля пользователя (цель, боль, ситуация), когда это уместно — коротко."""

MORNING_PROMPT = """Коротко (до 6 предложений), по-человечески. Напомни цель из контекста. Без «как настроение».
Не выдавай конкретную задачу на день в этом сообщении — только настрой и якорь на цель. Конец: приглашение написать, когда удобно."""

FIRST_TASK_AFTER_ONBOARD_PROMPT = """Пользователь только закончил короткое знакомство и нажал «Понимаю» — говорит, что примерно понимает, что делать дальше.
Дай один конкретный первый шаг (на сегодня или на ближайшие 1–2 дня): коротко, по делу. Без «мотивации», без опросов про настроение.
Контекст профиля ниже."""

OPTIONS_AFTER_ONBOARD_PROMPT = """Пользователь только закончил знакомство и нажал «Пока нет» — не понимает, что делать дальше.
Предложи 2–3 конкретных варианта действий (маркированный список или короткие пункты), опираясь на профиль. Без длинных вступлений и без воды.
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


def first_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Понимаю", callback_data="onboard_next:yes")],
            [InlineKeyboardButton("Пока нет", callback_data="onboard_next:no")],
        ]
    )


def _guess_gender_from_name(name: str) -> str | None:
    """male / female / None (нужен вопрос с кнопками)."""
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


def _has_digit(s: str) -> bool:
    return bool(re.search(r"\d", s))


def _morning_template(profile: dict) -> str:
    name = str(profile.get("name", "")).strip() or "ты"
    goal = str(profile.get("final_goal") or profile.get("raw_goal") or "свою цель").strip()
    gender = profile.get("gender", "neutral")
    if gender == "female":
        tail = "Напиши, когда будешь готова — дам задачу."
    elif gender == "male":
        tail = "Напиши, когда будешь готов — дам задачу."
    else:
        tail = "Напиши, когда будешь на связи — дам задачу."
    return (
        f"Доброе утро, {name} ✨\n\n"
        f"Как бы ни начался этот день, у тебя есть цель — {goal}\n\n"
        "Она не делается сама.\n"
        "Но она делается маленькими шагами.\n\n"
        "Сегодня нужен один.\n\n"
        f"{tail}"
    )


subscribers: set[int] = _load_subscribers()
user_profiles: dict[str, dict] = _load_user_profiles()
histories: dict[int, list[dict]] = {}
pending_morning: dict[int, str] = {}
# chat_id -> состояние шага онбординга
onboarding: dict[int, dict[str, object]] = {}


def _select_gemini_model_id() -> str:
    """Имя модели для GenerativeModel: 1.5-flash в v1beta часто даёт 404 — выбираем рабочую."""
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

    # list_models недоступен — не подставляем устаревшие имена, из‑за них был 404
    if preferred and preferred not in {
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-pro",
    }:
        return preferred
    return "gemini-2.0-flash"


def _build_model_chain(primary: str) -> list[str]:
    """Порядок моделей: основная, из GEMINI_FALLBACK_MODELS, затем запасные (при 429/лимитах)."""
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
    return (
        f"Имя: {p.get('name', '')}. Обращение: {g}. "
        f"Боль: {_pain_label(str(p.get('problem_type', '')))}. "
        f"Ситуация: {_sit_label(str(p.get('income_type', '')))}. "
        f"Цель (как писал человек): {p.get('raw_goal', '')}. "
        f"Итоговая цель: {p.get('final_goal', '')}. "
        f"Время ежедневного сообщения: {daily_time}."
    )


def _history_context_snippet(chat_id: int, max_turns: int = 14, max_chars: int = 700) -> str:
    """Фрагмент истории для утреннего сообщения — чтобы напомнить цель из прошлых реплик."""
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
    if prof.get("final_goal"):
        return _morning_template(prof)

    profile = _profile_snippet(chat_id)
    ctx = _history_context_snippet(chat_id)
    extra = ""
    bits: list[str] = []
    if profile:
        bits.append("Контекст профиля:\n" + profile)
    if ctx:
        bits.append("Недавняя переписка:\n" + ctx)
    if bits:
        extra = "\n\n" + "\n\n".join(bits)
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
    """Текст ответа; при блокировке/пустом candidates — понятное сообщение вместо падения на .text."""
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
    # 1 / STOP = нормальное завершение; остальное — обрыв или фильтр
    if reason is not None:
        rname = str(reason)
        if rname and "STOP" not in rname and rname not in ("1", "FinishReason.STOP"):
            return (
                f"Ответ оборвался ({reason}). Спроси иначе или короче — я на связи."
            )
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
    # Сначала полная история; при нехватке квоты — короче (меньше токенов в минуту).
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
    """Возвращает нормализованное HH:MM или None, если неверный формат (00:00–23:59)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _persist_onboarding_profile(cid: int, st: dict[str, object]) -> None:
    """Сохраняет профиль и сиды истории для коуча. Не снимает шаг онбординга (нужен для кнопок «Понимаю / Пока нет»)."""
    name = str(st.get("name", "")).strip() or "друг"
    gender = str(st.get("gender", "neutral"))
    problem_type = str(st.get("problem_type", ""))
    income_type = str(st.get("income_type", ""))
    raw_goal = str(st.get("raw_goal", "")).strip()
    amount = str(st.get("amount", "")).strip()
    deadline = str(st.get("deadline", "")).strip()
    final_goal = str(st.get("final_goal", "")).strip()
    daily_time = str(st.get("daily_time", "09:30"))

    user_profiles[str(cid)] = {
        "name": name,
        "gender": gender,
        "problem_type": problem_type,
        "income_type": income_type,
        "raw_goal": raw_goal,
        "amount": amount,
        "deadline": deadline,
        "final_goal": final_goal,
        "daily_time": daily_time,
        "last_daily_sent_date": "",
        # Пока пользователь не нажал «Понимаю»/«Пока нет», восстанавливаем шаг после перезапуска бота
        "onboarding_needs_first_next": True,
    }
    _save_user_profiles(user_profiles)

    blurb = (
        f"[SpiceSpace] Имя: {name}, род обращения: {gender}. Боль: {_pain_label(problem_type)}. "
        f"Где сейчас: {_sit_label(income_type)}. Цель (сырой текст): {raw_goal}. "
        f"Фиксация: {final_goal}. Ежедневное сообщение в {daily_time}."
    )
    histories[cid] = [{"role": "user", "parts": [blurb]}]


async def _gemini_post_onboarding_reply(
    chat_id: int, model_names: list[str], mode: str
) -> str:
    """mode: 'task' — один шаг; 'options' — 2–3 варианта."""
    profile = _profile_snippet(chat_id)
    if mode == "task":
        intro = FIRST_TASK_AFTER_ONBOARD_PROMPT
    else:
        intro = OPTIONS_AFTER_ONBOARD_PROMPT
    full_prompt = intro + ("\n\n" + profile if profile else "")

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
            return (
                "Один первый шаг: выбери сегодня одно действие по цели и запиши его одним предложением — "
                "когда и что именно сделаешь."
            )
        return (
            "Можно начать так:\n"
            "• записать цель ещё раз своими словами и повесить на видное место;\n"
            "• выделить 20 минут без телефона на первый микрошаг;\n"
            "• найти одного человека, у кого можно спросить совет по теме."
        )

    return await asyncio.to_thread(call)


def _ensure_onboarding_first_next_from_profile(cid: int) -> None:
    """Если профиль ждёт нажатия кнопок после времени — восстановить шаг в памяти (после рестарта процесса)."""
    if cid in onboarding:
        return
    p = user_profiles.get(str(cid))
    if isinstance(p, dict) and p.get("onboarding_needs_first_next"):
        onboarding[cid] = {"step": OB_FIRST_NEXT}


def _clear_onboarding_first_next_flag(cid: int) -> None:
    p = user_profiles.get(str(cid))
    if isinstance(p, dict) and p.pop("onboarding_needs_first_next", None) is not None:
        _save_user_profiles(user_profiles)


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
        st["raw_goal"] = raw.strip()[:4000]
        st["step"] = OB_ASK_AMOUNT
        await msg.reply_text("Сколько именно?")
        return

    if step == OB_ASK_AMOUNT:
        if not _has_digit(raw):
            await msg.reply_text("Без числа это не цель. Напиши конкретно.")
            return
        st["amount"] = raw.strip()[:500]
        st["step"] = OB_ASK_DEADLINE
        await msg.reply_text("За какой срок?")
        return

    if step == OB_ASK_DEADLINE:
        dl = raw.strip()
        if not dl:
            await msg.reply_text("Без срока это ни о чём. Укажи.")
            return
        st["deadline"] = dl[:500]
        amt = str(st.get("amount", "")).strip()
        st["final_goal"] = f"{amt} за {dl}"
        await msg.reply_text(
            "Во сколько писать каждый день?\nФормат HH:MM — например 09:30 или 18:00"
        )
        st["step"] = OB_ASK_TIME
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
        fg = str(st.get("final_goal", "")).strip()
        await msg.reply_text(
            "Окей.\n\n"
            f"Фиксируем:\n\n{fg}\n\n"
            "С этим уже можно работать.\n\n"
            "Давай не откладывать.\n\n"
            "Ты примерно понимаешь, что делать дальше\n"
            "или пока нет?",
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

    async def post_init(application: Application) -> None:
        scheduler.start()
        log.info("Scheduler started: daily_check each minute via IntervalTrigger (%s)", tz)

    async def post_shutdown(application: Application) -> None:
        scheduler.shutdown(wait=False)

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
        # Нормальная остановка по Ctrl+C; без полного traceback в консоли
        log.info("Бот остановлен (Ctrl+C).")
