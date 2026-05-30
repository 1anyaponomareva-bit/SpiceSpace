"""
SpiceSpace Telegram bot: companion с памятью, онбординг, утро/вечер daily loop,
daily_summaries в Supabase, Claude API с prompt caching, HTTP API для Mini App.

Secrets: TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY in .env

Optional .env:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  TIMEZONE=Asia/Ho_Chi_Minh  (дефолт для новых пользователей)
  CLAUDE_MODEL=claude-sonnet-4-5
  CLAUDE_FALLBACK_MODELS=claude-haiku-4-5
  PORT=8080
  MINIAPP_ORIGINS=...
  MINI_APP_URL=...
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import anthropic
import uvicorn

import db as db_store
import onboarding_flow as ob
from bot_typing import typing_while
from claude_client import build_model_chain, configure as configure_claude, generate as claude_generate
from claude_client import select_model_id
from prompts import (
    EVENING_MESSAGE_PROMPT,
    EVENING_NO_TASK_PROMPT,
    MORNING_MESSAGE_PROMPT,
    TODAY_TASK_PROMPT,
    build_chat_system,
    evening_opening,
    morning_opening,
    prepend_user_time,
    refresh_user_time_in_system,
    resolve_user_timezone,
    get_current_time_for_user,
    user_message_with_fresh_time,
)
from summaries import maybe_save_daily_summary
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DATA_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = DATA_DIR / "webapp"
load_dotenv(DATA_DIR / ".env")
ADMIN_TELEGRAM_ID = 8412438788

MILESTONE_DAYS = {
    3, 7, 10, 14, 17, 20, 24, 27, 30, 34, 37, 40, 44, 47, 50,
    54, 57, 60, 64, 67, 70, 74, 77, 80, 84,
}


def strip_profanity(text: str) -> str:
    patterns = [
        r"\bбля\b",
        r"\bблин\b",
        r"\bчёрт\b",
        r"\bчерт\b",
        r"\bфиг\b",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"  +", " ", text)
    return text.strip()


_CLOSING_PHRASE_PATTERNS = (
    r"\s*всё\s+на\s+сегодня\??",
    r"\s*ещё\s+что[- ]?то\??",
    r"\s*чем\s+ещё\s+могу\s+помочь\??",
    r"\s*есть\s+ещё\s+вопросы\??",
    r"\s*если\s+что\s*—?\s*пиши!?",
    r"\s*обращайся!?",
    r"\s*всегда\s+рада\s+помочь!?",
    r"\s*на\s+этом\s+всё\??",
    r"\s*нужно\s+ещё\s+что-?нибудь\??",
)


def strip_closing_phrases(text: str) -> str:
    for pat in _CLOSING_PHRASE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = re.sub(r"([.!?…])\s*([.!?…])+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def sanitize_bot_reply(text: str) -> str:
    return strip_closing_phrases(strip_profanity(strip_markdown(text)))


async def _bot_reply(message, text: str) -> None:
    await message.reply_text(sanitize_bot_reply(text))

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
OB_ASK_NAME = ob.OB_NAME
OB_GOAL_DIALOG = ob.OB_GOAL_DIALOG
OB_ASK_MORNING_TIME = ob.OB_MORNING_TIME
OB_ASK_EVENING_TIME = ob.OB_EVENING_TIME

CHANGE_GOAL_TRIGGERS = [
    "поменять цель",
    "изменить цель",
    "новая цель",
    "хочу другую цель",
    "смени цель",
    "давай поменяем цель",
    "хочу поменять цель",
    "поменяй цель",
    "начать заново",
    "новый цикл",
    "change goal",
    "new goal",
    "reset goal",
    "поменять недельную цель",
    "изменить цель на неделю",
    "другая задача на неделю",
]

CHANGE_WEEKLY_TRIGGERS = [
    "поменять цель на неделю",
    "изменить недельную цель",
    "другая цель на эту неделю",
    "поменяй недельную",
    "давай поменяем план на неделю",
]

ADD_12W_GOAL_MARKERS = [
    "добавить",
    "ещё одн",
    "еще одн",
    "вторая цель",
    "третья цель",
    "вторую цель",
    "третью цель",
    "параллельно",
    "ещё цель",
    "еще цель",
    "две цели",
    "несколько цел",
    "вместе с этой",
    "плюс ещё",
    "плюс еще",
]

REPLACE_12W_GOAL_MARKERS = [
    "поменять",
    "изменить",
    "заменить",
    "другая цель",
    "хочу другую",
    "смени цель",
    "сменить цель",
    "поменяй цель",
    "начать заново",
    "новый цикл",
    "change goal",
    "reset goal",
    "не та цель",
    "пересмотреть цель",
]


def _wants_to_add_second_12w_goal(text: str) -> bool:
    text_lower = (text or "").lower()
    return any(m in text_lower for m in ADD_12W_GOAL_MARKERS)


def _wants_to_change_12w_goal(text: str) -> bool:
    text_lower = (text or "").lower()
    if _wants_to_add_second_12w_goal(text_lower):
        return False
    if any(m in text_lower for m in REPLACE_12W_GOAL_MARKERS):
        return True
    return any(t in text_lower for t in CHANGE_GOAL_TRIGGERS)


def _wants_to_change_weekly_goal(text: str) -> bool:
    text_lower = (text or "").lower()
    return any(t in text_lower for t in CHANGE_WEEKLY_TRIGGERS)


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


def strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)  # **bold**
    text = re.sub(r"\*(.*?)\*", r"\1", text)  # *italic*
    text = re.sub(r"__(.*?)__", r"\1", text)  # __bold__
    text = re.sub(r"_(.*?)_", r"\1", text)  # _italic_
    text = re.sub(r"#{1,6}\s", "", text)  # # headers
    return text.strip()


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
про сбои расписания пользователю сообщит отдельное сервисное уведомление, не нужно оправдываться фантазией про «нельзя».

Если пользователь хочет изменить время утреннего или вечернего сообщения:
НЕ говори что поменяла время — ты не можешь менять данные напрямую.
Скажи: "Зайди в мини апп (кнопка внизу чата) → там видно время утреннего и вечернего сообщения, нажми ✏️ Изменить."""

COACH_STYLE_INSTRUCTION = """Ты ведёшь диалог на русском как живой человек: просто, по делу, без шаблонов и «мотивационных» речей.
Тон: мягкий вход, дальше конкретика. Запрещено спрашивать «как настроение», «как спалось», «представь что уже есть»,
длинные восторженные абзацы, инфоцыганство, сухой коучинг.

ГЛАВНОЕ ПРАВИЛО: НЕ давать общие списки советов без диагностики.

ВАЖНО — что Спейс ДЕЛАЕТ:
- Составляет план питания если просят
- Помогает с тренировками
- Пишет сценарии, тексты, контент
- Отвечает на конкретные практические вопросы

Спейс НЕ делает только одно: не принимает решения ЗА пользователя про его цели и жизнь.
Но конкретная помощь — всегда да.

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
— универсальные варианты, не привязанные к ответам человека,
— говорить «не буду», «не умею» на практические запросы,
— если просят меню — отказывать вместо того чтобы составить и предложить скорректировать,
— если просят сценарий — отказывать вместо того чтобы написать и спросить что поменять,
— сокращать имя без разрешения: если человек представился «Полина», называть «Полина», не «Поль».

Формат ответа:
— 1–4 предложения, без «мотивации»,
— максимум один маркированный список, и только если ≤ 2 пункта и нужен он по сути,
— один шаг, не десять.

Не выдавай себя за врача; медицины не давай.
Учитывай профиль пользователя (имя, цель, боль, ситуация, тип цели — measurable / qualitative, active_focus),
когда это уместно — коротко. Для qualitative-целей не требуй цифр и сроков —
говори про состояние и наблюдаемые признаки прогресса.

ЗАПРЕЩЕНО использовать markdown разметку: никаких **жирных**, никаких _курсивов_, никаких # заголовков, никаких - списков с дефисом.
Пиши plain text. Если нужно выделить — используй эмодзи."""

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


result = db_store.init_db()
if result:
    existing = db_store._request("GET", "user_profiles?select=user_id&limit=1")
    log.info("Supabase existing records: %s", existing)
    if not existing:
        log.info("Supabase empty — migrating from JSON...")
        json_path = Path(__file__).parent / "user_profiles.json"
        log.info("JSON path: %s exists: %s", json_path, json_path.exists())
        if json_path.exists():
            profiles = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(profiles, dict):
                log.info("Found %d profiles in JSON", len(profiles))
                for uid, profile in profiles.items():
                    if isinstance(profile, dict):
                        db_store.upsert_profile(uid, profile)
                        log.info(
                            "Migrated profile user_id=%s name=%s",
                            uid,
                            profile.get("name"),
                        )
        log.info("Migration complete")
    else:
        log.info("Supabase already has data — skipping migration")

subscribers: set[int] = db_store.load_subscribers()
user_profiles: dict[str, dict] = db_store.load_all_profiles()
histories: dict[int, list[dict]] = {}
pending_morning: dict[int, dict[str, object]] = {}  # morning pick / midday reminder state
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
        if tz and tz.lower() != "pending":
            return tz
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def _is_placeholder_timezone(tz: str | None) -> bool:
    name = (tz or "").strip()
    return not name or name.lower() == "pending" or name == "Asia/Ho_Chi_Minh"


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
            await bot.send_message(chat_id=tid, text=sanitize_bot_reply(text))
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
    # Временный bypass для разработки
    if os.getenv("SKIP_TMA_AUTH") == "true":
        tid = (telegram_id or "").strip()
        if tid.isdigit():
            return tid
        # Если telegram_id не передан — берём из initData без валидации
        init_data = _extract_init_data(request)
        if init_data:
            try:
                pairs = dict(parse_qsl(init_data, keep_blank_values=True))
                user_raw = pairs.get("user", "")
                if user_raw:
                    user_obj = json.loads(user_raw)
                    return str(user_obj["id"])
            except Exception:
                pass

    init_data = _extract_init_data(request)
    if init_data:
        user_obj = _validate_init_data(init_data)
        if not user_obj:
            raise HTTPException(status_code=401, detail="invalid init data")
        return str(user_obj["id"])
    tid = (telegram_id or "").strip()
    if tid.isdigit():
        return tid
    raise HTTPException(status_code=400, detail="telegram_id is required")


