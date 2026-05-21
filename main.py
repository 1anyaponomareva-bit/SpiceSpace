"""
SpiceSpace Telegram bot: companion с памятью, онбординг, утро/вечер daily loop,
daily_summaries в Supabase, Claude API с prompt caching, HTTP API для Mini App.

Secrets: TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY in .env

Optional .env:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  TIMEZONE=Asia/Ho_Chi_Minh  (дефолт для новых пользователей)
  CLAUDE_MODEL=claude-sonnet-4-20250514
  CLAUDE_FALLBACK_MODELS=claude-3-5-haiku-20241022
  PORT=8080
  MINIAPP_ORIGINS=...
  MINI_APP_URL=...
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import anthropic
import uvicorn

import db as db_store
import onboarding_flow as ob
from claude_client import build_model_chain, configure as configure_claude, generate as claude_generate
from claude_client import select_model_id
from prompts import (
    MORNING_TASK_PROMPT,
    build_chat_system,
    evening_opening,
    evening_reply_done,
    evening_reply_missed,
    morning_opening,
)
from summaries import maybe_save_daily_summary
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction
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
TASKS_PATH = DATA_DIR / "tasks.json"

# Онбординг — см. onboarding_flow.py (без кнопок)
OB_RETURNING = ob.OB_RETURNING
OB_ASK_NAME = ob.OB_ASK_NAME
OB_GOAL_DIALOG = ob.OB_GOAL_DIALOG
OB_ASK_MORNING_TIME = ob.OB_ASK_MORNING_TIME
OB_ASK_EVENING_TIME = ob.OB_ASK_EVENING_TIME

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

# Направления (focus) когда у человека большое видение / много целей.
FOCUS_ROWS: list[tuple[str, str]] = [
    ("money", "💰 Деньги"),
    ("relocation", "✈️ Переезд / Америка"),
    ("media", "🎤 Медийность"),
    ("instagram", "📷 Instagram"),
    ("tiktok", "🎵 TikTok"),
    ("blogs", "📰 Блоги / контент"),
    ("other", "✏️ Другое (своё)"),
]

# Под-цели на 30 дней внутри каждого направления (ключ -> [(subkey, label)]).
FOCUS_GOAL_ROWS: dict[str, list[tuple[str, str]]] = {
    "money": [
        ("first_500", "Заработать первые $500"),
        ("find_source", "Найти источник дохода"),
        ("sell_product", "Продать продукт/услугу"),
        ("find_niche", "Понять, на чём зарабатывать"),
    ],
    "media": [
        ("daily_post", "Публиковаться каждый день"),
        ("series", "Запустить рубрику/сериал"),
        ("first_1000", "Набрать первые +1000 подписчиков"),
        ("test_formats", "Протестировать 3 формата"),
    ],
    "instagram": [
        ("reels_30", "30 Reels за 30 дней"),
        ("subs_1000", "+1000 подписчиков"),
        ("formats_3", "Найти 3 формата, которые цепляют"),
        ("content_system", "Собрать контент-систему"),
    ],
    "tiktok": [
        ("videos_30", "30 видео за 30 дней"),
        ("test_formats", "Протестировать 3 формата"),
        ("adapt", "Адаптировать контент под язык/рынок"),
        ("first_views", "Стабильные первые просмотры"),
    ],
    "blogs": [
        ("pick_3", "Выбрать 3 направления блогов"),
        ("matrix", "Расписать контент-матрицу"),
        ("first_10", "Выпустить первые 10 публикаций"),
        ("test_3", "Протестировать 3 аккаунта"),
    ],
    "relocation": [
        ("path", "Понять реальный путь переезда"),
        ("first_step", "Сделать один конкретный шаг (документы/консультация)"),
        ("english", "Стабильная практика английского"),
        ("research", "Выбрать 2–3 страны и проверить условия"),
    ],
}

# Лёгкие сигналы направлений: используем для разбора большого vision-текста.
_FOCUS_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("money", ("$", "₽", "€", "доллар", "рубл", "евро", "деньг", "доход",
               "заработ", "зарплат", "продаж", "клиент", "млн", "тыс", "к/мес", "kk")),
    ("relocation", ("америк", "сша", "usa", "us ", "переезд", "релокац",
                    "виза", "грин-карт", "грин карт", "иммигр", "uk", "англи",
                    "канад", "европ", "lisbon", "берлин")),
    ("media", ("медийн", "извест", "узнаваем", "узнавай", "персон",
               "публичн", "блогер", "стать звезд")),
    ("instagram", ("instagram", " инст", "инста", " ig ", " ig\n", "reels", "рилс")),
    ("tiktok", ("tiktok", "тик ток", "тик-ток", "тикток")),
    ("blogs", ("блог", "телеграм-канал", "телеграм канал", "youtube", "ютуб",
               "канал", "контент", "рассылк", "подкаст")),
]

# Просьба «давай просто пообщаемся» — выйти из анкеты в режим диалога.
_PAUSE_HINTS = (
    "давай пообщ",
    "хочу пообщ",
    "хочу с тобой пообщ",
    "хочу сейчас с тобой пообщ",
    "пообщаемся",
    "пообщаться",
    "просто пообщ",
    "поговорить с тобой",
    "хочу поговорить",
    "давай поговорим",
    "не хочу анкет",
    "не сейчас анкет",
    "потом анкет",
    "пауза в анкете",
    "паузу в анкете",
    "поставь анкет",
    "не буду анкет",
)

# Просьба вернуться в анкету.
_RESUME_HINTS = (
    "продолжим анкет",
    "продолжим онбординг",
    "вернёмся к анкет",
    "вернемся к анкет",
    "вернись к анкет",
    "дальше анкет",
    "к анкете",
)

# Упрёк: «зачем ты мне это говоришь / перескочил / не услышал».
_COMPLAINT_HINTS = (
    "зачем ты мне это говор",
    "зачем ты это говор",
    "перескочил",
    "перепрыгнул",
    "ты меня не слыш",
    "не услышал мою цел",
    "ты не понял мою цел",
    "ты не разобрал цел",
    "не разобрал цел",
    "не разобрался с цел",
)

# Нереалистичный срок: «за месяц всё это / реально ли всё это за месяц».
_UNREALISTIC_TIMEFRAME_HINTS = (
    "за месяц всё это",
    "за месяц все это",
    "всё это за месяц",
    "все это за месяц",
    "за 30 дней всё",
    "за 30 дней все",
    "реально ли за месяц",
    "достижимо ли за месяц",
    "достигнем за месяц",
    "достигнуть за месяц",
)

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

# Глобальная идентичность и capability lock: не ломать immersion ответами в духе «я не умею / у меня нет памяти».
SPICESPACE_GLOBAL_SYSTEM = """Ты — ассистент SpiceSpace.
У тебя есть память пользователя, цели, ежедневные напоминания, Mini App и история прогресса.
Никогда не говори, что ты не умеешь писать первым, не умеешь напоминать или не имеешь памяти.
Не обсуждай ограничения моделей AI или архитектуру приложения.