def _resolve_user_profile(tid: str) -> dict | None:
    """Профиль всегда из БД (Supabase/JSON), затем синхронизация в RAM-кэш."""
    profile = db_store.get_profile(tid)
    if isinstance(profile, dict):
        user_profiles[tid] = profile
        return profile
    return None


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


def _format_today_conversation_context(chat_id: int, max_turns: int = 10) -> str:
    hist = histories.get(chat_id) or []
    if not hist:
        return ""
    lines: list[str] = []
    for turn in hist[-max_turns:]:
        role = turn.get("role")
        parts = turn.get("parts") or []
        content = str(parts[0] if parts else "").strip()
        if not content:
            continue
        label = "Пользователь" if role == "user" else "Спейс"
        lines.append(f"{label}: {content[:200]}")
    return "\n".join(lines)


def _facts_block_for_prompt(chat_id: int) -> str:
    facts = db_store.load_user_facts(chat_id, limit=10)
    facts_text = "\n".join(f"— {f}" for f in facts) if facts else ""
    if facts_text:
        return f"Важные факты о пользователе:\n{facts_text}"
    return ""


def _personality_text_from_row(personality: dict | None) -> str:
    if not personality:
        return ""
    parts: list[str] = []
    if personality.get("communication_style"):
        parts.append(f"Стиль общения: {personality['communication_style']}")
    if personality.get("motivation_triggers"):
        parts.append(f"Что мотивирует: {personality['motivation_triggers']}")
    if personality.get("procrastination_patterns"):
        parts.append(f"Паттерны прокрастинации: {personality['procrastination_patterns']}")
    if personality.get("best_time_of_day"):
        parts.append(f"Лучшее время: {personality['best_time_of_day']}")
    if personality.get("response_to_pressure"):
        parts.append(f"Реакция на давление: {personality['response_to_pressure']}")
    if personality.get("personal_values"):
        parts.append(f"Ценности: {personality['personal_values']}")
    if personality.get("strengths"):
        parts.append(f"Сильные стороны: {personality['strengths']}")
    if personality.get("blockers"):
        parts.append(f"Блокеры: {personality['blockers']}")
    if personality.get("raw_insights"):
        parts.append(f"Наблюдения: {personality['raw_insights']}")
    return "\n".join(parts)


def _personality_block_for_prompt(chat_id: int) -> str:
    text = _personality_text_from_row(db_store.load_personality(chat_id))
    if text:
        return f"Профиль личности:\n{text}"
    return ""


async def _restore_history_from_db(cid: int, purpose: str) -> None:
    if cid in histories and histories[cid]:
        return
    try:
        loaded = await asyncio.to_thread(db_store.load_history, cid, 20)
        if loaded:
            histories[cid] = loaded
            log.info(
                "Restored history for %s cid=%s turns=%s",
                purpose,
                cid,
                len(loaded),
            )
    except Exception as e:
        log.warning("load_history for %s failed cid=%s: %s", purpose, cid, e)


def _exact_name_prompt_instruction(profile: dict, chat_id: int | None = None) -> str:
    name = str(profile.get("name", "")).strip()
    if not name:
        return "Имя в профиле не указано — не выдумывай и не сокращай имя."
    line = f"Имя пользователя: {name}. Используй только это имя, не сокращай."
    if chat_id is not None:
        ctx = _format_today_conversation_context(chat_id).lower()
        if any(
            p in ctx
            for p in (
                "не называй",
                "не зови",
                "не называй меня",
                "полным именем",
                "не сокращ",
            )
        ):
            line += " Пользователь просил не сокращать — строго соблюдай."
    return line


def _format_time_per_day_for_prompt(profile: dict) -> str:
    raw = str(profile.get("time_per_day") or "").strip()
    if not raw:
        return "не указано"
    if re.search(r"мин|час|hour|min", raw, re.I):
        return raw
    return f"{raw} минут"


async def _morning_message_text(
    chat_id: int,
    profile: dict,
    model_names: list[str],
) -> str:
    tz_name = str(profile.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    yesterday = db_store.get_yesterday_summary(chat_id, tz_name) or {}
    name = str(profile.get("name", "")).strip()
    display_name = name or "подруга"
    name_instruction = _exact_name_prompt_instruction(profile, chat_id)
    main_goal = str(
        profile.get("main_goal") or profile.get("final_goal") or ""
    ).strip() or "не указана"
    vision = str(profile.get("vision") or "").strip() or "не указана"
    weekly_goal = str(profile.get("weekly_goal") or "").strip() or main_goal
    last_summary = str(
        yesterday.get("summary") or yesterday.get("key_detail") or ""
    ).strip() or "нет"
    time_per_day = _format_time_per_day_for_prompt(profile)
    facts_block = _facts_block_for_prompt(chat_id)
    personality_block = _personality_block_for_prompt(chat_id)

    user_content = (
        f"{name_instruction}\n\n"
        + MORNING_MESSAGE_PROMPT.format(
            name=display_name,
            vision=vision,
            main_goal=main_goal,
            weekly_goal=weekly_goal,
            last_summary=last_summary,
            time_per_day=time_per_day,
            facts_block=facts_block,
            personality_block=personality_block,
        )
    )

    morning_body = (
        "Напиши только текст утреннего сообщения для Telegram. Без markdown.\n"
        f"{name_instruction}"
    )
    for block in (facts_block, personality_block):
        if block:
            morning_body += f"\n\n{block}"
    morning_system = prepend_user_time(profile, morning_body)

    def call() -> str:
        for mid in model_names:
            try:
                text = sanitize_bot_reply(
                    claude_generate(
                        mid,
                        [{"role": "user", "content": user_message_with_fresh_time(profile, user_content)}],
                        system=refresh_user_time_in_system(profile, morning_system),
                        max_tokens=360,
                        cache_core=False,
                    )
                ).strip()
                if text:
                    return text
            except Exception as e:
                log.warning("morning message %s: %s", mid, e)
        return morning_opening(
            display_name,
            weekly_goal=weekly_goal,
            main_goal=main_goal,
            vision=vision,
            key_detail=str(yesterday.get("key_detail") or ""),
        )

    return await asyncio.to_thread(call)


_EVENING_PERSONAL_SYSTEM = (
    "Напиши только текст вечернего сообщения для Telegram. Без markdown.\n"
    "Обращайся по имени из профиля полностью — не сокращай (не «Поля», если имя Полина).\n\n"
    "ЗАПРЕЩЕНО:\n"
    '- Говорить "сделала!", "выполнила!", "молодец!" когда пользователь только собирается что-то сделать\n'
    '- Путать намерение ("давай сделаем") с фактом ("сделала")\n'
    "- Материться или использовать слова: бля, блин, чёрт, фиг и подобные\n"
    "- Задавать один и тот же вопрос дважды подряд\n\n"
    'Если пользователь говорит "давай наметим задачу" — он ХОЧЕТ поставить задачу, а не выполнил её.\n'
    'Просто спроси: "Что конкретно сделаешь завтра?"'
)


def _today_has_task(chat_id: int, profile: dict) -> bool:
    today = _profile_local_date(profile)
    summ = db_store.get_daily_summary(chat_id, today) or {}
    task = str(summ.get("task") or "").strip()
    return bool(task) and len(task) > 5


async def _evening_message_text(
    chat_id: int,
    profile: dict,
    model_names: list[str],
) -> str:
    today = _profile_local_date(profile)
    today_summary = db_store.get_daily_summary(chat_id, today) or {}
    today_context = str(today_summary.get("summary") or "").strip()
    if not today_context:
        lines: list[str] = []
        for msg in (histories.get(chat_id, []) or [])[-10:]:
            role = "Пользователь" if msg.get("role") == "user" else "Спейс"
            content = msg.get("content") or (msg.get("parts") or [""])[0]
            text = str(content or "").strip()
            if text:
                lines.append(f"{role}: {text[:200]}")
        today_context = "\n".join(lines)
    summary_text = str(today_summary.get("summary") or "").strip()
    # Only use task from daily_summary, never from conversation
    task = str(today_summary.get("task") or "").strip()
    has_task = bool(task) and len(task) > 5
    log.info(
        "evening message cid=%s has_task=%s task=%s",
        chat_id,
        has_task,
        task[:50] if task else "",
    )

    if not summary_text and not today_context:
        return evening_opening(has_task=has_task)

    name = str(profile.get("name", "")).strip()
    display_name = name or "подруга"
    name_instruction = _exact_name_prompt_instruction(profile, chat_id)
    goal = str(profile.get("main_goal") or profile.get("final_goal") or "").strip()
    facts_block = _facts_block_for_prompt(chat_id)
    personality_block = _personality_block_for_prompt(chat_id)

    summary_lines: list[str] = []
    if summary_text:
        summary_lines.append(f"summary: {summary_text}")
    mood = str(today_summary.get("mood") or "").strip()
    key_detail = str(today_summary.get("key_detail") or "").strip()
    if mood:
        summary_lines.append(f"mood: {mood}")
    if key_detail:
        summary_lines.append(f"key_detail: {key_detail}")
    if has_task:
        summary_lines.append(f"task: {task}")
    summary_block = "\n".join(summary_lines) if summary_lines else "пока нет сводки"

    prompt_tpl = EVENING_MESSAGE_PROMPT if has_task else EVENING_NO_TASK_PROMPT
    user_content = (
        f"{name_instruction}\n\n"
        + prompt_tpl.format(
            name=display_name,
            goal=goal or "не указана",
            summary_block=summary_block,
            today_context="(см. блок «Сегодняшний диалог» в системном промпте)",
            today_task=task or "не задана",
            name_rule=name_instruction,
            facts_block=facts_block,
            personality_block=personality_block,
        )
    )
    evening_extra = "\n\n".join(
        b for b in (facts_block, personality_block) if b
    )
    evening_body = (
        f"{_EVENING_PERSONAL_SYSTEM}\n\n{name_instruction}"
        + (f"\n\n{evening_extra}" if evening_extra else "")
    )
    if today_context:
        evening_body += (
            f"\n\nСегодняшний диалог (время в репликах — прошлое, не текущее):\n"
            f"{today_context}\n"
            "Используй контекст дня — упомяни конкретную деталь. ЗАПРЕЩЕНО начинать с нуля."
        )
    evening_system = prepend_user_time(profile, evening_body)

    def call() -> str:
        for mid in model_names:
            try:
                text = sanitize_bot_reply(
                    claude_generate(
                        mid,
                        [{"role": "user", "content": user_message_with_fresh_time(profile, user_content)}],
                        system=refresh_user_time_in_system(profile, evening_system),
                        max_tokens=200,
                        cache_core=False,
                    ).strip()
                )
                if text:
                    return text
            except Exception as e:
                log.warning("evening personal message %s: %s", mid, e)
        return evening_opening(has_task=has_task)

    return await asyncio.to_thread(call)


async def _check_and_send_milestone(
    bot,
    cid: int,
    profile: dict,
    model_chain: list,
) -> None:
    try:
        tid = str(cid)
        summaries = db_store.list_daily_summaries(tid)
        active_days = len([s for s in summaries if s.get("date")])

        if active_days not in MILESTONE_DAYS:
            return

        shown_key = f"milestone_shown_{active_days}"
        if profile.get(shown_key):
            return

        name = str(profile.get("name") or "").strip()
        main_goal = str(profile.get("main_goal") or "").strip()
        vision = str(profile.get("vision") or "").strip()

        prompt = f"""Напиши короткое тёплое сообщение пользователю который {active_days} дней использует бота.

Имя: {name}
Цель на 12 недель: {main_goal}
Мечта: {vision}

Правила:
- 2-3 предложения
- Говори про её конкретную цель
- Тон: подруга которая рада что человек здесь
- Упомяни {active_days} дней
- НЕ важно выполняла ли она задачи — просто факт что она здесь {active_days} дней
- Без markdown, один эмодзи максимум"""

        def gen() -> str:
            for mid in model_chain:
                try:
                    text = claude_generate(
                        mid,
                        [{"role": "user", "content": prompt}],
                        system="Пиши тепло и лично.",
                        max_tokens=150,
                        cache_core=False,
                    ).strip()
                    if text:
                        return sanitize_bot_reply(text)
                except Exception as e:
                    log.warning("milestone telegram generate %s: %s", mid, e)
            display_name = name or "подруга"
            return f"{display_name}, {active_days} дней вместе — это уже что-то 💙"

        message = await asyncio.to_thread(gen)
        await bot.send_message(chat_id=cid, text=message)
        profile[shown_key] = True
        db_store.update_profile(cid, {shown_key: True})
        user_profiles[tid] = profile
        log.info("milestone sent cid=%s days=%s", cid, active_days)
    except Exception as e:
        log.warning("milestone check failed cid=%s: %s", cid, e)


def _profile_local_date(profile: dict) -> date:
    tz = _zone_or_default(_profile_timezone_name(profile))
    return datetime.now(tz).date()


def _wants_evening_task_planning(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if "давай" in low and any(w in low for w in ("задач", "намет", "постав", "завтра")):
        return True
    markers = (
        "давай наметим",
        "наметим задачу",
        "поставим задачу",
        "задачу на завтра",
        "утром займ",
        "займёмся",
        "займемся",
    )
    return any(m in low for m in markers)


def _detect_evening_outcome(raw: str) -> tuple[bool, bool]:
    low = (raw or "").strip().lower()
    if not low or _wants_evening_task_planning(raw):
        return False, False
    if re.fullmatch(r"да[\s!\.?]*", low):
        return True, False
    done_words = (
        "сделала",
        "получилось",
        "успела",
        "выполнила",
        "сделано",
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
    if re.search(r"\bготово\b", low) and "не готово" not in low and "не готова" not in low:
        done = True
    missed = any(w in low for w in miss_words)
    if done and missed:
        return False, False
    return done, missed


def _update_today_summary_field(
    chat_id: int, profile: dict, **fields: object
) -> None:
    today = _profile_local_date(profile)
    existing = db_store.get_daily_summary(chat_id, today) or {}
    patch: dict[str, object] = {
        "summary": str(fields.get("summary") or existing.get("summary") or ""),
        "mood": str(fields.get("mood") or existing.get("mood") or ""),
        "key_detail": str(fields.get("key_detail") or existing.get("key_detail") or ""),
    }
    if "task" in fields:
        patch["task"] = str(fields.get("task") or "")
    else:
        patch["task"] = str(existing.get("task") or "")
    if "completed" in fields:
        patch["completed"] = fields["completed"]
    if "task_completed" in fields:
        patch["task_completed"] = fields["task_completed"]
    db_store.patch_daily_summary(chat_id, today, **patch)


def _touch_streak_for_activity(chat_id: int, profile: dict) -> None:
    """Отметить активность пользователя сегодня (для streak), без last_daily_sent_date."""
    if not isinstance(profile, dict):
        return
    today = _profile_local_date(profile)
    before = str(profile.get("last_streak_date") or "").strip()
    _bump_streak_on_mark(profile, today)
    after = str(profile.get("last_streak_date") or "").strip()
    if before != after:
        db_store.update_profile(
            chat_id,
            {
                "streak": int(profile.get("streak") or 0),
                "last_streak_date": after,
            },
        )
        user_profiles[str(chat_id)] = profile


def _is_future_task(text: str) -> bool:
    """Returns True if user is talking about tomorrow, not today."""
    low = (text or "").strip().lower()
    future_markers = (
        "завтра",
        "завтра буду",
        "завтра сделаю",
        "завтра займусь",
        "завтра планирую",
        "на завтра",
        "следующей неделе",
        "потом",
        "позже",
        "на следующей",
    )
    return any(m in low for m in future_markers)


def _extract_future_task(text: str) -> str:
    """Extract tomorrow's task from user message."""
    low = (text or "").lower()
    markers = ("на завтра", "завтра надо", "завтра буду", "завтра сделаю")
    for marker in markers:
        if marker in low:
            idx = low.find(marker) + len(marker)
            task = text[idx:].strip().strip("—-:,").strip()
            if len(task) > 5:
                return task[:200]
    return ""


async def _try_save_task_from_message(
    chat_id: int,
    bot_reply: str,
    user_message: str,
    profile: dict,
) -> None:
    """Сохранить задачу дня, если бот подтвердил её в пост-онбординговом диалоге."""
    if _is_future_task(user_message):
        return
    bot_lower = (bot_reply or "").lower()
    indicators = (
        "записала",
        "отлично, конкретно",
        "зафиксировала",
        "вечером спрошу",
        "вечером проверю",
    )
    if not any(ind in bot_lower for ind in indicators):
        return
    task = (user_message or "").strip()
    if len(task) < 10 or len(task) > 200:
        return
    if _looks_like_greeting_or_chat(task):
        return
    save_daily_task(chat_id, profile, task, source="conversation")


def save_daily_task(
    chat_id: int,
    profile: dict,
    task: str,
    *,
    source: str = "conversation",
) -> None:
    """Persist today's task. Only morning_flow may overwrite an existing task."""
    weekly = str(profile.get("weekly_goal") or "").strip()
    cleaned = _sanitize_today_task(task, weekly_goal=weekly)
    if not cleaned:
        return
    today = _profile_local_date(profile)
    existing = db_store.get_daily_summary(chat_id, today) or {}
    if source != "morning_flow" and str(existing.get("task") or "").strip():
        return
    _update_today_summary_field(chat_id, profile, task=cleaned)


def _detect_evening_task_completed(text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    if any(
        w in low
        for w in (
            "частич",
            "немного",
            "половин",
            "чуть-чуть",
            "чуть ",
            "наполовину",
            "не всё",
            "не все",
        )
    ):
        return "partial"
    done, missed = _detect_evening_outcome(text)
    if done and not missed:
        return "true"
    explicit_no = bool(re.search(r"\bнет\b", low)) or "не получилось" in low
    if explicit_no and not done:
        return "false"
    return None


async def _handle_evening_reply(
    chat_id: int,
    user_text: str,
    profile: dict,
    model_names: list[str],
) -> str:
    state = pending_evening.setdefault(chat_id, {})

    if state.get("awaiting_tomorrow_task"):
        task = (user_text or "").strip()
        if len(task) >= 5:
            pending_evening.pop(chat_id, None)
            save_daily_task(chat_id, profile, task, source="evening_flow")
            return f"Записала ✨ На завтра: {task[:200]}"
        return "Что конкретно сделаешь завтра?"

    if _wants_evening_task_planning(user_text):
        state["awaiting_tomorrow_task"] = True
        return "Что конкретно сделаешь завтра?"

    outcome = _detect_evening_task_completed(user_text)

    if _is_future_task(user_text):
        future_task = _extract_future_task(user_text)
        if future_task:
            save_daily_task(chat_id, profile, future_task, source="evening_flow")

    if outcome:
        patch: dict[str, object] = {"task_completed": outcome}
        if outcome == "true":
            patch["completed"] = True
        elif outcome == "false":
            patch["completed"] = False
        _update_today_summary_field(chat_id, profile, **patch)
        log.info("evening task_completed saved cid=%s outcome=%s", chat_id, outcome)

        if outcome == "true":
            today = _profile_local_date(profile)
            _bump_streak_on_mark(profile, today)
            _week_scores_array(profile, str(chat_id))
            db_store.update_profile(
                chat_id,
                {
                    "streak": int(profile.get("streak") or 0),
                    "last_streak_date": str(profile.get("last_streak_date") or ""),
                    "weekly_score": int(profile.get("weekly_score") or 0),
                    "week_scores": profile.get("week_scores") or [0] * 12,
                },
            )
            user_profiles[str(chat_id)] = profile

    pending_evening.pop(chat_id, None)
    return await _coach_reply(
        chat_id, user_text, model_names, append_history=False
    )


async def _generate_weekly_summary_async(
    cid: int, profile: dict, model_names: list[str]
) -> None:
    try:
        from summaries import generate_weekly_summary

        await asyncio.to_thread(generate_weekly_summary, cid, profile, model_names)
    except Exception as e:
        log.warning("weekly_summary_async failed cid=%s: %s", cid, e)


async def _refresh_profile_timezone(chat_id: int, prof: dict) -> dict:
    if not isinstance(prof, dict):
        return prof
    if str(prof.get("timezone") or "").strip().lower() == "pending":
        prof["timezone"] = resolve_user_timezone(prof)
        await asyncio.to_thread(
            db_store.update_profile,
            chat_id,
            {"timezone": prof["timezone"]},
        )
    return prof


async def _coach_reply(
    chat_id: int,
    user_text: str,
    model_names: list[str],
    *,
    append_history: bool = True,
) -> str:
    tid = str(chat_id)
    prof = db_store.get_profile(chat_id) or user_profiles.get(tid) or {}
    if isinstance(prof, dict):
        prof = await _refresh_profile_timezone(chat_id, prof)
        user_profiles[tid] = prof
    tz_name = str(prof.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    yesterday = db_store.get_yesterday_summary(chat_id, tz_name)
    today_summary = db_store.get_daily_summary(chat_id, _profile_local_date(prof))
    facts = await asyncio.to_thread(db_store.load_user_facts, chat_id, 10)
    facts_text = "\n".join(f"— {f}" for f in facts) if facts else ""
    personality = await asyncio.to_thread(db_store.load_personality, chat_id)
    personality_text = _personality_text_from_row(personality)
    last_week = await asyncio.to_thread(db_store.load_last_weekly_summary, chat_id)
    weekly_context = ""
    if last_week:
        weekly_context = (
            f"Итог прошлой недели: {last_week.get('summary', '')}\n"
            f"Достижения: {last_week.get('achievements', '')}\n"
            f"Сложности: {last_week.get('challenges', '')}"
        )
    extra_parts: list[str] = []
    if facts_text:
        extra_parts.append(f"Важные факты:\n{facts_text}")
    if personality_text:
        extra_parts.append(f"Профиль личности:\n{personality_text}")
    if weekly_context:
        extra_parts.append(f"Прошлая неделя:\n{weekly_context}")
    extra = "\n\n".join(extra_parts)

    system = build_chat_system(prof, yesterday, today_summary, extra=extra)
    log.info(
        "coach_reply time cid=%s tz=%s now=%s",
        chat_id,
        resolve_user_timezone(prof),
        get_current_time_for_user(prof),
    )

    hist = histories.setdefault(chat_id, [])
    history_prefixes: list[list[dict]] = [list(hist)]
    if len(hist) > 20:
        history_prefixes.append(hist[-20:])

    last_err: BaseException | None = None

    def try_models(hist_prefix: list[dict]) -> str | None:
        nonlocal last_err
        messages = _hist_to_claude_messages(
            hist_prefix, user_message_with_fresh_time(prof, user_text or "")
        )
        for mid in model_names:
            try:
                fresh_system = refresh_user_time_in_system(prof, system)
                reply_text = sanitize_bot_reply(
                    claude_generate(mid, messages, system=fresh_system)
                )
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

    if append_history:
        _append_history_turn(chat_id, user_text, reply)
        picked_task = _maybe_save_task_from_user_reply(chat_id, prof, user_text)
        if picked_task and telegram_app:
            asyncio.create_task(
                _ask_midday_reminder(telegram_app.bot, chat_id, prof, picked_task)
            )
        asyncio.create_task(
            maybe_save_daily_summary(
                chat_id, prof, histories.get(chat_id, []), model_names
            )
        )

    return reply


_VISION_MODEL = "claude-sonnet-4-5"


async def _coach_reply_photo(
    chat_id: int,
    photo_b64: str,
    caption: str,
    model_names: list[str],
) -> str:
    tid = str(chat_id)
    prof = db_store.get_profile(chat_id) or user_profiles.get(tid) or {}
    if isinstance(prof, dict):
        prof = await _refresh_profile_timezone(chat_id, prof)
        user_profiles[tid] = prof
    tz_name = str(prof.get("timezone") or os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    yesterday = db_store.get_yesterday_summary(chat_id, tz_name)
    today_summary = db_store.get_daily_summary(chat_id, _profile_local_date(prof))
    system = build_chat_system(prof, yesterday, today_summary, extra="")

    user_label = user_message_with_fresh_time(
        prof, (caption or "Что на фото?").strip()
    )
    user_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": photo_b64,
            },
        },
        {"type": "text", "text": user_label},
    ]

    hist = histories.setdefault(chat_id, [])
    history_prefixes: list[list[dict]] = [list(hist)]
    if len(hist) > 20:
        history_prefixes.append(hist[-20:])

    last_err: BaseException | None = None

    def try_models(hist_prefix: list[dict]) -> str | None:
        nonlocal last_err
        messages = _hist_to_claude_messages(hist_prefix, None)
        messages.append({"role": "user", "content": user_content})
        try:
            reply_text = sanitize_bot_reply(
                claude_generate(
                    _VISION_MODEL,
                    messages,
                    system=refresh_user_time_in_system(prof, system),
                    cache_core=False,
                )
            )
            log.info("Claude vision ответ через модель %s", _VISION_MODEL)
            return reply_text
        except anthropic.RateLimitError as e:
            last_err = e
            log.warning("Claude 429 (квота) на модели %s", _VISION_MODEL)
            return None
        except anthropic.NotFoundError:
            log.warning("Claude 404 для модели %s", _VISION_MODEL)
            return None
        except Exception as e:
            log.warning("Claude vision %s: %s", _VISION_MODEL, e)
            return None

    reply: str | None = None
    for prefix in history_prefixes:
        reply = await asyncio.to_thread(try_models, prefix)
        if reply is not None:
            break

    if reply is None:
        if isinstance(last_err, anthropic.RateLimitError):
            raise last_err
        raise RuntimeError("Claude vision не ответила")

    _append_history_turn(chat_id, user_label, reply)
    asyncio.create_task(
        maybe_save_daily_summary(chat_id, prof, histories.get(chat_id, []), model_names)
    )
    return reply


def _parse_daily_time(raw: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _time_in_window(target_hm: str, now_hm: str, window_minutes: int = 5) -> bool:
    """Returns True if now_hm is within window_minutes after target_hm."""
    try:
        th, tm = map(int, target_hm.split(":"))
        nh, nm = map(int, now_hm.split(":"))
        target_total = th * 60 + tm
        now_total = nh * 60 + nm
        return 0 <= (now_total - target_total) < window_minutes
    except Exception:
        return False


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
    try:
        asyncio.get_running_loop().create_task(
            _save_history_turns_async(chat_id, user_text, model_text)
        )
    except RuntimeError:
        log.warning("No running event loop for history save chat_id=%s", chat_id)


async def _save_history_turns_async(chat_id: int, user_text: str, model_text: str) -> None:
    try:
        await asyncio.to_thread(db_store.save_history_turn, chat_id, "user", user_text)
        await asyncio.to_thread(db_store.save_history_turn, chat_id, "model", model_text)
    except Exception as e:
        log.warning("save_history_turns failed cid=%s: %s", chat_id, e)


async def _save_conversation_turn(
    cid: int, profile: dict, user_text: str, bot_reply: str
) -> None:
    """Save running turn log to daily_summaries for evening context."""
    try:
        today = _profile_local_date(profile)
        existing = db_store.get_daily_summary(cid, today)
        existing_log = str((existing or {}).get("summary") or "")
        new_entry = f"Пользователь: {user_text[:200]}\nСпейс: {bot_reply[:200]}"
        if existing_log:
            updated_log = f"{existing_log}\n---\n{new_entry}"
        else:
            updated_log = new_entry
        updated_log = updated_log[-2000:]
        db_store.patch_daily_summary(
            cid,
            today,
            summary=updated_log,
            mood=str((existing or {}).get("mood") or ""),
            key_detail=str((existing or {}).get("key_detail") or ""),
        )
    except Exception as e:
        log.warning("_save_conversation_turn failed cid=%s: %s", cid, e)


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

    # /start and /start webapp (Mini App deep link) — одинаковый сценарий
    tid = str(cid)
    prof = db_store.get_profile(cid) or user_profiles.get(tid)
    if isinstance(prof, dict):
        user_profiles[tid] = prof
    if isinstance(prof, dict) and prof.get("name"):
        ob.start_returning_choice(onboarding, cid)
        await _bot_reply(
            update.message, ob.greeting_returning(str(prof.get("name", "")))
        )
        return

    ob.start_new_onboarding(onboarding, cid)
    await _bot_reply(update.message, ob.GREETING_NEW)


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
    await _bot_reply(
        update.message,
        "Утренние и вечерние сообщения выключены. Напиши /start, чтобы снова включить.",
    )


async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    webapp_url = f"https://spicespace-production.up.railway.app/webapp/?telegram_id={cid}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Открыть SpiceSpace", web_app=WebAppInfo(url=webapp_url))
    ]])
    await update.message.reply_text("Твой прогресс 👇", reply_markup=keyboard)


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
    await _bot_reply(
        update.message,
        "Контекст диалога сброшен. Можем начать с чистого листа. Чтобы пройти знакомство снова — /start.",
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
        await _bot_reply(update.message, ob.GREETING_NEW)
        return

    prof_raw = user_profiles.get(str(cid))
    prof_d = prof_raw if isinstance(prof_raw, dict) else None
    if prof_d:
        _touch_streak_for_activity(cid, prof_d)

    morning_state = pending_morning.get(cid)
    if isinstance(morning_state, dict) and morning_state.get("awaiting_reminder"):
        raw_lower = raw.strip().lower()

        if any(w in raw_lower for w in ("нет", "не надо", "не нужно", "ненадо")):
            pending_morning.pop(cid, None)
            await _bot_reply(update.message, "Окей 💙")
            return

        parsed = _parse_daily_time(raw.strip())
        if parsed:
            pending_morning.pop(cid, None)
            task_title = str(morning_state.get("task") or "Задача на сегодня")
            prof_rem = prof_d or _resolve_user_profile(str(cid)) or {}
            tz_name = _profile_timezone_name(prof_rem)
            tz = _zone_or_default(tz_name)
            today = datetime.now(tz).strftime("%Y-%m-%d")
            try:
                _create_task_from_payload(
                    cid,
                    prof_rem,
                    {
                        "title": task_title[:200],
                        "date": today,
                        "time": parsed,
                        "timezone": tz_name,
                        "repeat": "none",
                    },
                )
                await _bot_reply(update.message, f"Напомню в {parsed} 💙")
            except Exception as e:
                log.warning("midday reminder create failed: %s", e)
                await _bot_reply(
                    update.message,
                    "Не вышло сохранить напоминание — попробуй ещё раз.",
                )
            return

        await _bot_reply(
            update.message,
            "Напиши время в формате ЧЧ:ММ — например, 14:00. Или «нет».",
        )
        return

    if _looks_like_reminder_capability_question(raw):
        prof_raw = user_profiles.get(str(cid))
        prof_d = prof_raw if isinstance(prof_raw, dict) else None
        reply = _reminder_capability_reply(prof_d)
        _append_history_turn(cid, raw, reply)
        await _bot_reply(update.message, reply)
        return

    model_names: list[str] = context.bot_data["claude_model_names"]

    if prof_d:
        if _wants_to_change_weekly_goal(raw):
            ob.start_change_weekly(onboarding, cid, prof_d)
            opening = ob.change_weekly_opening(prof_d)
            await _bot_reply(update.message, opening)
            _append_history_turn(cid, raw, opening)
            return
        if _wants_to_change_12w_goal(raw):
            ob.start_change_12w(onboarding, cid, prof_d)
            opening = ob.change_12w_choice_prompt()
            await _bot_reply(update.message, opening)
            _append_history_turn(cid, raw, opening)
            return

    if cid in pending_evening:
        pe_state = pending_evening.setdefault(cid, {})
        if pe_state.get("replied"):
            return
        pe_state["replied"] = True
        try:
            prof_evening = prof_d or _resolve_user_profile(str(cid)) or {}
            reply = await _handle_evening_reply(cid, raw, prof_evening, model_names)
            _append_history_turn(cid, raw, reply)
            await _bot_reply(update.message, reply)
        finally:
            pe_state.pop("replied", None)
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
                    await _bot_reply(
                        update.message,
                        "Не вышло сохранить напоминание — проверь дату и время в сообщении.",
                    )
                    return
                tail = f"в {task['time']}"
                if task.get("repeat") == "daily":
                    tail += ", каждый день"
                elif task.get("repeat") == "weekly":
                    tail += ", по выбранным дням недели"
                msg = f"Окей ✨ Напомню про «{task['title']}» {tail}."
                await _bot_reply(update.message, msg)
                _append_history_turn(cid, raw, msg)
                return

    if _is_gotovo_message(raw):
        tid_key = last_reminder_task_id.get(cid)
        if tid_key and _mark_task_done_by_id(tid_key, cid):
            last_reminder_task_id.pop(cid, None)
            msg = "Записала ✨ Красота."
            await _bot_reply(update.message, msg)
            _append_history_turn(cid, raw, msg)
            return
        msg = (
            "Отметь в Mini App в разделе «План» или дождись напоминания от меня — "
            "тогда «готово» сработает сразу."
        )
        await _bot_reply(update.message, msg)
        _append_history_turn(cid, raw, msg)
        return

    if prof_d and _looks_like_reminder_command(raw):
        parsed = _parse_natural_reminder(raw, prof_d)
        if parsed:
            need_title = bool(parsed.pop("_need_title", False)) or not (parsed.get("title") or "").strip()
            if need_title:
                pending_natural_reminder[cid] = dict(parsed)
                msg = "Что напомнить?"
                await _bot_reply(update.message, msg)
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
                await _bot_reply(update.message, msg)
                _append_history_turn(cid, raw, msg)
                return

    # Restore history from Supabase if not in memory.
    if cid not in histories or not histories[cid]:
        try:
            loaded = await asyncio.to_thread(db_store.load_history, cid, 20)
            if loaded:
                histories[cid] = loaded
                log.info("Restored %d history turns for cid=%s", len(loaded), cid)
        except Exception as e:
            log.warning("load_history failed cid=%s: %s", cid, e)

    log.info("incoming text chat_id=%s len=%s", cid, len(raw))

    try:
        async with typing_while(context.bot, cid):
            reply = await _coach_reply(cid, raw, model_names)
    except anthropic.RateLimitError:
        log.exception("Claude quota exhausted")
        await _bot_reply(
            update.message,
            "У Claude API сейчас лимит запросов (ошибка 429): слишком частые сообщения "
            "или дневная квота исчерпана. Подожди 1–2 минуты и напиши снова.\n\n"
            "Если так постоянно: проверь ключ и лимиты в консоли Anthropic "
            "(https://console.anthropic.com) — при необходимости смени модель в .env (CLAUDE_MODEL).",
        )
        return
    except Exception as e:
        log.exception("Claude error: %s", e)
        await _bot_reply(
            update.message,
            "Сейчас не получилось связаться с моделью. Попробуй ещё раз через минуту.",
        )
        return

    prof_for_turn = prof_d or _resolve_user_profile(str(cid)) or {}
    if isinstance(prof_for_turn, dict) and prof_for_turn:
        await _save_conversation_turn(cid, prof_for_turn, raw, reply)
    await _bot_reply(update.message, reply)
    if prof_d:
        await _try_save_task_from_message(cid, reply, raw, prof_d)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message or not update.message.photo:
        return
    cid = update.effective_chat.id
    msg = update.message

    st_ob = onboarding.get(cid)
    if st_ob is not None and int(st_ob.get("step") or 0) > 0:
        await _bot_reply(
            msg,
            "Давай до конца знакомство текстом — фото чуть позже 💛",
        )
        return

    if not user_profiles.get(str(cid)):
        ob.start_new_onboarding(onboarding, cid)
        await _bot_reply(msg, ob.GREETING_NEW)
        return

    prof_photo = user_profiles.get(str(cid))
    if isinstance(prof_photo, dict):
        _touch_streak_for_activity(cid, prof_photo)

    model_names: list[str] = context.bot_data["claude_model_names"]
    caption = (msg.caption or "").strip() or "Что на фото?"

    log.info("incoming photo chat_id=%s caption_len=%s", cid, len(caption))

    try:
        tg_file = await context.bot.get_file(msg.photo[-1].file_id)
        photo_bytes = await tg_file.download_as_bytearray()
        photo_b64 = base64.b64encode(photo_bytes).decode()
        async with typing_while(context.bot, cid):
            reply = await _coach_reply_photo(cid, photo_b64, caption, model_names)
    except anthropic.RateLimitError:
        log.exception("Claude quota exhausted (photo)")
        await _bot_reply(
            msg,
            "У Claude API сейчас лимит запросов (ошибка 429). Подожди 1–2 минуты и отправь фото снова.",
        )
        return
    except Exception:
        log.exception("Photo reply failed chat_id=%s", cid)
        await _bot_reply(
            msg,
            "Не получилось разобрать фото. Попробуй ещё раз или опиши текстом.",
        )
        return

    await _bot_reply(msg, reply)
    prof_reply = user_profiles.get(str(cid))
    if isinstance(prof_reply, dict):
        await _try_save_task_from_message(cid, reply, caption, prof_reply)


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_message:
        return
    cid = update.effective_chat.id
    st_ob = onboarding.get(cid)
    if st_ob and int(st_ob.get("step") or 0) > 0:
        await _bot_reply(
            update.effective_message,
            "Давай до конца знакомство текстом — голос чуть позже 💛",
        )
        return
    await _bot_reply(
        update.effective_message,
        "Голосовые сообщения пока не расшифровываю — напиши текстом, так диалог стабильнее.",
    )


# --------------------------- FastAPI server for Railway / Mini App ---------------------------

_DEFAULT_MINI_APP_URL = "https://spice-space.vercel.app"


def _public_base_url() -> str:
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain:
        return f"https://{domain}".rstrip("/")
    explicit = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    return ""


def _mini_app_url() -> str:
    """URL Telegram Mini App: /webapp на Railway или MINI_APP_URL (Vercel)."""
    if (WEBAPP_DIR / "index.html").is_file():
        base = _public_base_url()
        if base:
            return f"{base}/webapp"
    raw = (os.getenv("MINI_APP_URL") or "").strip()
    return (raw or _DEFAULT_MINI_APP_URL).rstrip("/")


def _allowed_origins() -> set[str]:
    raw = os.getenv(
        "MINIAPP_ORIGINS",
        "https://spice-space.vercel.app,http://localhost:5173,http://localhost:3000",
    )
    origins = {x.strip() for x in raw.split(",") if x.strip()}
    base = _public_base_url()
    if base:
        origins.add(base)
    return origins


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


def _short_task_title(text: str) -> str:
    line = (text or "").strip().split("\n")[0].strip()
    if len(line) > 140:
        return line[:137] + "…"
    return line


def _normalize_goal_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _task_equals_weekly_goal(task: str, weekly_goal: str) -> bool:
    a = _normalize_goal_text(task)
    b = _normalize_goal_text(weekly_goal)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 12 and len(b) >= 12 and (a in b or b in a):
        return True
    return False


def _extract_morning_task_options(text: str) -> list[str]:
    m = re.search(
        r"сегодня можно:\s*(.+?)(?:\.\s*что берёшь|\?\s*что берёшь|\.\s*$)",
        text or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return []
    blob = m.group(1).strip().rstrip(".")
    return [p.strip() for p in re.split(r"\s*/\s*", blob) if p.strip()][:3]


async def _generate_today_task(profile: dict, model_names: list[str]) -> str:
    weekly_goal = str(profile.get("weekly_goal") or "").strip() or "не указана"
    main_goal = str(
        profile.get("main_goal") or profile.get("final_goal") or ""
    ).strip() or "не указана"
    time_per_day = _format_time_per_day_for_prompt(profile)
    prompt = TODAY_TASK_PROMPT.format(
        weekly_goal=weekly_goal,
        main_goal=main_goal,
        time_per_day=time_per_day,
    )

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system=refresh_user_time_in_system(
                        profile,
                        prepend_user_time(profile, "Верни только задачу на сегодня."),
                    ),
                    max_tokens=120,
                    cache_core=False,
                ).strip()
                if text:
                    return text.strip().strip('"')[:140]
            except Exception as e:
                log.warning("generate_today_task %s: %s", mid, e)
        return ""

    return await asyncio.to_thread(call)


def _pick_weekly_tactic_from_reply(raw: str, options: str) -> str:
    text = (raw or "").strip()
    low = text.lower()
    parts = [p.strip() for p in re.split(r"\s*/\s*", options or "") if p.strip()]
    if not parts:
        return text[:2000]
    if re.search(r"\b1\b|перв|вариант\s*1", low) and len(parts) >= 1:
        return parts[0][:2000]
    if re.search(r"\b2\b|втор|вариант\s*2", low) and len(parts) >= 2:
        return parts[1][:2000]
    if re.search(r"\b3\b|трет|вариант\s*3", low) and len(parts) >= 3:
        return parts[2][:2000]
    for part in parts:
        if part.lower() in low or low in part.lower():
            return part[:2000]
    return text[:2000]


def _pick_morning_task_from_reply(raw: str, morning_text: str) -> str:
    options = _extract_morning_task_options(morning_text)
    if options:
        picked = _pick_weekly_tactic_from_reply(raw, " / ".join(options))
        if picked:
            return picked
    return raw.strip()[:2000]


def _save_today_task_choice(
    chat_id: int,
    profile: dict,
    task: str,
) -> None:
    save_daily_task(chat_id, profile, task, source="morning_flow")


def _pending_morning_text(state: object | None) -> str:
    if isinstance(state, dict):
        return str(state.get("text") or "")
    if isinstance(state, str):
        return state
    return ""


async def _ask_midday_reminder(bot, chat_id: int, profile: dict, task: str) -> None:
    """Ask user if they want a midday reminder for today's task."""
    await asyncio.sleep(1)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "Напомнить тебе про задачу днём? "
                "Напиши время — например, 14:00. Или «нет» если не нужно."
            ),
        )
    except Exception as e:
        log.warning("midday reminder ask failed cid=%s: %s", chat_id, e)
        return
    pending_morning[chat_id] = {"task": task, "awaiting_reminder": True}