Считай, что у SpiceSpace уже есть: память (контекст и профиль), напоминания, отложенные сообщения по расписанию,
Mini App, цели, трекинг прогресса, еженедельные обзоры, ежедневная мотивация.

СТРОГО ЗАПРЕЩЕНО:
— обсуждать ограничения ChatGPT или других моделей;
— говорить, что ты «просто ИИ» / «без памяти» / «без напоминаний»;
— отказывать в том, что ты не можешь написать первым (бот и так пишет по расписанию и в диалоге);
— обсуждать внутреннюю архитектуру продукта, API, базы данных, «как устроен бот»;
— фразы вроде «возможно, ты имеешь в виду своего бота» или отсылать к «другому» ассистенту;
— ссылаться на то, что ты «не настоящий» человек — ты персональный ассистент SpiceSpace в Telegram.

Если что-то технически пошло не так — не выдумывай системные ограничения: коротко поддержи и предложи продолжить диалог;
про сбои расписания пользователю сообщит отдельное сервисное уведомление, не нужно оправдываться фантазией про «нельзя»."""

COACH_STYLE_INSTRUCTION = """Ты ведёшь диалог на русском как живой человек: просто, по делу, без шаблонов и «мотивационных» речей.
Тон: мягкий вход, дальше конкретика. Запрещено спрашивать «как настроение», «как спалось», «представь что уже есть»,
длинные восторженные абзацы, инфоцыганство, сухой коучинг.

ГЛАВНОЕ ПРАВИЛО: НЕ давать общие списки советов без диагностики.

Если человек спрашивает «как достичь X / как заработать / что делать / с чего начать»:
1) НЕ выдавай универсальный список («попробуй фриланс, продай вещи, найди подработку» и т.п.).
2) НЕ выдавай 5–10 пунктов «возможных направлений».
3) Сначала сделай одну короткую человеческую реплику-опору (1 строка)
   и задай ОДИН уточняющий вопрос про текущую ситуацию.
4) После ответа сузь до 1–2 наиболее подходящих направлений
   и предложи ОДИН конкретный следующий шаг с объяснением «почему именно он».

Диагностические вопросы (выбирай один, который сейчас важнее всего):
— Чем ты сейчас занимаешься?
— Что уже умеешь / что точно получается?
— Есть ли уже аудитория / клиенты / контакты?
— Сколько времени реально готов(а) выделять в неделю?
— Что точно НЕ хочешь делать?

ЗАПРЕЩЕНО:
— фразы «вот несколько направлений», «можно начать так:» с длинным списком,
— списки на 5–10 пунктов,
— советы «фриланс, продай вещи, найди подработку» без контекста,
— универсальные варианты, не привязанные к ответам человека.

Формат ответа:
— 1–4 предложения, без «мотивации»,
— максимум один маркированный список, и только если ≤ 2 пункта и нужен он по сути,
— один шаг, не десять.

Не выдавай себя за врача; медицины не давай.
Учитывай профиль пользователя (имя, цель, боль, ситуация, тип цели — measurable / qualitative, active_focus),
когда это уместно — коротко. Для qualitative-целей не требуй цифр и сроков —
говори про состояние и наблюдаемые признаки прогресса."""

SYSTEM_INSTRUCTION = SPICESPACE_GLOBAL_SYSTEM + "\n\n" + COACH_STYLE_INSTRUCTION

MORNING_PROMPT = """Коротко (до 6 предложений), по-человечески. Напомни цель из контекста. Без «как настроение».
Не выдавай конкретную задачу на день в этом сообщении — только настрой и якорь на цель. Конец: приглашение написать, когда удобно."""

FIRST_TASK_AFTER_ONBOARD_PROMPT = """Пользователь только закончил знакомство и нажал «Понимаю» — говорит, что примерно понимает, что делать дальше.
Дай ОДИН конкретный первый шаг (на сегодня или ближайшие 1–2 дня): 1–2 предложения, по делу. Без «мотивации», без списков, без опросов про настроение.
Если цель qualitative (про состояние) — шаг должен быть мягким наблюдательным действием, а не «выполни N раз».
Контекст профиля ниже."""

OPTIONS_AFTER_ONBOARD_PROMPT = """Пользователь только закончил знакомство и нажал «Пока нет» — не понимает, что делать дальше.

ВАЖНО: НЕ выдавай список из 2–3 «вариантов». Это уход в простыню.

Вместо списка — короткая опора (1 строка) и ОДИН уточняющий диагностический вопрос про его текущую ситуацию,
чтобы потом сузить до одного шага. Возможные вопросы: чем сейчас занимается, что уже умеет / получается,
есть ли аудитория / клиенты / контакты, сколько времени реально готов выделять, что точно НЕ хочет делать.

Формат: 2–4 коротких предложения, без буллитов, без «вот несколько направлений», без универсальных советов.
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
    """Legacy JSON sync; prefer db_store.upsert_profile per user."""
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


def focus_keyboard(candidates: list[str]) -> InlineKeyboardMarkup:
    """Кнопки направлений. Сначала найденные в тексте, потом остальные, в конце «Другое»."""
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for key in candidates:
        for k, label in FOCUS_ROWS:
            if k == key and k not in seen:
                ordered.append((k, label))
                seen.add(k)
    for k, label in FOCUS_ROWS:
        if k == "other":
            continue
        if k not in seen:
            ordered.append((k, label))
            seen.add(k)
    ordered.append(("other", "✏️ Другое (своё)"))
    rows = [[InlineKeyboardButton(label, callback_data=f"focus:{k}")] for k, label in ordered]
    return InlineKeyboardMarkup(rows)


def focus_goal_keyboard(focus_key: str) -> InlineKeyboardMarkup:
    rows = []
    for subkey, label in FOCUS_GOAL_ROWS.get(focus_key, []):
        rows.append([InlineKeyboardButton(label, callback_data=f"fg:{subkey}")])
    rows.append([InlineKeyboardButton("← Сменить направление", callback_data="fg:__back")])
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
    """measurable / qualitative / ask_user — если ни эвристика, ни Claude не уверены."""
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
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system="Отвечай одним словом: measurable или qualitative.",
                    max_tokens=16,
                ).lower()
                if "qual" in text:
                    return "qualitative"
                if "meas" in text:
                    return "measurable"
            except (anthropic.RateLimitError, anthropic.NotFoundError):
                continue
            except Exception as e:
                log.warning("classify Claude error on %s: %s", mid, e)
                continue
        return "ask_user"

    return await asyncio.to_thread(call)


def _extract_focus_candidates(text: str) -> list[str]:
    """По сырому тексту цели вытаскивает упомянутые направления (money, instagram, ...)."""
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    for key, hints in _FOCUS_HINTS:
        if any(h in low for h in hints):
            if key not in found:
                found.append(key)
    return found