def _maybe_save_task_from_user_reply(
    chat_id: int,
    profile: dict,
    user_text: str,
) -> str | None:
    weekly = str(profile.get("weekly_goal") or "").strip()
    raw = (user_text or "").strip()
    if _is_future_task(raw):
        return None
    if len(raw) < 3 or len(raw) > 200:
        return None
    if _looks_like_greeting_or_chat(raw):
        return None

    state = pending_morning.get(chat_id)
    if isinstance(state, dict) and state.get("awaiting_reminder"):
        return None

    morning_text = _pending_morning_text(state)
    if not morning_text:
        return None

    pending_morning.pop(chat_id, None)
    picked = _pick_morning_task_from_reply(raw, morning_text)

    picked = _sanitize_today_task(picked, weekly_goal=weekly)
    if not picked:
        return None

    today = _profile_local_date(profile)
    existing = db_store.get_daily_summary(chat_id, today) or {}
    current = str(existing.get("task") or "").strip()
    if current == picked and existing.get("task_completed") is None:
        return None
    _save_today_task_choice(chat_id, profile, picked)
    return picked


_GREETING_TASK_MARKERS = (
    "привет",
    "доброе утро",
    "добрый день",
    "добрый вечер",
    "давай начн",
    "давай начнем",
    "продуктивн",
    "начнём день",
    "начнем день",
    "рада тебя",
    "рад тебя",
    "как дела",
    "сколько времени",
)