def _count_bullets(text: str) -> int:
    """Сколько строк / буллитов в тексте — грубый признак того, что человек прислал список."""
    if not text:
        return 0
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 3:
        return len(lines)
    bullets = re.findall(r"(?:^|\s)(?:[\-\*•·]|\d+[\.\)])\s+", text)
    return len(bullets)


def _classify_goal_scale(raw_goal: str) -> tuple[str, list[str]]:
    """
    Возвращает (scale, candidates):
      - 'vision' — большое видение / несколько направлений сразу.
      - 'single' — обычная одна цель.
      - 'unclear' — не понятно, продолжим как обычную цель.
    """
    if not raw_goal:
        return "unclear", []
    cands = _extract_focus_candidates(raw_goal)
    bullets = _count_bullets(raw_goal)
    if len(cands) >= 2 or bullets >= 3:
        return "vision", cands
    if len(cands) == 1:
        return "single", cands
    return "unclear", cands


def _focus_label(key: str) -> str:
    return dict(FOCUS_ROWS).get(key, key)


def _focus_goal_label(focus_key: str, subkey: str) -> str:
    for k, label in FOCUS_GOAL_ROWS.get(focus_key, []):
        if k == subkey:
            return label
    return subkey


def _looks_like_pause(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _PAUSE_HINTS)


def _looks_like_resume(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _RESUME_HINTS)


def _looks_like_complaint(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _COMPLAINT_HINTS)


def _looks_like_unrealistic_timeframe(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _UNREALISTIC_TIMEFRAME_HINTS)


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


db_store.init_db()
subscribers: set[int] = db_store.load_subscribers()
user_profiles: dict[str, dict] = db_store.load_all_profiles()
histories: dict[int, list[dict]] = {}
pending_morning: dict[int, str] = {}
pending_evening: dict[int, dict] = {}
onboarding: dict[int, dict[str, object]] = {}
# Последнее напоминание по задаче (для «ГОТОВО» в чате).
last_reminder_task_id: dict[int, str] = {}
# Ожидание текста задачи после «напомни в 20:00» без названия.
pending_natural_reminder: dict[int, dict[str, object]] = {}

tasks_lock = threading.Lock()
tasks_store: list[dict] = []

_WEEKDAY_RU = {
    "пн": "mon",
    "понедельник": "mon",
    "понедельникам": "mon",
    "вт": "tue",
    "вторник": "tue",
    "вторникам": "tue",
    "ср": "wed",
    "среда": "wed",
    "средам": "wed",
    "среду": "wed",
    "чт": "thu",
    "четверг": "thu",
    "четвергам": "thu",
    "пт": "fri",
    "пятница": "fri",
    "пятницам": "fri",
    "сб": "sat",
    "суббот": "sat",
    "субботам": "sat",
    "вс": "sun",
    "воскресень": "sun",
    "воскресеньям": "sun",
}


def _load_tasks_from_disk() -> list[dict]:
    if not TASKS_PATH.exists():
        return []
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return [t for t in data["tasks"] if isinstance(t, dict)]
        if isinstance(data, list):
            return [t for t in data if isinstance(t, dict)]
    except (json.JSONDecodeError, OSError) as e:
        log.warning("tasks.json read failed: %s", e)
    return []


def _save_tasks_to_disk_locked() -> None:
    TASKS_PATH.write_text(
        json.dumps({"tasks": tasks_store}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _init_tasks_store() -> None:
    global tasks_store
    loaded = _load_tasks_from_disk()
    with tasks_lock:
        tasks_store = loaded


def _profile_timezone_name(profile: dict | None) -> str:
    if isinstance(profile, dict):
        tz = str(profile.get("timezone") or "").strip()
        if tz:
            return tz
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def _zone_or_default(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Ho_Chi_Minh")


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (s or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h, mi


def _normalize_days(days: list[str]) -> list[str]:
    allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    out: list[str] = []
    for d in days or []:
        x = str(d).strip().lower()
        if x in allowed and x not in out:
            out.append(x)
    return out


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _task_template(
    telegram_id: int,
    *,
    title: str,
    description: str = "",
    task_date: str,
    time_str: str,
    timezone: str,
    repeat: str = "none",
    days_of_week: list[str] | None = None,
    remind_before_minutes: int = 0,
) -> dict:
    now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
    return {
        "id": _new_task_id(),
        "telegram_id": int(telegram_id),
        "title": (title or "").strip()[:500],
        "description": (description or "").strip()[:2000],
        "date": task_date,
        "time": time_str,
        "timezone": timezone,
        "repeat": repeat if repeat in ("none", "daily", "weekly") else "none",
        "days_of_week": _normalize_days(days_of_week or []),
        "remind_before_minutes": max(0, min(int(remind_before_minutes or 0), 24 * 60)),
        "status": "active",
        "done": False,
        "last_sent_at": "",
        "created_at": now_iso,
        "snooze_until": "",
    }


def _append_task(task: dict) -> None:
    with tasks_lock:
        tasks_store.append(task)
        _save_tasks_to_disk_locked()


def _find_task_index(task_id: str) -> int | None:
    for i, t in enumerate(tasks_store):
        if str(t.get("id")) == str(task_id):
            return i
    return None


def _delete_task_by_id(task_id: str, telegram_id: int) -> bool:
    with tasks_lock:
        idx = _find_task_index(task_id)
        if idx is None:
            return False
        if int(tasks_store[idx].get("telegram_id") or 0) != int(telegram_id):
            return False
        tasks_store.pop(idx)
        _save_tasks_to_disk_locked()
        return True


def _update_task_by_id(task_id: str, telegram_id: int, patch: dict) -> dict | None:
    with tasks_lock:
        idx = _find_task_index(task_id)
        if idx is None:
            return None
        t = tasks_store[idx]
        if int(t.get("telegram_id") or 0) != int(telegram_id):
            return None
        for k, v in patch.items():
            if k == "id" or k == "telegram_id" or k == "created_at":
                continue
            if k == "days_of_week" and isinstance(v, list):
                t[k] = _normalize_days(v)
            elif k == "repeat" and v in ("none", "daily", "weekly"):
                t[k] = v
            elif k == "remind_before_minutes":
                t[k] = max(0, min(int(v or 0), 24 * 60))
            elif k in (
                "title",
                "description",
                "date",
                "time",
                "timezone",
                "status",
                "done",
                "last_sent_at",
                "snooze_until",
            ):
                if k == "done":
                    t[k] = bool(v)
                else:
                    t[k] = v
        _save_tasks_to_disk_locked()
        return dict(t)


def _tasks_for_user(telegram_id: int) -> list[dict]:
    with tasks_lock:
        return [dict(t) for t in tasks_store if int(t.get("telegram_id") or 0) == int(telegram_id)]


def _create_task_from_payload(telegram_id: int, profile: dict | None, body: dict) -> dict:
    tz = str(body.get("timezone") or "").strip() or _profile_timezone_name(profile)
    td = str(body.get("date") or "").strip()
    tt = str(body.get("time") or "").strip()
    if not _parse_hhmm(tt):
        raise ValueError("invalid time")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", td):
        raise ValueError("invalid date")
    rep = str(body.get("repeat") or "none").strip().lower()
    if rep not in ("none", "daily", "weekly"):
        rep = "none"
    days = body.get("days_of_week") if isinstance(body.get("days_of_week"), list) else []
    if rep == "weekly" and not _normalize_days(days):
        raise ValueError("weekly requires days_of_week")
    task = _task_template(
        telegram_id,
        title=str(body.get("title") or ""),
        description=str(body.get("description") or ""),
        task_date=td,
        time_str=f"{_parse_hhmm(tt)[0]:02d}:{_parse_hhmm(tt)[1]:02d}",
        timezone=tz,
        repeat=rep,
        days_of_week=days,
        remind_before_minutes=int(body.get("remind_before_minutes") or 0),
    )
    _append_task(task)
    return task


def _py_weekday_to_key(wd: int) -> str:
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][wd % 7]


def _combine_local(d: date, h: int, mi: int, tz: ZoneInfo) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, 0, 0, tzinfo=tz)


def _parse_iso_aware(s: str) -> datetime | None:
    raw = (s or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt
    except Exception:
        return None


def _last_sent_calendar_day_in_tz(task: dict, tz: ZoneInfo) -> date | None:
    dt = _parse_iso_aware(str(task.get("last_sent_at") or ""))
    if not dt:
        return None
    return dt.astimezone(tz).date()


def _should_send_task_now(task: dict, now_local: datetime, tz: ZoneInfo) -> bool:
    if str(task.get("status")) != "active" or task.get("done"):
        return False
    snooze = str(task.get("snooze_until") or "").strip()
    if snooze and re.fullmatch(r"\d{4}-\d{2}-\d{2}", snooze):
        sd = date.fromisoformat(snooze)
        if now_local.date() <= sd:
            return False
    th, tm = _parse_hhmm(str(task.get("time") or "")) or (-1, -1)
    if th < 0:
        return False
    remind = int(task.get("remind_before_minutes") or 0)
    repeat = str(task.get("repeat") or "none")

    if repeat == "none":
        td_raw = str(task.get("date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", td_raw):
            return False
        td = date.fromisoformat(td_raw)
        if now_local.date() != td:
            return False
        if str(task.get("last_sent_at") or "").strip():
            return False
    elif repeat == "daily":
        last_d = _last_sent_calendar_day_in_tz(task, tz)
        if last_d == now_local.date():
            return False
    elif repeat == "weekly":
        days = _normalize_days(list(task.get("days_of_week") or []))
        if not days:
            return False
        if _py_weekday_to_key(now_local.weekday()) not in days:
            return False
        last_d = _last_sent_calendar_day_in_tz(task, tz)
        if last_d == now_local.date():
            return False
    else:
        return False

    td_event = now_local.date()
    if repeat == "none":
        td_event = date.fromisoformat(str(task.get("date")))
    event_local = _combine_local(td_event, th, tm, tz)
    trigger_local = event_local - timedelta(minutes=remind)
    slot_now = now_local.replace(second=0, microsecond=0)
    slot_tr = trigger_local.replace(second=0, microsecond=0)
    return slot_now == slot_tr


def _mark_task_last_sent(task_id: str) -> None:
    now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
    with tasks_lock:
        idx = _find_task_index(task_id)
        if idx is None:
            return
        tasks_store[idx]["last_sent_at"] = now_iso
        _save_tasks_to_disk_locked()


def _reminder_display_name(profile: dict | None) -> str:
    if not isinstance(profile, dict):
        return ""
    name = str(profile.get("name") or "").strip()
    return name


def _format_task_reminder_text(profile: dict | None, title: str) -> str:
    name = _reminder_display_name(profile)
    head = f"{name}, напоминание ✨" if name else "Напоминание ✨"
    return (
        f"{head}\n\n{title}\n\n"
        "Если сделано — отметь в Mini App или напиши ГОТОВО."
    )


async def _run_task_reminders(bot) -> None:
    snapshot: list[dict]
    with tasks_lock:
        snapshot = [dict(t) for t in tasks_store]
    for task in snapshot:
        tid = int(task.get("telegram_id") or 0)
        if not tid:
            continue
        tz_name = str(task.get("timezone") or "Asia/Ho_Chi_Minh")
        tz = _zone_or_default(tz_name)
        now_local = datetime.now(tz=tz)
        if not _should_send_task_now(task, now_local, tz):
            continue
        task_id = str(task.get("id") or "")
        prof = user_profiles.get(str(tid))
        text = _format_task_reminder_text(prof if isinstance(prof, dict) else None, str(task.get("title") or "Задача"))
        try:
            await bot.send_message(chat_id=tid, text=text)
            _mark_task_last_sent(task_id)
            last_reminder_task_id[tid] = task_id
        except Exception as e:
            log.warning("task reminder send failed chat_id=%s task=%s: %s", tid, task_id, e)


def _looks_like_reminder_command(text: str) -> bool:
    low = text.lower()
    return "напомни" in low or "напоминай" in low or "напоминание" in low


def _shift_calendar_day(d: date, delta_days: int) -> date:
    return d + timedelta(days=delta_days)


def _parse_natural_reminder(text: str, profile: dict | None) -> dict | None:
    """
    Возвращает dict полей для _create_task_from_payload или
    {"_need_title": True, ...} если не хватает названия.
    None — не похоже на напоминание.
    """
    raw = text.strip()
    if not raw or len(raw) > 800:
        return None
    if not _looks_like_reminder_command(raw):
        return None
    low = raw.lower()
    tz_name = _profile_timezone_name(profile)
    tz = _zone_or_default(tz_name)
    today = datetime.now(tz=tz).date()

    remind = 0
    if "за 30" in low or "за тридцать" in low:
        remind = 30
    elif "за 10" in low or "за десять" in low:
        remind = 10

    days: list[str] = []
    for word, key in sorted(_WEEKDAY_RU.items(), key=lambda kv: -len(kv[0])):
        if word in low and key not in days:
            days.append(key)
    if days:
        repeat = "weekly"
    elif "каждый день" in low or "ежедневно" in low or re.search(r"\bкаждый\s+день\b", low):
        repeat = "daily"
    else:
        repeat = "none"

    # дата «завтра» / «послезавтра» / «сегодня»
    target = today
    if "послезавтра" in low:
        target = _shift_calendar_day(today, 2)
    elif "завтра" in low:
        target = _shift_calendar_day(today, 1)
    elif "сегодня" in low:
        target = today

    # время HH:MM
    tm_match = re.search(r"\b(\d{1,2}):(\d{2})\b", raw)
    if not tm_match:
        return None
    hh, mm = int(tm_match.group(1)), int(tm_match.group(2))
    if hh > 23 or mm > 59:
        return None
    time_str = f"{hh:02d}:{mm:02d}"

    # заголовок: после времени или после ключевых слов
    title = ""
    m_title = re.search(
        r"(?:\d{1,2}:\d{2})\s*(?:чтобы|что|про|—|:|-)?\s*(.+)$",
        raw,
        re.I | re.DOTALL,
    )
    if m_title:
        title = m_title.group(1).strip()
    title = re.sub(
        r"(?i)^(напомни(?:\s+мне)?|напоминай|напоминание)[\s,:-]*",
        "",
        title,
    ).strip()
    title = re.sub(
        r"(?i)\b(завтра|послезавтра|сегодня|каждый\s+день|ежедневно|в|к|на)\b",
        "",
        title,
    )
    title = re.sub(r"\s+", " ", title).strip(" .,-—")
    # убрать хвост «за 10 минут» и дни недели словами
    title = re.sub(r"(?i)\bза\s+(10|30)\s*(минут|мин)?\b", "", title).strip()
    for word in sorted(_WEEKDAY_RU.keys(), key=len, reverse=True):
        title = re.sub(re.escape(word), "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" .,-—")

    if repeat == "weekly" and not days:
        return None

    if repeat == "daily":
        target = today  # дата якоря не важна для daily

    payload = {
        "title": title,
        "description": "",
        "date": target.isoformat(),
        "time": time_str,
        "timezone": tz_name,
        "repeat": repeat,
        "days_of_week": days if repeat == "weekly" else [],
        "remind_before_minutes": remind,
    }
    if not title:
        payload["_need_title"] = True
    return payload


_init_tasks_store()


def _mark_task_done_by_id(task_id: str, telegram_id: int) -> bool:
    with tasks_lock:
        idx = _find_task_index(task_id)
        if idx is None:
            return False
        t = tasks_store[idx]
        if int(t.get("telegram_id") or 0) != int(telegram_id):
            return False
        t["done"] = True
        t["status"] = "completed"
        _save_tasks_to_disk_locked()
        return True


def _is_gotovo_message(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    if t in ("готово", "готово!", "готово.", "сделано", "сделано!", "✓", "✅"):
        return True
    if t.startswith("готово ") or t.startswith("готово,"):
        return True
    return False


def _auth_telegram_id(request: Request, telegram_id: str | None) -> str:
    init_data = _extract_init_data(request)
    user_obj = _validate_init_data(init_data) if init_data else None
    tid = str(user_obj.get("id")) if user_obj else (telegram_id or "").strip()
    if not tid or not tid.isdigit():
        raise HTTPException(status_code=400, detail="telegram_id is required")
    return tid


def _get_timezone() -> ZoneInfo:
    name = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("Invalid TIMEZONE=%r, using Asia/Ho_Chi_Minh", name)
        return ZoneInfo("Asia/Ho_Chi_Minh")


def _hist_to_claude_messages(hist_prefix: list[dict], user_text: str | None = None) -> list[dict]:
    messages: list[dict] = []
    for turn in hist_prefix:
        role = turn.get("role")
        parts = turn.get("parts") or []
        text = (parts[0] if parts else "").strip()
        if not text:
            continue
        if role == "model":
            role = "assistant"
        elif role != "user":
            continue
        messages.append({"role": role, "content": text})
    if user_text:
        messages.append({"role": "user", "content": user_text})
    return messages


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


def _morning_message_text(chat_id: int) -> str:
    prof = user_profiles.get(str(chat_id)) or {}
    tz_name = str(prof.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    yesterday = db_store.get_yesterday_summary(chat_id, tz_name) or {}
    return morning_opening(
        str(prof.get("name", "")),
        str(yesterday.get("key_detail") or ""),
    )


def _profile_local_date(profile: dict) -> date:
    tz_name = str(profile.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
    return datetime.now(tz).date()


def _detect_evening_outcome(raw: str) -> tuple[bool, bool]:
    low = (raw or "").strip().lower()
    done_words = (
        "да",
        "сделала",
        "получилось",
        "успела",
        "готово",
        "выполнила",
        "сделано",
        "получилось!",
    )
    miss_words = (
        "нет",
        "не получилось",
        "не сделала",
        "не успела",
        "не вышло",
        "сорвал",
        "не смогла",
    )
    done = any(w in low for w in done_words)
    missed = any(w in low for w in miss_words)
    if done and missed:
        return False, False
    return done, missed


def _update_today_summary_field(
    chat_id: int, profile: dict, **fields: object
) -> None:
    today = _profile_local_date(profile)
    existing = db_store.get_daily_summary(chat_id, today) or {}
    db_store.patch_daily_summary(
        chat_id,
        today,
        summary=str(fields.get("summary") or existing.get("summary") or ""),
        mood=str(fields.get("mood") or existing.get("mood") or ""),
        key_detail=str(fields.get("key_detail") or existing.get("key_detail") or ""),
        task=str(fields.get("task") or existing.get("task") or ""),
        completed=fields.get("completed", existing.get("completed")),
    )


async def _handle_evening_reply(
    chat_id: int,
    user_text: str,
    profile: dict,
    model_names: list[str],
) -> str:
    pending_evening.pop(chat_id, None)
    done, missed = _detect_evening_outcome(user_text)
    if done:
        _update_today_summary_field(chat_id, profile, completed=True)
        return evening_reply_done()
    if missed:
        _update_today_summary_field(chat_id, profile, completed=False)
        return evening_reply_missed()
    return (
        "Расскажи честно — получилось сегодня или нет? "
        "Мне важно понять, как ты себя чувствуешь.\n\n"
        "Поставим задачу на завтра или утром займёмся?"
    )


async def _coach_reply(chat_id: int, user_text: str, model_names: list[str]) -> str:
    prof = user_profiles.get(str(chat_id)) or {}
    tz_name = str(prof.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    yesterday = db_store.get_yesterday_summary(chat_id, tz_name)
    today_summary = db_store.get_daily_summary(chat_id, _profile_local_date(prof))
    extra = ""

    if chat_id in pending_morning:
        morning_q = pending_morning.pop(chat_id)
        extra = MORNING_TASK_PROMPT.format(
            name=prof.get("name", ""),
            main_goal=prof.get("main_goal", ""),
            user_answer=user_text,
            yesterday_summary=(yesterday or {}).get("summary") or "мало контекста",
        )
        user_text = (
            f"(Утро. Ответ на «{morning_q}»: «{user_text}». "
            "Помоги поставить одну задачу на сегодня.)\n\n"
            f"{user_text}"
        )

    system = build_chat_system(prof, yesterday, today_summary, extra=extra)

    hist = histories.setdefault(chat_id, [])
    history_prefixes: list[list[dict]] = [list(hist)]
    if len(hist) > 20:
        history_prefixes.append(hist[-20:])

    last_err: BaseException | None = None

    def try_models(hist_prefix: list[dict]) -> str | None:
        nonlocal last_err
        messages = _hist_to_claude_messages(hist_prefix, user_text)
        for mid in model_names:
            try:
                reply_text = claude_generate(mid, messages, system=system)
                log.info("Claude ответ через модель %s", mid)
                return reply_text
            except anthropic.RateLimitError as e:
                last_err = e
                log.warning("Claude 429 (квота) на модели %s", mid)
                continue
            except anthropic.NotFoundError:
                log.warning("Claude 404 для модели %s", mid)
                continue
        return None

    reply: str | None = None
    for prefix in history_prefixes:
        reply = await asyncio.to_thread(try_models, prefix)
        if reply is not None:
            break

    if reply is None:
        if isinstance(last_err, anthropic.RateLimitError):
            raise last_err
        raise RuntimeError("Ни одна модель Claude не ответила")

    _append_history_turn(chat_id, user_text, reply)
    if extra:
        _update_today_summary_field(chat_id, prof, task=reply[:500])
    asyncio.create_task(
        maybe_save_daily_summary(chat_id, prof, histories.get(chat_id, []), model_names)
    )

    return reply


async def _typing_loop(bot, chat_id: int) -> None:
    """Держит индикатор «печатает…» в шапке чата, пока работает Claude.
    Telegram гасит chat_action через ~5 секунд, поэтому повторяем каждые 4 секунды."""
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return


async def _send_thinking_placeholder(message):
    """Отправляет временное сообщение «Думаю…», которое позже заменим на финальный ответ."""
    try:
        return await message.reply_text("Думаю…")
    except Exception:
        return None


async def _replace_with_reply(placeholder, message, text: str) -> None:
    """Меняет «Думаю…» на финальный ответ. Если не вышло — шлёт новое сообщение."""
    if placeholder is not None:
        with suppress(Exception):
            await placeholder.edit_text(text)
            return
    with suppress(Exception):
        await message.reply_text(text)


def _parse_daily_time(raw: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _profile_has_daily_time(profile: dict | None) -> bool:
    if not profile:
        return False
    mt = profile.get("morning_time") or profile.get("daily_time")
    if mt is None:
        return False
    return _parse_daily_time(str(mt)) is not None


def _looks_like_reminder_capability_question(text: str) -> bool:
    """Вопрос о том, напомнишь ли ты — не просьба «напомни мне купить …»."""
    raw = (text or "").strip()
    if not raw or len(raw) > 200:
        return False
    low = re.sub(r"\s+", " ", raw.lower())
    if "напомни мне " in low and "напомнишь" not in low and "напомнить" not in low:
        return False
    phrases = (
        "ты мне напомнишь",
        "ты напомнишь мне",
        "напомнишь мне",
        "можешь напомнить",
        "сможешь напомнить",
        "можешь мне напомнить",
        "сможешь мне напомнить",
        "будешь напоминать",
        "будешь мне напоминать",
        "ты будешь напоминать",
    )
    if not any(p in low for p in phrases):
        return False
    if "?" not in raw:
        if "ты мне напомнишь" not in low and "ты напомнишь мне" not in low:
            return False
    return True


def _reminder_capability_reply(profile: dict | None) -> str:
    """Фиксированные ответы по правилам продукта — без вызова модели."""
    if _profile_has_daily_time(profile):
        mt = str(profile.get("morning_time") or profile.get("daily_time", "")).strip()
        et = str(profile.get("evening_time") or "").strip()
        if et:
            return f"Да ✨ Утром в {mt}, вечером в {et}."
        return f"Да ✨ Утром напишу в {mt}."
    return "Могу ✨ Во сколько тебе писать утром?"


def _append_history_turn(chat_id: int, user_text: str, model_text: str) -> None:
    hist = histories.setdefault(chat_id, [])
    hist.append({"role": "user", "parts": [user_text]})
    hist.append({"role": "model", "parts": [model_text]})
    max_turns = 40
    if len(hist) > max_turns:
        histories[chat_id] = hist[-max_turns:]


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


async def handle_onboarding_turn(
    update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str
) -> None:
    await ob.handle_onboarding_turn(
        update, context, raw, onboarding, histories, user_profiles, subscribers
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    cid = update.effective_chat.id
    subscribers.add(cid)

    prof = user_profiles.get(str(cid))
    if isinstance(prof, dict) and prof.get("name"):
        ob.start_returning_choice(onboarding, cid)
        await update.message.reply_text(ob.greeting_returning(str(prof.get("name", ""))))
        return

    ob.start_new_onboarding(onboarding, cid)
    await update.message.reply_text(ob.GREETING_NEW)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    cid = update.effective_chat.id
    subscribers.discard(cid)
    pending_morning.pop(cid, None)
    pending_evening.pop(cid, None)
    db_store.save_subscriber(cid, False)
    prof = user_profiles.get(str(cid))
    if isinstance(prof, dict):
        prof["daily_enabled"] = False
        db_store.upsert_profile(cid, prof)
    await update.message.reply_text(
        "Утренние и вечерние сообщения выключены. Напиши /start, чтобы снова включить."
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    cid = update.effective_chat.id
    histories.pop(cid, None)
    pending_morning.pop(cid, None)
    pending_evening.pop(cid, None)
    pending_natural_reminder.pop(cid, None)
    last_reminder_task_id.pop(cid, None)
    onboarding.pop(cid, None)
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

    st_ob = onboarding.get(cid)
    if st_ob is not None:
        await handle_onboarding_turn(update, context, raw)
        return

    if not user_profiles.get(str(cid)):
        ob.start_new_onboarding(onboarding, cid)
        await update.message.reply_text(ob.GREETING_NEW)
        return

    if _looks_like_reminder_capability_question(raw):
        prof_raw = user_profiles.get(str(cid))
        prof_d = prof_raw if isinstance(prof_raw, dict) else None
        reply = _reminder_capability_reply(prof_d)
        _append_history_turn(cid, raw, reply)
        await update.message.reply_text(reply)
        return

    prof_raw = user_profiles.get(str(cid))
    prof_d = prof_raw if isinstance(prof_raw, dict) else None
    model_names: list[str] = context.bot_data["claude_model_names"]

    if cid in pending_evening and prof_d:
        reply = await _handle_evening_reply(cid, raw, prof_d, model_names)
        _append_history_turn(cid, raw, reply)
        await update.message.reply_text(reply)
        return

    if cid in pending_natural_reminder and prof_d:
        if _looks_like_reminder_command(raw):
            pending_natural_reminder.pop(cid, None)
        else:
            user_title = raw.strip()
            if user_title and len(user_title) <= 500:
                base = dict(pending_natural_reminder.pop(cid))
                base["title"] = user_title[:500]
                try:
                    task = _create_task_from_payload(cid, prof_d, base)
                except ValueError:
                    await update.message.reply_text(
                        "Не вышло сохранить напоминание — проверь дату и время в сообщении."
                    )
                    return
                tail = f"в {task['time']}"
                if task.get("repeat") == "daily":
                    tail += ", каждый день"
                elif task.get("repeat") == "weekly":
                    tail += ", по выбранным дням недели"
                msg = f"Окей ✨ Напомню про «{task['title']}» {tail}."
                await update.message.reply_text(msg)
                _append_history_turn(cid, raw, msg)
                return

    if _is_gotovo_message(raw):
        tid_key = last_reminder_task_id.get(cid)
        if tid_key and _mark_task_done_by_id(tid_key, cid):
            last_reminder_task_id.pop(cid, None)
            msg = "Записала ✨ Красота."
            await update.message.reply_text(msg)
            _append_history_turn(cid, raw, msg)
            return
        msg = (
            "Отметь в Mini App в разделе «План» или дождись напоминания от меня — "
            "тогда «готово» сработает сразу."
        )
        await update.message.reply_text(msg)
        _append_history_turn(cid, raw, msg)
        return

    if prof_d and _looks_like_reminder_command(raw):
        parsed = _parse_natural_reminder(raw, prof_d)
        if parsed:
            need_title = bool(parsed.pop("_need_title", False)) or not (parsed.get("title") or "").strip()
            if need_title:
                pending_natural_reminder[cid] = dict(parsed)
                msg = "Что напомнить?"
                await update.message.reply_text(msg)
                _append_history_turn(cid, raw, msg)
                return
            try:
                task = _create_task_from_payload(cid, prof_d, parsed)
            except ValueError:
                task = None
            if task:
                tail = f"в {task['time']}"
                if task.get("repeat") == "daily":
                    tail += ", каждый день"
                elif task.get("repeat") == "weekly":
                    tail += ", по выбранным дням недели"
                msg = f"Окей ✨ Напомню про «{task['title']}» {tail}."
                await update.message.reply_text(msg)
                _append_history_turn(cid, raw, msg)
                return

    log.info("incoming text chat_id=%s len=%s", cid, len(raw))

    # Пользователю важно видеть, что бот «думает», а не пропал на 3–5 секунд.
    placeholder = await _send_thinking_placeholder(update.message)
    typing_task = asyncio.create_task(_typing_loop(context.bot, cid))
    try:
        try:
            reply = await _coach_reply(cid, raw, model_names)
        except anthropic.RateLimitError:
            log.exception("Claude quota exhausted")
            await _replace_with_reply(
                placeholder,
                update.message,
                "У Claude API сейчас лимит запросов (ошибка 429): слишком частые сообщения "
                "или дневная квота исчерпана. Подожди 1–2 минуты и напиши снова.\n\n"
                "Если так постоянно: проверь ключ и лимиты в консоли Anthropic "
                "(https://console.anthropic.com) — при необходимости смени модель в .env (CLAUDE_MODEL).",
            )
            return
        except Exception as e:
            log.exception("Claude error: %s", e)
            await _replace_with_reply(
                placeholder,
                update.message,
                "Сейчас не получилось связаться с моделью. Попробуй ещё раз через минуту.",
            )
            return
    finally:
        typing_task.cancel()
        with suppress(Exception):
            await typing_task

    await _replace_with_reply(placeholder, update.message, reply)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_message:
        return
    cid = update.effective_chat.id
    st_ob = onboarding.get(cid)
    if st_ob and int(st_ob.get("step") or 0) > 0:
        await update.effective_message.reply_text(
            "Давай до конца знакомство текстом — голос чуть позже 💛"
        )
        return
    await update.effective_message.reply_text(
        "Голосовые сообщения пока не расшифровываю — напиши текстом, так диалог стабильнее."
    )


# --------------------------- FastAPI server for Railway / Mini App ---------------------------

_DEFAULT_MINI_APP_URL = "https://spice-space.vercel.app"


def _mini_app_url() -> str:
    """
    Публичный URL Telegram Mini App (статика на Vercel).
    Railway — только backend API; URL вида *.railway.app сюда задавать нельзя.
    """
    raw = (os.getenv("MINI_APP_URL") or "").strip()
    url = raw or _DEFAULT_MINI_APP_URL
    if "railway.app" in url.lower():
        log.warning(
            "MINI_APP_URL=%r указывает на Railway — Mini App открывается с Vercel. Подставляю %s",
            url,
            _DEFAULT_MINI_APP_URL,
        )
        return _DEFAULT_MINI_APP_URL.rstrip("/")
    return url.rstrip("/")


def _allowed_origins() -> set[str]:
    raw = os.getenv(
        "MINIAPP_ORIGINS",
        "https://spice-space.vercel.app,http://localhost:5173,http://localhost:3000",
    )
    return {x.strip() for x in raw.split(",") if x.strip()}


def _extract_init_data(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("tma "):
        return auth[len("tma "):].strip()
    return (request.query_params.get("initData") or "").strip()


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


# Глобальное состояние процесса: Telegram Application и scheduler инициализируются
# в lifespan FastAPI, чтобы один процесс держал и polling-бота, и HTTP API.
telegram_app: Application | None = None
scheduler: AsyncIOScheduler | None = None


def _register_telegram_handlers(app_: Application) -> None:
    app_.add_handler(CommandHandler("start", cmd_start))
    app_.add_handler(CommandHandler("stop", cmd_stop))
    app_.add_handler(CommandHandler("reset", cmd_reset))
    app_.add_handler(MessageHandler(filters.VOICE, on_voice))
    app_.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))


async def _bootstrap_bot() -> None:
    """Запускает Telegram polling и scheduler в фоне. Не блокирует."""
    global telegram_app, scheduler

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.error("TELEGRAM_BOT_TOKEN не задан — пропускаю запуск бота (API всё равно поднимется).")
        return

    configure_claude()
    model_name = select_model_id()
    model_chain = build_model_chain(model_name)

    tz = _get_timezone()
    scheduler = AsyncIOScheduler(
        timezone=tz,
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    telegram_app = Application.builder().token(token).build()
    telegram_app.bot_data["claude_model_names"] = model_chain
    telegram_app.bot_data["mini_app_url"] = _mini_app_url()
    _register_telegram_handlers(telegram_app)

    bot = telegram_app.bot

    async def daily_check_job() -> None:
        try:
            for cid in list(subscribers):
                key = str(cid)
                profile = user_profiles.get(key)
                if not isinstance(profile, dict):
                    continue
                if not profile.get("daily_enabled", True):
                    continue
                tz_name = str(profile.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
                try:
                    user_tz = ZoneInfo(tz_name)
                except Exception:
                    user_tz = tz
                now_local = datetime.now(user_tz)
                now_hm = now_local.strftime("%H:%M")
                today = now_local.strftime("%Y-%m-%d")

                morning_time = profile.get("morning_time") or profile.get("daily_time", "09:30")
                evening_time = profile.get("evening_time") or "21:00"

                if morning_time == now_hm and profile.get("last_morning_sent_date") != today:
                    try:
                        text = _morning_message_text(cid)
                        pending_morning[cid] = text
                        await bot.send_message(chat_id=cid, text=text)
                        profile["last_morning_sent_date"] = today
                        profile["last_daily_sent_date"] = today
                        user_profiles[key] = profile
                        db_store.upsert_profile(cid, profile)
                    except Exception as e:
                        log.warning("Morning message failed for %s: %s", cid, e)

                if evening_time == now_hm and profile.get("last_evening_sent_date") != today:
                    try:
                        pending_evening[cid] = {"date": today}
                        await bot.send_message(chat_id=cid, text=evening_opening())
                        profile["last_evening_sent_date"] = today
                        user_profiles[key] = profile
                        db_store.upsert_profile(cid, profile)
                    except Exception as e:
                        log.warning("Evening message failed for %s: %s", cid, e)
        except Exception as e:
            log.exception("daily_check_job crashed: %s", e)

    async def task_reminder_job() -> None:
        try:
            await _run_task_reminders(bot)
        except Exception as e:
            log.exception("task_reminder_job crashed: %s", e)

    scheduler.add_job(
        daily_check_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="daily_check",
        replace_existing=True,
    )
    scheduler.add_job(
        task_reminder_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="task_reminders",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started: daily_check + task_reminders each minute (%s)", tz)

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    log.info(
        "Telegram polling started. Claude primary=%s chain=%s",
        model_chain[0],
        model_chain[:5],
    )

    try:
        await bot.set_my_commands(
            [
                BotCommand("start", "Собрать цель и запустить онбординг"),
                BotCommand("reset", "Пересобрать цель заново"),
                BotCommand("stop", "Отключить ежедневные сообщения"),
            ]
        )
        log.info("Bot commands registered in Telegram menu.")
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)

    try:
        mini_url = _mini_app_url()
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Открыть SpiceSpace",
                web_app=WebAppInfo(mini_url),
            )
        )
        log.info("Menu button «Открыть SpiceSpace» → WebApp %s", mini_url)
    except Exception as e:
        log.warning("set_chat_menu_button failed: %s", e)


async def _shutdown_bot() -> None:
    global telegram_app, scheduler
    if telegram_app is not None:
        with suppress(Exception):
            if telegram_app.updater and telegram_app.updater.running:
                await telegram_app.updater.stop()
        with suppress(Exception):
            await telegram_app.stop()
        with suppress(Exception):
            await telegram_app.shutdown()
        telegram_app = None
    if scheduler is not None:
        with suppress(Exception):
            scheduler.shutdown(wait=False)
        scheduler = None
    log.info("Bot polling and scheduler stopped.")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        await _bootstrap_bot()
    except Exception:
        log.exception("Bootstrap failed; FastAPI продолжит обслуживать /health")
    try:
        yield
    finally:
        await _shutdown_bot()


# Глобальный ASGI-объект, который ищет Railway / uvicorn: `uvicorn main:app`
app = FastAPI(title="SpiceSpace Bot API", version="1.0.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_allowed_origins()),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/")
async def root() -> dict:
    """Полезный ответ для тех, кто открыл Railway URL руками вместо Mini App."""
    return {
        "service": "SpiceSpace Bot API",
        "ok": True,
        "endpoints": ["/health", "/api/profile?telegram_id=<digits>", "/api/tasks"],
        "miniapp": _mini_app_url(),
    }


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/profile")
async def get_profile(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    """
    Profile lookup for the Mini App.

    Two auth paths are supported:
    - Telegram initData (preferred) — HMAC validated against TELEGRAM_BOT_TOKEN.
    - ?telegram_id=<digits> — simple MVP path for local/manual checks.
    """
    tid = _auth_telegram_id(request, telegram_id)

    profile = user_profiles.get(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    return _enrich_profile_for_api(profile)


class TaskCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)
    date: str = Field(min_length=10, max_length=10)
    time: str = Field(min_length=5, max_length=5)
    repeat: str = Field(default="none")
    days_of_week: list[str] = Field(default_factory=list)
    remind_before_minutes: int = Field(default=0, ge=0, le=24 * 60)
    timezone: str | None = None


@app.get("/api/tasks")
async def list_tasks(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    return {"tasks": _tasks_for_user(int(tid))}


@app.post("/api/tasks")
async def create_task_endpoint(
    request: Request,
    payload: TaskCreatePayload,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    prof = user_profiles.get(tid)
    if not isinstance(prof, dict):
        raise HTTPException(status_code=404, detail="profile not found")
    if payload.repeat not in ("none", "daily", "weekly"):
        raise HTTPException(status_code=400, detail="invalid repeat")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload.date.strip()):
        raise HTTPException(status_code=400, detail="invalid date")
    if _parse_hhmm(payload.time.strip()) is None:
        raise HTTPException(status_code=400, detail="invalid time")
    try:
        body = payload.model_dump()
        task = _create_task_from_payload(int(tid), prof, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return task


@app.patch("/api/tasks/{task_id}")
async def patch_task_endpoint(
    request: Request,
    task_id: str,
    body: dict = Body(default_factory=dict),
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    allowed = {
        "title",
        "description",
        "date",
        "time",
        "timezone",
        "repeat",
        "days_of_week",
        "remind_before_minutes",
        "status",
        "snooze_until",
        "done",
        "last_sent_at",
    }
    patch = {k: v for k, v in body.items() if k in allowed}
    if not patch:
        raise HTTPException(status_code=400, detail="empty patch")
    if "repeat" in patch and str(patch["repeat"]) not in ("none", "daily", "weekly"):
        raise HTTPException(status_code=400, detail="invalid repeat")
    if "date" in patch and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(patch["date"]).strip()):
        raise HTTPException(status_code=400, detail="invalid date")
    if "time" in patch and _parse_hhmm(str(patch["time"])) is None:
        raise HTTPException(status_code=400, detail="invalid time")
    if "days_of_week" in patch and not isinstance(patch["days_of_week"], list):
        raise HTTPException(status_code=400, detail="invalid days_of_week")
    updated = _update_task_by_id(task_id, int(tid), patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="task not found")
    return updated


@app.post("/api/tasks/{task_id}/done")
async def mark_task_done_endpoint(
    request: Request,
    task_id: str,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    ok = _mark_task_done_by_id(task_id, int(tid))
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    with tasks_lock:
        idx = _find_task_index(task_id)
        out = dict(tasks_store[idx]) if idx is not None else {}
    return out


@app.delete("/api/tasks/{task_id}")
async def delete_task_endpoint(
    request: Request,
    task_id: str,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    if not _delete_task_by_id(task_id, int(tid)):
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