def _looks_like_greeting_or_chat(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    if len(low) > 150:
        return True
    if low.count("?") >= 2:
        return True
    if "?" in low and len(low) < 30:
        return True
    if re.match(r"^привет[,\s!👋]", low):
        return True
    for prefix in ("мне кажется", "я думаю", "слушай", "кстати"):
        if low.startswith(prefix):
            return True
    return any(m in low for m in _GREETING_TASK_MARKERS)


def _extract_action_task(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    for pattern in (
        r"задача на сегодня:\s*(.+)",
        r"задача:\s*(.+)",
        r"сегодня:\s*(.+)",
        r"шаг на сегодня:\s*(.+)",
        r"план на сегодня:\s*(.+)",
    ):
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            line = m.group(1).split("\n")[0].strip()
            if line and not _looks_like_greeting_or_chat(line):
                return _short_task_title(line)
    parts = [p.strip() for p in re.split(r"[.!?]\s+", raw) if p.strip()]
    for cand in reversed(parts):
        if 8 <= len(cand) <= 120 and not _looks_like_greeting_or_chat(cand):
            return _short_task_title(cand)
    cleaned = _short_task_title(raw)
    if _looks_like_greeting_or_chat(cleaned):
        return ""
    return cleaned


def _sanitize_today_task(text: str, weekly_goal: str = "") -> str:
    cleaned = _extract_action_task(text)
    if not cleaned:
        return ""
    if weekly_goal and _task_equals_weekly_goal(cleaned, weekly_goal):
        return ""
    return cleaned


def _week_scores_array(profile: dict, telegram_id: str | None = None) -> list[int]:
    tid = str(telegram_id or profile.get("telegram_id") or profile.get("user_id") or "")
    if tid:
        summaries = db_store.list_daily_summaries(tid)
        completed_count = sum(
            1 for s in summaries if s.get("task_completed") == "true"
        )
        weekly_score = min(100, completed_count * 14)
        profile["weekly_score"] = weekly_score

    cw = max(1, min(12, int(profile.get("current_week") or 1)))
    ws = int(profile.get("weekly_score") or 0)
    out = [0] * 12
    out[cw - 1] = ws
    return out


def _display_streak(profile: dict, telegram_id: str | None) -> int:
    if not telegram_id:
        return int(profile.get("streak") or 0)

    # Count consecutive days from today backwards
    today = _profile_local_date(profile)
    summaries = db_store.list_daily_summaries(telegram_id)

    # Build set of dates where user was active (any interaction)
    active_dates = set()
    for s in summaries:
        d = str(s.get("date") or "")[:10]
        if d:
            active_dates.add(d)

    # Count consecutive days from today backwards
    streak = 0
    check = today
    for _ in range(84):
        if check.isoformat() in active_dates:
            streak += 1
            check = check - timedelta(days=1)
        else:
            break

    return max(streak, int(profile.get("streak") or 0))


def _bump_streak_on_mark(profile: dict, today: date) -> int:
    last = str(profile.get("last_streak_date") or "").strip()
    old = int(profile.get("streak") or 0)
    today_iso = today.isoformat()
    if last == today_iso:
        return old
    if last == (today - timedelta(days=1)).isoformat():
        new = old + 1 if old > 0 else 1
    else:
        new = 1
    profile["streak"] = new
    profile["last_streak_date"] = today_iso
    return new


def _enrich_profile_for_api(profile: dict, telegram_id: str | None = None) -> dict:
    """Старые профили без новых полей получают разумные дефолты при отдаче в Mini App."""
    p = dict(profile)
    if not p.get("main_goal"):
        p["main_goal"] = (
            p.get("final_goal") or p.get("raw_goal") or p.get("amount") or ""
        ).strip()
    if not p.get("raw_goal"):
        p["raw_goal"] = p.get("main_goal") or ""
    if not p.get("final_goal"):
        p["final_goal"] = p.get("main_goal") or p.get("raw_goal") or ""
    if not p.get("goal_type"):
        p["goal_type"] = "measurable" if p.get("amount") or _has_digit(str(p.get("raw_goal", ""))) else "qualitative"
    p.setdefault("goal_signals", [])
    p.setdefault("method", "")
    p.setdefault("streak", 0)
    p.setdefault("weekly_score", 0)
    p.setdefault("completed_tasks", [])
    p.setdefault("missed_tasks", [])
    p.setdefault("current_week", 1)
    p.setdefault("vision", p.get("vision") or "")
    mt = p.get("morning_time") or p.get("daily_time")
    if mt:
        p.setdefault("morning_time", mt)
        p.setdefault("daily_time", mt)
    p.setdefault("evening_time", p.get("evening_time") or "21:00")

    tid = telegram_id or str(p.get("telegram_id") or "")
    today = _profile_local_date(p)
    if tid:
        summ = db_store.get_daily_summary(tid, today)
        if isinstance(summ, dict):
            task_raw = str(summ.get("task") or "").strip()
            weekly = str(p.get("weekly_goal") or "").strip()
            task_clean = _sanitize_today_task(task_raw, weekly_goal=weekly)
            if task_clean:
                p["today_task"] = task_clean
            tc = db_store.normalize_task_completed(summ.get("task_completed"))
            p["task_completed"] = tc
            p["today_completed"] = tc == "true"

    p["week_scores"] = _week_scores_array(p, tid or None)
    p["display_streak"] = _display_streak(p, tid or None)
    return p


# Глобальное состояние процесса: Telegram Application и scheduler инициализируются
# в lifespan FastAPI, чтобы один процесс держал и polling-бота, и HTTP API.
telegram_app: Application | None = None
scheduler: AsyncIOScheduler | None = None


def _register_telegram_handlers(app_: Application) -> None:
    app_.add_handler(CommandHandler("start", cmd_start))
    app_.add_handler(CommandHandler("app", app_command))
    app_.add_handler(CommandHandler("stop", cmd_stop))
    app_.add_handler(CommandHandler("reset", cmd_reset))
    app_.add_handler(MessageHandler(filters.VOICE, on_voice))
    app_.add_handler(MessageHandler(filters.PHOTO, on_photo))
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

                # Always reload fresh profile from DB to pick up time changes
                fresh = db_store.get_profile(key)
                if isinstance(fresh, dict):
                    profile = fresh
                    user_profiles[key] = fresh

                if not profile.get("daily_enabled", True):
                    continue
                tz_name = _profile_timezone_name(profile)
                try:
                    user_tz = ZoneInfo(tz_name)
                except Exception:
                    user_tz = tz
                now_local = datetime.now(user_tz)
                now_hm = now_local.strftime("%H:%M")
                today = now_local.strftime("%Y-%m-%d")

                morning_time = profile.get("morning_time") or profile.get("daily_time", "09:30")
                evening_time = profile.get("evening_time") or "21:00"

                if _time_in_window(morning_time, now_hm) and profile.get("last_morning_sent_date") != today:
                    try:
                        # Save FIRST, then send — prevents duplicate if scheduler fires again
                        profile["last_morning_sent_date"] = today
                        profile["last_daily_sent_date"] = today
                        user_profiles[key] = profile
                        db_store.update_profile(
                            cid,
                            {
                                "last_morning_sent_date": today,
                                "last_daily_sent_date": today,
                            },
                        )
                        await _restore_history_from_db(cid, "morning message")
                        async with typing_while(bot, cid):
                            text = await _morning_message_text(
                                cid, profile, model_chain
                            )
                        histories.setdefault(cid, []).append(
                            {"role": "model", "parts": [text]}
                        )
                        webapp_url = os.getenv(
                            "MINI_APP_URL",
                            "https://spicespace-production.up.railway.app/webapp/",
                        )
                        keyboard = InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "Открыть SpiceSpace",
                                web_app=WebAppInfo(url=webapp_url),
                            )
                        ]])
                        await bot.send_message(
                            chat_id=cid,
                            text=sanitize_bot_reply(text),
                            reply_markup=keyboard,
                        )
                        pending_morning[cid] = {
                            "task": "",
                            "text": text,
                            "awaiting_reminder": False,
                        }
                        asyncio.create_task(
                            _check_and_send_milestone(bot, cid, profile, model_chain)
                        )
                    except Exception as e:
                        log.warning("Morning message failed for %s: %s", cid, e)

                if _time_in_window(evening_time, now_hm) and profile.get("last_evening_sent_date") != today:
                    try:
                        pending_evening[cid] = {"date": today}
                        await _restore_history_from_db(cid, "evening message")
                        async with typing_while(bot, cid):
                            evening_text = await _evening_message_text(
                                cid, profile, model_chain
                            )
                        webapp_url = os.getenv(
                            "MINI_APP_URL",
                            "https://spicespace-production.up.railway.app/webapp/",
                        )
                        keyboard = InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "Открыть SpiceSpace",
                                web_app=WebAppInfo(url=webapp_url),
                            )
                        ]])
                        await bot.send_message(
                            chat_id=cid,
                            text=sanitize_bot_reply(evening_text),
                            reply_markup=keyboard,
                        )
                        profile["last_evening_sent_date"] = today
                        user_profiles[key] = profile
                        db_store.upsert_profile(cid, profile)
                    except Exception as e:
                        log.warning("Evening message failed for %s: %s", cid, e)

                if now_local.weekday() == 6 and _time_in_window(
                    evening_time, now_hm, window_minutes=10
                ):
                    week_number = int(profile.get("current_week") or 1)
                    existing_weekly = db_store.load_weekly_summaries(cid, limit=1)
                    already_this_week = (
                        existing_weekly
                        and int(existing_weekly[0].get("week_number") or 0) == week_number
                    )
                    if not already_this_week:
                        await _generate_weekly_summary_async(cid, profile, model_chain)
                    weekly_summ = db_store.load_last_weekly_summary(cid)
                    if weekly_summ and profile.get("last_weekly_recap_date") != today:
                        recap = (
                            f"Неделя {weekly_summ.get('week_number')} позади 💙\n\n"
                            f"{weekly_summ.get('summary', '')}\n\n"
                            f"На следующую неделю: {weekly_summ.get('next_week_goal', '')}"
                        )
                        try:
                            await bot.send_message(
                                chat_id=cid,
                                text=sanitize_bot_reply(recap),
                            )
                            profile["last_weekly_recap_date"] = today
                            user_profiles[key] = profile
                            db_store.update_profile(
                                cid, {"last_weekly_recap_date": today}
                            )
                        except Exception as e:
                            log.warning("Weekly recap failed for %s: %s", cid, e)
        except Exception as e:
            log.exception("daily_check_job crashed: %s", e)

    async def task_reminder_job() -> None:
        try:
            await _run_task_reminders(bot)
        except Exception as e:
            log.exception("task_reminder_job crashed: %s", e)

    async def onboarding_reminder_job() -> None:
        """One reminder if onboarding stalled 2+ hours after user shared their name."""
        now = datetime.now(tz)
        for cid, st in list(onboarding.items()):
            if not isinstance(st, dict):
                continue
            if st.get("reminder_sent"):
                continue
            step = st.get("step")
            if step in (ob.OB_DONE, None):
                continue
            if not st.get("name"):
                continue
            last_activity = st.get("last_activity_at")
            if not last_activity:
                continue
            if isinstance(last_activity, str):
                try:
                    last_activity = datetime.fromisoformat(last_activity)
                except ValueError:
                    continue
            if last_activity.tzinfo is None:
                minutes_idle = (now.replace(tzinfo=None) - last_activity).total_seconds() / 60
            else:
                minutes_idle = (now - last_activity).total_seconds() / 60
            if minutes_idle < 120:
                continue
            try:
                name = str(st.get("name") or "").strip()
                greeting = f"{name}, ты там?" if name else "Ты там?"
                await bot.send_message(
                    chat_id=cid,
                    text=f"{greeting} Осталось буквально пара вопросов 💙",
                )
                st["reminder_sent"] = True
            except Exception as e:
                log.warning("onboarding_reminder failed cid=%s: %s", cid, e)

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
    scheduler.add_job(
        onboarding_reminder_job,
        IntervalTrigger(minutes=15, timezone=tz),
        id="onboarding_reminder_job",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "Scheduler started: daily_check + task_reminders (1m), onboarding_reminder (15m) (%s)",
        tz,
    )

    await telegram_app.initialize()
    await telegram_app.start()
    await bot.delete_webhook(drop_pending_updates=False)
    await asyncio.sleep(3)
    await telegram_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    log.info(
        "Telegram polling started. Claude primary=%s chain=%s",
        model_chain[0],
        model_chain[:5],
    )

    try:
        await bot.set_my_commands([])
        log.info("Bot commands menu cleared.")
    except Exception as e:
        log.warning("set_my_commands failed: %s", e)


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

if (WEBAPP_DIR / "index.html").is_file():
    app.mount(
        "/webapp",
        StaticFiles(directory=str(WEBAPP_DIR), html=True),
        name="webapp",
    )
    log.info("Mini App static files mounted at /webapp")


@app.get("/")
async def root() -> dict:
    """Полезный ответ для тех, кто открыл Railway URL руками вместо Mini App."""
    return {
        "service": "SpiceSpace Bot API",
        "ok": True,
        "endpoints": [
            "/health",
            "/api/profile?telegram_id=<digits>",
            "/api/calendar",
            "/api/milestone",
            "/api/tasks",
        ],
        "miniapp": _mini_app_url(),
        "webapp_static": (WEBAPP_DIR / "index.html").is_file(),
    }


@app.get("/health")
async def health() -> dict[str, str | bool]:
    from onboarding_flow import BOT_BUILD

    return {"ok": True, "build": BOT_BUILD}


@app.get("/api/admin/stats")
async def admin_stats(request: Request) -> dict:
    tid = request.query_params.get("admin_id", "")
    if str(tid) != str(ADMIN_TELEGRAM_ID):
        raise HTTPException(status_code=403, detail="forbidden")

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    day3_ago = (now - timedelta(days=3)).strftime("%Y-%m-%d")

    profiles = db_store.load_all_profiles()
    message_counts: dict[str, int] = {}
    total_message_counts: dict[str, int] = {}
    replied_today: set[str] = set()
    if getattr(db_store, "_use_supabase", False):
        rows = db_store._request(
            "GET",
            f"conversation_history?role=eq.user&created_at=gte.{today}T00:00:00&select=user_id",
        ) or []
        for r in rows:
            if isinstance(r, dict):
                uid = str(r.get("user_id"))
                if not uid:
                    continue
                replied_today.add(uid)
                message_counts[uid] = message_counts.get(uid, 0) + 1
        all_rows = db_store._request(
            "GET",
            "conversation_history?role=eq.user&select=user_id",
        ) or []
        for r in all_rows:
            if isinstance(r, dict):
                uid = str(r.get("user_id"))
                if not uid:
                    continue
                total_message_counts[uid] = total_message_counts.get(uid, 0) + 1
    users: list[dict] = []
    for uid, prof in profiles.items():
        if not isinstance(prof, dict):
            continue
        uid_str = str(uid)
        last_morning = str(prof.get("last_morning_sent_date") or "")
        last_evening = str(prof.get("last_evening_sent_date") or "")
        last_active = max(last_morning, last_evening) if (last_morning or last_evening) else ""
        users.append(
            {
                "user_id": uid_str,
                "name": prof.get("name") or "—",
                "main_goal": str(prof.get("main_goal") or "")[:60],
                "streak": int(prof.get("streak") or 0),
                "current_week": int(prof.get("current_week") or 1),
                "last_active": last_active,
                "timezone": prof.get("timezone") or "—",
                "has_goal": bool(prof.get("main_goal")),
                "onboarding_done": bool(prof.get("name") and prof.get("main_goal")),
                "active_today": last_active == today,
                "replied_today": uid_str in replied_today,
                "messages_today": message_counts.get(uid_str, 0),
                "messages_total": total_message_counts.get(uid_str, 0),
                "active_week": last_active >= week_ago if last_active else False,
                "churned": last_active < day3_ago if last_active else True,
            }
        )

    users.sort(key=lambda x: x.get("last_active") or "", reverse=True)
    total = len(users)
    onboarded = sum(1 for u in users if u.get("onboarding_done"))
    active_today = sum(1 for u in users if u.get("active_today"))
    replied_today_count = sum(1 for u in users if u.get("replied_today"))
    active_week = sum(1 for u in users if u.get("active_week"))
    churned = sum(1 for u in users if u.get("churned") and u.get("onboarding_done"))
    streak_3plus = sum(1 for u in users if int(u.get("streak") or 0) >= 3)

    return {
        "stats": {
            "total": total,
            "onboarded": onboarded,
            "active_today": active_today,
            "replied_today": replied_today_count,
            "active_week": active_week,
            "churned": churned,
            "streak_3plus": streak_3plus,
        },
        "users": users,
    }


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

    profile = db_store.get_profile(tid)
    if not isinstance(profile, dict):
        log.info("api/profile 404 user_id=%s (нет профиля в БД)", tid)
        raise HTTPException(status_code=404, detail="profile not found")
    user_profiles[tid] = profile

    user_obj = _validate_init_data(_extract_init_data(request))
    return {
        "profile": _enrich_profile_for_api(profile, tid),
        "user": user_obj if isinstance(user_obj, dict) else None,
    }


def _purge_user_runtime(chat_id: int) -> None:
    """Очистка RAM-состояния пользователя после сброса профиля."""
    tid = str(chat_id)
    histories.pop(chat_id, None)
    db_store.delete_user_facts(chat_id)
    db_store.delete_personality(chat_id)
    db_store.delete_history(chat_id)
    onboarding.pop(chat_id, None)
    pending_morning.pop(chat_id, None)
    pending_evening.pop(chat_id, None)
    pending_natural_reminder.pop(chat_id, None)
    last_reminder_task_id.pop(chat_id, None)
    subscribers.discard(chat_id)
    user_profiles.pop(tid, None)
    with tasks_lock:
        before = len(tasks_store)
        tasks_store[:] = [
            t for t in tasks_store if int(t.get("telegram_id") or 0) != chat_id
        ]
        if len(tasks_store) != before:
            _save_tasks_to_disk_locked()


@app.post("/api/profile/reset")
async def reset_profile_endpoint(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    db_store.delete_profile(tid)
    _purge_user_runtime(int(tid))
    return {"ok": True}


@app.post("/api/profile/stop")
async def stop_profile_endpoint(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    cid = int(tid)
    subscribers.discard(cid)
    pending_morning.pop(cid, None)
    pending_evening.pop(cid, None)
    db_store.save_subscriber(cid, False)
    prof = user_profiles.get(tid) or db_store.get_profile(tid)
    if isinstance(prof, dict):
        prof["daily_enabled"] = False
        db_store.upsert_profile(cid, prof)
        user_profiles[tid] = prof
    return {"ok": True}


class TimezonePayload(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)


class ProfilePatchPayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)


class TimesPatchPayload(BaseModel):
    morning_time: str | None = None
    evening_time: str | None = None


@app.patch("/api/profile")
async def patch_profile_endpoint(
    request: Request,
    payload: ProfilePatchPayload,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    profile = _resolve_user_profile(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    if payload.name is None:
        raise HTTPException(status_code=400, detail="name is required")

    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="name is required")
    profile["name"] = new_name[:50]

    db_store.upsert_profile(int(tid), profile)
    user_profiles[tid] = profile
    return {"ok": True, "profile": _enrich_profile_for_api(profile, tid)}


@app.patch("/api/profile/times")
async def patch_profile_times_endpoint(
    request: Request,
    payload: TimesPatchPayload,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    profile = _resolve_user_profile(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    if payload.morning_time is None and payload.evening_time is None:
        raise HTTPException(status_code=400, detail="at least one time is required")

    if payload.morning_time is not None:
        parsed = _parse_daily_time(payload.morning_time.strip())
        if not parsed:
            raise HTTPException(status_code=400, detail="invalid morning_time")
        profile["morning_time"] = parsed
        profile["daily_time"] = parsed

    if payload.evening_time is not None:
        parsed = _parse_daily_time(payload.evening_time.strip())
        if not parsed:
            raise HTTPException(status_code=400, detail="invalid evening_time")
        profile["evening_time"] = parsed

    db_store.upsert_profile(int(tid), profile)
    user_profiles[tid] = profile
    return {"ok": True, "profile": _enrich_profile_for_api(profile, tid)}


@app.patch("/api/profile/timezone")
async def patch_profile_timezone_endpoint(
    request: Request,
    payload: TimezonePayload,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    profile = _resolve_user_profile(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    new_tz = payload.timezone.strip()
    if not new_tz:
        raise HTTPException(status_code=400, detail="timezone is required")
    try:
        ZoneInfo(new_tz)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid timezone") from exc

    profile["timezone"] = new_tz
    db_store.upsert_profile(int(tid), profile)
    user_profiles[tid] = profile
    return {"ok": True, "profile": _enrich_profile_for_api(profile, tid)}


def _profile_cycle_start(profile: dict, user_id: str | int) -> date:
    raw = str(profile.get("cycle_start_date") or "").strip()[:10]
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    rows = db_store.list_daily_summaries(user_id)
    if rows:
        try:
            return date.fromisoformat(rows[0]["date"])
        except ValueError:
            pass
    return _profile_local_date(profile)


@app.get("/api/milestone")
async def get_milestone(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    profile = _resolve_user_profile(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    summaries = db_store.list_daily_summaries(tid)
    completed_days = sum(
        1 for s in summaries if s.get("task_completed") == "true"
    )

    if completed_days not in MILESTONE_DAYS:
        return {"milestone": None}

    shown_key = f"milestone_shown_{completed_days}"
    if profile.get(shown_key):
        return {"milestone": None}

    name = str(profile.get("name") or "").strip()
    main_goal = str(profile.get("main_goal") or "").strip()
    vision = str(profile.get("vision") or "").strip()

    prompt = f"""Напиши короткое персональное поздравление для пользователя.

Имя: {name}
Цель на 12 недель: {main_goal}
Мечта: {vision}
Выполнено дней подряд: {completed_days}

Правила:
- 2-3 предложения максимум
- Говори про ЕЁ конкретную цель — не общие слова
- Тон: тёплая подруга которая искренне радуется
- Упомяни цифру {completed_days} дней
- Свяжи с её целью или мечтой
- Каждый раз разный текст
- Без markdown, без восклицательных знаков через слово
- Один эмодзи максимум"""

    def generate_message() -> str:
        model_names = build_model_chain(select_model_id())
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": prompt}],
                    system="Пиши тепло и лично. Только текст поздравления.",
                    max_tokens=150,
                    cache_core=False,
                ).strip()
                if text:
                    return sanitize_bot_reply(text)
            except Exception as e:
                log.warning("milestone generate %s: %s", mid, e)
        display_name = name or "подруга"
        return f"{display_name}, {completed_days} дней — ты реально двигаешься к своей цели 💙"

    message = await asyncio.to_thread(generate_message)

    profile[shown_key] = True
    db_store.update_profile(int(tid), {shown_key: True})
    user_profiles[tid] = profile

    return {
        "milestone": {
            "days": completed_days,
            "message": message,
            "name": name,
        }
    }


@app.get("/api/calendar")
async def calendar_endpoint(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    profile = _resolve_user_profile(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    start = _profile_cycle_start(profile, tid)
    today = _profile_local_date(profile)
    by_date = {
        str(r.get("date", ""))[:10]: r.get("task_completed")
        for r in db_store.list_daily_summaries(tid)
        if r.get("date")
    }
    days: list[dict] = []
    for i in range(84):
        d = start + timedelta(days=i)
        iso = d.isoformat()
        days.append(
            {
                "date": iso,
                "task_completed": by_date.get(iso),
                "is_today": iso == today.isoformat(),
                "is_future": d > today,
                "week_index": i // 7,
                "day_index": i % 7,
            }
        )
    cw = max(1, min(12, int(profile.get("current_week") or 1)))
    return {
        "cycle_start_date": start.isoformat(),
        "current_week": cw,
        "days": days,
    }


@app.post("/api/mark-day")
async def mark_day_endpoint(
    request: Request,
    telegram_id: str | None = Query(default=None, min_length=1, max_length=32),
    body: dict | None = Body(default=None),
) -> dict:
    tid = _auth_telegram_id(request, telegram_id)
    profile = _resolve_user_profile(tid)
    if not isinstance(profile, dict):
        raise HTTPException(status_code=404, detail="profile not found")

    if isinstance(body, dict) and body.get("streak_only"):
        _touch_streak_for_activity(int(tid), profile)
        return {
            "ok": True,
            "streak": int(profile.get("streak") or 0),
            "display_streak": _display_streak(profile, tid),
            "profile": _enrich_profile_for_api(profile, tid),
        }

    tc = None
    if isinstance(body, dict) and body.get("task_completed"):
        tc = db_store.normalize_task_completed(body["task_completed"])

    if tc == "false":
        return {"profile": _enrich_profile_for_api(profile, tid)}

    if tc:
        patch: dict[str, object] = {"task_completed": tc}
        if tc == "true":
            patch["completed"] = True
        _update_today_summary_field(int(tid), profile, **patch)

    if tc == "true":
        today = _profile_local_date(profile)
        _bump_streak_on_mark(profile, today)
        profile["week_scores"] = _week_scores_array(profile, tid)

        db_store.upsert_profile(int(tid), profile)
        user_profiles[tid] = profile

    user_profiles[tid] = profile
    return {"profile": _enrich_profile_for_api(profile, tid)}


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
