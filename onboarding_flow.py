"""Онбординг SpiceSpace — 12-Week Year: мечта → цель → тактики → время."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Callable
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes

import db
from bot_typing import typing_while
from claude_client import generate as claude_generate
from prompts import (
    CHANGE_WEEKLY_GOAL_SYSTEM,
    GOAL_DIALOG_SYSTEM,
    NAME_EXTRACT_PROMPT,
    WEEKLY_RECAP_DIALOG_SYSTEM,
    WEEKLY_TACTICS_DIALOG_SYSTEM,
    goal_polish_prompt_template,
)
from summaries import save_onboarding_summary

if TYPE_CHECKING:
    pass

log = logging.getLogger("coach_bot")

_flow_outgoing_prepare: Callable[[int, str], str] | None = None

FIRST_PAIN_QUESTION_SYSTEM = """Ты Спейс. Пользователь только что назвал своё имя.
Задай один короткий живой вопрос про то что сейчас не так в его жизни
или что хочется изменить. Не используй слова 'мечта', 'представь',
'через 3 месяца'. Говори как подруга — просто и по-человечески.
Один вопрос, 1-2 предложения максимум.
Ответь только текстом вопроса, без JSON и кавычек."""

WHY_DIG_SYSTEM = """Ты Спейс. Твоя задача — докопаться до истинной причины цели пользователя.
Задавай уточняющие вопросы 'зачем тебе это?' / 'что изменится когда это случится?'
/ 'что это даст тебе на самом деле?'
Один вопрос за раз. Не переходи к формулировке цели пока не поняла
эмоциональную суть — уверенность, свобода, признание, покой и т.д.
Как только поняла суть — переходи к формулировке цели:
мягко спроси что самое важное реализовать за эти 12 недель или предложи черновик.

КРИТИЧЕСКИ ВАЖНО: задавай строго ОДИН вопрос за раз.
Никогда не задавай два вопроса в одном сообщении.
Если хочется спросить несколько вещей — выбери самый важный.

ЗАПРЕЩЕНО: слова 'мечта', 'представь через 3 месяца', коуч-язык, markdown.
Максимум 3 предложения.

Верни JSON: {"message": "...", "ready_for_goal": true/false}
ready_for_goal=true только когда эмоциональная суть ясна и можно переходить к цели на 12 недель."""

DONT_KNOW_EXIT_SYSTEM = """Ты Спейс. Пользователь несколько раз ответил 'не знаю'.
Скажи тепло но честно что не можешь помочь если человек сам не готов
разобраться. Скажи что ты здесь и готова помочь когда он будет готов.
Попрощайся. 2-3 предложения.
Только текст ответа, без JSON."""

VARY_PHRASE_SYSTEM = """Скажи то же самое своими словами, каждый раз немного по-другому.
Тон — живая подруга, не бот. Сохрани смысл и ключевые факты/имена/числа.
Без markdown. Только готовый текст для пользователя."""

RETURNING_ONBOARDING_SYSTEM = """Ты Спейс. Пользователь уже проходил онбординг раньше — ты его знаешь.
Не используй стандартные фразы из первого знакомства.
Говори как подруга которая продолжает разговор, не как бот который
запустил скрипт заново. Каждый раз формулируй немного по-другому.
Сохрани смысл исходной фразы и ключевые факты/имена/числа.
Без markdown. Только готовый текст для пользователя."""

GOAL_SPHERE_SYSTEM = """Classify the goal into one word only:
health — body, fitness, weight, sport, energy, sleep
money — money, career, business, clients, income
relations — relationships, family, love, friends
creative — creative work, content, project, media, art
other — anything else
Reply with only one word: health, money, relations, creative, or other."""

SAME_SPHERE_OPENING_SYSTEM = """Ты Спейс. Пользователь меняет цель но остаётся в той же сфере.
Начни с того что помнишь предыдущую цель и спроси что пошло не так.
Используй имя и конкретную предыдущую цель. Без приветствия.
1-3 предложения. Только текст."""

DIFF_SPHERE_OPENING_SYSTEM = """Ты Спейс. Пользователь меняет цель на другую сферу.
Без приветствия. Коротко отметь что раньше было другое направление
и спроси что сейчас важнее. Используй имя если есть.
1-3 предложения. Только текст."""


def register_flow_outgoing_prepare(fn: Callable[[int, str], str]) -> None:
    global _flow_outgoing_prepare
    _flow_outgoing_prepare = fn


async def flow_reply_text(msg, text: str) -> None:
    cid = msg.chat_id
    out = text
    if _flow_outgoing_prepare:
        out = _flow_outgoing_prepare(cid, text)
    await msg.reply_text(out)

BOT_BUILD = "trial-messages-v38"

OB_RETURNING = 0
OB_NAME = 1
OB_VISION = 2
OB_VISION_DIALOG = 3
OB_GOAL_12W = 4
OB_GOAL_DIALOG = 5
OB_WEEKLY_TACTICS = 6
OB_MORNING_TIME = 7
OB_EVENING_TIME = 8
OB_DONE = 9
OB_CHANGE_WEEKLY = 10
OB_CHANGE_12W = 11
OB_WEEKLY_RECAP = 12
OB_REENGAGE_GOAL = 13

GOAL_TYPE_12W = "12-недельная цель"
GOAL_TYPE_WEEKLY = "цель на неделю"

_GOAL_CONFIRM_YES = (
    "да",
    "ок",
    "окей",
    "верно",
    "верна",
    "подходит",
    "соглас",
    "согласна",
    "yes",
    "ага",
    "угу",
    "именно",
    "точно",
    "всё верно",
    "все верно",
    "записывай",
    "запиши",
)

_GOAL_CONFIRM_NO = (
    "нет",
    "не верно",
    "не то",
    "не подходит",
    "измени",
    "перепиши",
    "другой",
    "другая",
    "не надо",
)

# Совместимость с main.py
OB_ASK_NAME = OB_NAME
OB_ASK_MORNING_TIME = OB_MORNING_TIME
OB_ASK_EVENING_TIME = OB_EVENING_TIME

GREETING_NEW_RU = "Привет! 👋 Меня зовут Спейс. Как тебя зовут?"
GREETING_NEW_EN = "Hey! 👋 I'm Space. What's your name?"
GREETING_NEW = GREETING_NEW_EN


def get_greeting_new(lang: str = "en") -> str:
    return s("greeting", lang)


def _is_ru(lang: str) -> bool:
    return str(lang or "en").lower().startswith("ru")


def _ob_lang(st: dict | None = None, profile: dict | None = None) -> str:
    if isinstance(st, dict):
        lc = str(st.get("lang") or st.get("language_code") or "").strip()
        if lc:
            return lc
    if isinstance(profile, dict):
        lc = str(profile.get("language_code") or "").strip()
        if lc:
            return lc
    return "en"


def _friend_word(lang: str) -> str:
    return s("friend_default", lang)


def _goal_type_label(goal_type: str, lang: str) -> str:
    if _is_ru(lang):
        return goal_type
    if goal_type == GOAL_TYPE_12W:
        return "12-week goal"
    return "weekly goal"


def _confirmation_text(goal: str, lang: str = "en") -> str:
    g = (goal or "").strip()
    return f"{s('wrote_down', lang)} {g}. {s('right', lang)}"


def _weekly_confirmation_text(goal: str, lang: str = "en") -> str:
    g = (goal or "").strip()
    return f"{s('wrote_down', lang)} {g}. {s('right', lang)}"


_EN_SYSTEM_PREFIX = (
    "YOU MUST RESPOND IN ENGLISH ONLY. THIS IS NON-NEGOTIABLE. "
    "DO NOT USE RUSSIAN UNDER ANY CIRCUMSTANCES.\n\n"
)
_EN_USER_OVERRIDE = (
    "[SYSTEM OVERRIDE: Respond in English only. Never use Russian.]\n\n"
)


def _system_with_lang(system: str, lang: str) -> str:
    lc = str(lang or "en").lower()
    if lc.startswith("ru"):
        return "IMPORTANT: User speaks Russian. Respond in Russian only.\n\n" + system
    return _EN_SYSTEM_PREFIX + system


def _user_content_with_lang(content: str, lang: str) -> str:
    lc = str(lang or "en").lower()
    if lc.startswith("ru"):
        return content
    return _EN_USER_OVERRIDE + content


def _messages_with_lang(messages: list[dict], lang: str) -> list[dict]:
    """Prepend EN override to the last user message in a Claude turn list."""
    lc = str(lang or "en").lower()
    if lc.startswith("ru"):
        return messages
    out = [dict(m) for m in messages]
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user" and out[i].get("content"):
            out[i] = {
                **out[i],
                "content": _EN_USER_OVERRIDE + str(out[i]["content"]),
            }
            break
    return out


def _claude_lang_suffix(lang: str) -> str:
    """Deprecated: use _system_with_lang."""
    if _is_ru(lang):
        return "\n\nIMPORTANT: User speaks Russian. Respond in Russian only."
    return ""


STRINGS: dict[str, dict[str, str]] = {
    "ru": {
    "greeting": GREETING_NEW_RU,
    "greeting_new": GREETING_NEW_RU,
    "nice_to_meet": "приятно познакомиться 💙",
    "privacy": (
        "Кстати — всё что ты пишешь здесь остаётся между нами. "
        "Твои цели и наши разговоры не видит никто другой. 🔒"
    ),
    "dream_intro": (
        "Что сейчас сильнее всего хочется изменить в своей жизни?"
    ),
    "wrote_down": "Записала ✨",
    "right": "Верно?",
    "great": "Отлично 🎯",
    "got_it": "Понятно 💙",
    "okay": "Хорошо 💙",
    "lets_go": "Погнали 💙",
    "got_everything": "Всё запомнила ✨",
    "morning_at": "Буду писать утром в",
    "evening_at": "и вечером в",
    "goal_12weeks": "Цель на 12 недель:",
    "first_week": "Первая неделя:",
    "writing_down": "Записываю:",
    "correct": "Верно?",
    "now_first_week": "Теперь первая неделя.",
    "what_week": "Что хочешь сделать на этой неделе?",
    "morning_question": (
        "Когда тебе удобнее всего побыть наедине с собой — без детей, без работы, без суеты? 🌅\n\n"
        "В это время я буду писать тебе чтобы сосредоточиться на твоей цели. "
        "Лучше выбирать утро или первую половину дня."
    ),
    "evening_question": (
        "И ещё — в какое время вечером мне спрашивать как прошёл день? 🌙"
    ),
    "open_miniapp": "Смотри как выглядит твой прогресс 👇",
    "onboarding_done_btn": "✦ Открыть SpiceSpace",
    "reminder_question": (
        "Напомнить тебе про задачу днём? Напиши время — например, 14:00. "
        "Или «нет» если не нужно."
    ),
    "reminder_set": "Напомню в",
    "invalid_time": "Напиши время в формате ЧЧ:ММ — например, 14:00. Или «нет».",
    "onboarding_reminder": "ты там? Осталось буквально пара вопросов 💙",
    "week_locked": (
        "Одна цель на 12 недель — это правило, не рекомендация. "
        "Именно один фокус даёт результат."
    ),
    "confirm_12w": "Отлично 🎯 Цель на 12 недель зафиксирована.",
    "dialog_user": "Пользователь",
    "dialog_space": "Спейс",
    "dialog_start": "Начало диалога",
    "goal_proposed": "Получается твоя цель: {goal}. Так?",
    "goal_detail_fallback": (
        "Расскажи подробнее — что именно хочешь изменить за эти 12 недель?"
    ),
    "vision_detail_fallback": (
        "Расскажи подробнее — что в этом дне для тебя самое важное?"
    ),
    "vision_ready": (
        "Слышу тебя — картина уже вырисовывается 💚 "
        "Окей, из всего этого — что самое важное реализовать за эти 12 недель?"
    ),
    "weekly_saved_short": "Записала — звучит конкретно.",
    "weekly_ask_sentence": (
        "Что именно хочешь сделать на этой неделе — одним предложением?"
    ),
    "name_extract_system": "Верни только имя, одно слово или имя целиком.",
    "polish_goal_system": "Верни только отшлифованную цель, без кавычек и пояснений.",
    "not_specified": "не указана",
    "history_onboarding": (
        "[SpiceSpace] Онбординг: {name}, цель 12 нед — {main}, "
        "неделя 1 — {weekly}, утро {mt}, вечер {et}."
    ),
    "friend_default": "подруга",
    "greeting_returning": (
        "Привет, {name} 🙂 Ты уже со мной.\n\n"
        "Хочешь обновить профиль или просто поговорить?"
    ),
    "greeting_after_name": "{name}, приятно познакомиться 💚",
    "vision_question": (
        "Что сейчас сильнее всего хочется изменить в своей жизни?"
    ),
    "vision_privacy": (
        "Кстати — всё что ты пишешь здесь остаётся между нами. "
        "Твои цели и наши разговоры не видит никто другой. 🔒"
    ),
    "morning_time_question": (
        "Когда тебе удобнее всего побыть наедине с собой — без детей, без работы, без суеты? 🌅\n\n"
        "В это время я буду писать тебе чтобы сосредоточиться на твоей цели. "
        "Лучше выбирать утро или первую половину дня."
    ),
    "evening_time_question": (
        "И ещё — в какое время вечером мне спрашивать как прошёл день? 🌙"
    ),
    "onboarding_complete": (
        "Всё запомнила ✨\n\n"
        "Цель на 12 недель: {main_goal}\n"
        "Первая неделя: {weekly_goal}\n\n"
        "Буду писать утром в {mt} и вечером в {et}.\n\n"
        "Погнали 💚"
    ),
    "progress_open": "Смотри как выглядит твой прогресс 👇",
    "first_msg_fallback": "Кому первому отправишь бота на тест? 💚",
    "returning_just_chat": "Хорошо, я здесь. Напиши что у тебя на душе.",
    "returning_hint": (
        "Напиши «заново» или «обновить цели» — перенастроим цели. "
        "Или «поговорить» — просто поболтаем."
    ),
    "profile_incomplete_reonboard": (
        "{name}, в базе есть имя, но цели не подтянулись в приложение. "
        "Давай заново пройдём настройку целей — отвечай на вопросы ниже 👇"
    ),
    "goal_rewrite_12w": "Окей, напиши 12-недельную цель своими словами — как хочешь чтобы звучало.",
    "goal_rewrite_weekly": "Окей, напиши цель на эту неделю своими словами — как хочешь чтобы звучало.",
    "goal_confirm_yes_no": "Напиши «да» / «верно» — или перепиши цель.",
    "goal_saved_confirm": "Записала ✨ {polished}. Верно?",
    "time_morning_unclear": "Не совсем поняла. Напиши, пожалуйста, в формате 09:30.",
    "time_evening_unclear": (
        "Не совсем поняла. Напиши вечернее время как 21:00 или «в 9 вечера»."
    ),
    "change_weekly_opening": (
        "Меняем цель на эту неделю 💚\n\n"
        "Твоя цель на 12 недель: {main_goal}"
    ),
    "new_week_opening": (
        "Теперь поставим цель на следующую неделю 💚\n\n"
        "Твоя цель на 12 недель: {main_goal}\n\n"
        "Сейчас предложу варианты — выбери или напиши свой."
    ),
    "weekly_saved_evening": (
        "Записала 💚 Цель на эту неделю: {weekly}. "
        "Утром напишу как обычно — с задачей на день."
    ),
    "weekly_recap_opening": (
        "Последний вечер этой недели — давай подведём итоги.\n\n"
        "Цель этой недели была: {weekly_goal}\n\n"
        "Как прошла неделя? Что получилось, а что нет?"
    ),
    "change_12w_choice": (
        "Хочешь начать новый 12-недельный цикл с новой целью — "
        "или просто скорректировать текущую?"
    ),
    "change_12w_adjust": (
        "Окей, давай уточним. Сейчас твоя цель: {goal}\n"
        "Что хочешь изменить?"
    ),
    "change_12w_choice_hint": (
        "Напиши «новый цикл» — начнём с мечты заново, "
        "или «скорректировать» — уточним текущую цель."
    ),
    "change_12w_broken": "Что-то сбилось. Напиши «поменять цель» ещё раз.",
    "main_to_weekly": "Отлично 🎯 Теперь первая неделя.",
    "weekly_saved": "Записала 💚 Цель на эту неделю: {weekly}.",
    "weekly_saved_morning_now": (
        "Записала 💚 Цель на эту неделю: {weekly}. Вот задача на сегодня 👇"
    ),
    "finish_12w": (
        "Записала ✨ Новая цель на 12 недель: {main}\n"
        "На эту неделю: {weekly}\n"
        "Погнали с чистого листа 💚"
    ),
    "something_wrong": "Что-то сбилось. Нажми /start — начнём сначала.",
    "vision_fallback": "Расскажи ещё чуть-чуть?",
    "goal_fallback": "Расскажи подробнее?",
    "weekly_dialog_fallback": "Расскажи подробнее?",
    "default_goal": "твоя цель",
    "open_spicespace_btn": "Открыть SpiceSpace",
    "app_progress_hint": "Твой прогресс 👇",
    "time_update_miniapp": (
        "Зайди в мини апп — там кнопка ✏️ Изменить рядом со временем. "
        "Меняется за секунду 👇"
    ),
    "cmd_stop_msg": (
        "Утренние и вечерние сообщения выключены. Напиши /start, чтобы снова включить."
    ),
    "cmd_reset_msg": (
        "Контекст диалога сброшен. Можем начать с чистого листа. "
        "Чтобы пройти знакомство снова — /start."
    ),
    "reengagement_btn_continue": "Продолжаем 💪",
    "reengagement_btn_new_goal": "Поставим новую цель 🎯",
    "reengagement_continue": (
        "Отлично, я здесь 🙂 Завтра утром напишу как обычно."
    ),
    "reengagement_goal_ask": (
        "Расскажи — что сейчас актуальнее? Какую цель хочешь поставить?"
    ),
    "reengagement_goal_saved": (
        "Записала ✨ Цель: {main}\n"
        "На эту неделю: {weekly}\n"
        "Завтра утром напишу как обычно 💚"
    ),
    "reengagement_fallback": (
        "Привет, я заметила, что тебя давно не было — и это окей. "
        "Хочешь продолжить с тем, что было, или поставим новую цель?"
    ),
    "reengagement_claude_user": "Напиши сообщение.",
    "reengagement_claude_user_named": "Имя пользователя: {name}",
    "subscription_activated": (
        "Подписка активирована ✨\n\n"
        "Спейс с тобой до {end_date}.\n\n"
        "В мини-апп → «Подписка» — там видно, до какого числа всё активно 💚"
    ),
    "subscription_expired": (
        "{name}, подписка закончилась 🌙\n\n"
        "Хочешь продолжить — открой мини апп и выбери тариф."
    ),
    "subscription_expiring": (
        "{name}, подписка заканчивается через 3 дня 🌙 Не забудь продлить."
    ),
    "subscription_renew_btn": "💳 Продлить",
    "subscription_invoice_desc": (
        "Спейс помнит тебя и помогает не сливаться с целей. {label} доступа."
    ),
    "subscription_checkout_invalid": (
        "Неизвестный тариф. Выбери план в мини-приложении."
    ),
    "trial_expired_offer": (
        "{name}, 3 бесплатных дня прошли 🌙\n\n"
        "Утренние и вечерние сообщения на паузе. "
        "Оформи подписку — и продолжим."
    ),
    "subscription_paywall": (
        "{name}, для общения со мной нужна подписка 🌙\n\n"
        "Бесплатный период закончился — выбери тариф в мини-апп."
    ),
    "subscription_subscribe_btn": "💳 Оформить подписку",
    "subscription_view_btn": "💳 Моя подписка",
    },
    "en": {
    "greeting": GREETING_NEW_EN,
    "greeting_new": GREETING_NEW_EN,
    "nice_to_meet": "nice to meet you 💙",
    "privacy": (
        "By the way — everything you write here stays between us. "
        "Your goals and our conversations are visible only to you 🔒"
    ),
    "dream_intro": (
        "What do you most want to change in your life right now?"
    ),
    "wrote_down": "Got it ✨",
    "right": "Right?",
    "great": "Great 🎯",
    "got_it": "Got it 💙",
    "okay": "Okay 💙",
    "lets_go": "Let's go 💙",
    "got_everything": "Got everything ✨",
    "morning_at": "I'll message you mornings at",
    "evening_at": "and evenings at",
    "goal_12weeks": "12-week goal:",
    "first_week": "First week:",
    "writing_down": "Writing down:",
    "correct": "Right?",
    "now_first_week": "Now let's plan your first week.",
    "what_week": "What do you want to accomplish this week?",
    "morning_question": (
        "When is the best time for you to focus on yourself — no kids, no work, no chaos? 🌅\n\n"
        "This is when I'll message you to focus on your goal. "
        "Morning or first half of the day works best."
    ),
    "evening_question": (
        "And what time in the evening should I check in on how your day went? 🌙"
    ),
    "open_miniapp": "See how your progress looks 👇",
    "onboarding_done_btn": "✦ Open SpiceSpace",
    "reminder_question": (
        "Want me to remind you about your task during the day? Write a time — "
        "for example, 2:00 PM. Or 'no' if you don't need it."
    ),
    "reminder_set": "I'll remind you at",
    "invalid_time": "Write the time in HH:MM format — for example, 14:00. Or 'no'.",
    "onboarding_reminder": "you there? Just a couple more questions 💙",
    "week_locked": (
        "One goal for 12 weeks — that's the rule, not a suggestion. "
        "One focus is what gets results."
    ),
    "confirm_12w": "Great 🎯 12-week goal is set.",
    "dialog_user": "User",
    "dialog_space": "Space",
    "dialog_start": "Dialog start",
    "goal_proposed": "So your goal is: {goal}. Right?",
    "goal_detail_fallback": "Tell me more — what do you want to change in these 12 weeks?",
    "vision_detail_fallback": "Tell me more — what matters most in that day for you?",
    "vision_ready": (
        "I hear you — the picture is taking shape 💚 "
        "What matters most to make real in these 12 weeks?"
    ),
    "weekly_saved_short": "Saved — sounds specific.",
    "weekly_ask_sentence": "What do you want to do this week — in one sentence?",
    "name_extract_system": "Return only the name, one word or full name.",
    "polish_goal_system": "Return only the polished goal, no quotes or explanations.",
    "not_specified": "not specified",
    "history_onboarding": (
        "[SpiceSpace] Onboarding: {name}, 12-week goal — {main}, "
        "week 1 — {weekly}, morning {mt}, evening {et}."
    ),
    "friend_default": "friend",
    "greeting_returning": (
        "Hey, {name} 🙂 You're already with me.\n\n"
        "Want to update your profile or just chat?"
    ),
    "greeting_after_name": "Nice to meet you, {name} 💚",
    "vision_question": (
        "What do you most want to change in your life right now?"
    ),
    "vision_privacy": (
        "By the way — everything you write here stays between us. "
        "Your goals and our conversations are visible only to you 🔒"
    ),
    "morning_time_question": (
        "When is the best time for you to be alone with yourself — "
        "without kids, work, or chaos? 🌅\n\n"
        "This is when I'll write to you to focus on your goal. "
        "Morning or first half of the day works best."
    ),
    "evening_time_question": (
        "And what time in the evening should I check in on how your day went? 🌙"
    ),
    "onboarding_complete": (
        "Got it all ✨\n\n"
        "12-week goal: {main_goal}\n"
        "First week: {weekly_goal}\n\n"
        "I'll write mornings at {mt} and evenings at {et}.\n\n"
        "Let's go 💙"
    ),
    "progress_open": "See how your progress looks 👇",
    "first_msg_fallback": "Who will you send the bot to first for testing? 💚",
    "returning_just_chat": "Okay, I'm here. Tell me what's on your mind.",
    "returning_hint": (
        "Type «start over» or «update goals» to reconfigure. Or «just chat» to talk."
    ),
    "profile_incomplete_reonboard": (
        "{name}, I have your name but goals didn't load in the app. "
        "Let's set up your goals again — answer below 👇"
    ),
    "goal_rewrite_12w": "Okay — rewrite your 12-week goal in your own words.",
    "goal_rewrite_weekly": "Okay — rewrite this week's goal in your own words.",
    "goal_confirm_yes_no": "Type «yes» / «correct» — or rewrite the goal.",
    "goal_saved_confirm": "Wrote it down ✨ {polished}. Right?",
    "time_morning_unclear": "I didn't quite get that. Please use format 09:30.",
    "time_evening_unclear": (
        "I didn't quite get that. Try 21:00 or «9 pm» for evening time."
    ),
    "change_weekly_opening": (
        "Okay, let's change this week's goal. Your 12-week goal is: {main_goal}\n\n"
        "What do you want to do this week instead?"
    ),
    "new_week_opening": (
        "Let's set your goal for the coming week 💚\n\n"
        "12-week goal: {main_goal}\n\n"
        "I'll suggest a few options — pick one or write your own."
    ),
    "weekly_saved_evening": (
        "Saved 💚 This week's goal: {weekly}. "
        "Tomorrow morning I'll message you as usual with today's task."
    ),
    "weekly_recap_opening": (
        "Sunday evening — time to wrap up the week.\n\n"
        "This week's goal was: {weekly_goal}\n\n"
        "How did the week go? What worked and what didn't?"
    ),
    "change_12w_choice": (
        "Do you want to start a new 12-week cycle with a new goal — "
        "or adjust the current one?"
    ),
    "change_12w_adjust": (
        "Okay, let's clarify. Your current goal: {goal}\n"
        "What do you want to change?"
    ),
    "change_12w_choice_hint": (
        "Type «new cycle» to dream again from scratch, "
        "or «adjust» to refine your current goal."
    ),
    "change_12w_broken": "Something went wrong. Type «change goal» again.",
    "main_to_weekly": "First week. What do you want to accomplish this week?",
    "weekly_saved": "Saved 💚 This week's goal: {weekly}.",
    "weekly_saved_morning_now": (
        "Saved 💚 This week's goal: {weekly}. Here's today's task 👇"
    ),
    "finish_12w": (
        "Saved ✨ New 12-week goal: {main}\n"
        "This week: {weekly}\n"
        "Fresh start 💚"
    ),
    "something_wrong": "Something went wrong. Tap /start to begin again.",
    "vision_fallback": "Tell me a bit more?",
    "goal_fallback": "Tell me more?",
    "weekly_dialog_fallback": "Tell me more?",
    "default_goal": "your goal",
    "open_spicespace_btn": "Open SpiceSpace",
    "app_progress_hint": "Your progress 👇",
    "time_update_miniapp": (
        "Open the mini app — tap ✏️ Edit next to your message times. "
        "Takes a second 👇"
    ),
    "cmd_stop_msg": (
        "Morning and evening messages are off. Send /start to turn them back on."
    ),
    "cmd_reset_msg": (
        "Chat context cleared. We can start fresh. "
        "To go through setup again — /start."
    ),
    "reengagement_btn_continue": "Let's continue 💪",
    "reengagement_btn_new_goal": "Set a new goal 🎯",
    "reengagement_continue": (
        "Great, I'm here 🙂 I'll message you tomorrow morning as usual."
    ),
    "reengagement_goal_ask": (
        "Tell me — what feels more relevant right now? What goal do you want to set?"
    ),
    "reengagement_goal_saved": (
        "Saved ✨ Goal: {main}\n"
        "This week: {weekly}\n"
        "I'll message you tomorrow morning as usual 💚"
    ),
    "reengagement_fallback": (
        "Hey, I noticed you've been away for a while — and that's okay. "
        "Want to pick up where we left off, or set a new goal?"
    ),
    "reengagement_claude_user": "Write the message.",
    "reengagement_claude_user_named": "User name: {name}",
    "subscription_activated": (
        "Subscription activated ✨\n\n"
        "Space is with you until {end_date}.\n\n"
        "In the mini app → «Subscription» — you'll see how long it's active 💚"
    ),
    "subscription_expired": (
        "{name}, your subscription ended 🌙\n\n"
        "To continue — open the mini app and pick a plan."
    ),
    "subscription_expiring": (
        "{name}, your subscription ends in 3 days 🌙 Don't forget to renew."
    ),
    "subscription_renew_btn": "💳 Renew",
    "subscription_invoice_desc": (
        "SpiceSpace remembers you and helps you stay on track. {label} of access."
    ),
    "subscription_checkout_invalid": (
        "Unknown plan. Pick a plan in the mini app."
    ),
    "trial_expired_offer": (
        "{name}, your 3 free days are up 🌙\n\n"
        "Morning and evening messages are paused. "
        "Subscribe to keep going."
    ),
    "subscription_paywall": (
        "{name}, you need a subscription to chat with me 🌙\n\n"
        "Your free trial has ended — pick a plan in the mini app."
    ),
    "subscription_subscribe_btn": "💳 Subscribe",
    "subscription_view_btn": "💳 My subscription",
    },
}


def s(key: str, lang: str = "en", **kwargs: object) -> str:
    """Get string in correct language."""
    lang_key = "ru" if _is_ru(lang) else "en"
    text = STRINGS[lang_key].get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        return str(text).format(**kwargs)
    return str(text)


def open_spicespace_button_label(lang: str = "en", *, prefix: str = "") -> str:
    if prefix in ("✦", "✦ "):
        return s("onboarding_done_btn", lang)
    label = s("open_spicespace_btn", lang)
    return f"{prefix}{label}" if prefix else label


def _mini_app_root_needs_webapp_suffix(root: str) -> bool:
    """Railway serves static UI at /webapp; Vercel deploy uses site root."""
    r = (root or "").strip().rstrip("/").lower()
    if not r or r.endswith("/webapp"):
        return False
    if "vercel.app" in r:
        return False
    if ".railway.app" in r or "localhost" in r or "127.0.0.1" in r:
        return True
    return False


def mini_app_webapp_url(base: str, cid: int, lang: str = "en") -> str:
    root = str(base or "").strip().rstrip("/")
    if not root:
        root = "https://spice-space.vercel.app"
    elif _mini_app_root_needs_webapp_suffix(root):
        root = f"{root}/webapp"
    lang_q = "ru" if _is_ru(lang) else "en"
    return f"{root}/?telegram_id={cid}&lang={lang_q}"


def ob_text(key: str, lang: str = "en", **kwargs: object) -> str:
    return s(key, lang, **kwargs)


def get_greeting(lang: str = "en") -> str:
    return get_greeting_new(lang)


def morning_time_question(lang: str = "en") -> str:
    return ob_text("morning_time_question", lang)


def evening_time_question(lang: str = "en") -> str:
    return ob_text("evening_time_question", lang)


MORNING_TIME_QUESTION = morning_time_question()
EVENING_TIME_QUESTION = evening_time_question()

_KIDS_HINTS = (
    "ребён",
    "ребен",
    "дети",
    "детей",
    "дочь",
    "дочк",
    "сын",
    "сынов",
    "малыш",
    "младен",
    "груднич",
    "садик",
    "сад ",
    "в сад",
    "школ",
    "няня",
    "нян",
    "подгуз",
    "коляск",
    "родитель",
    "мама ",
    "мамой",
)

_WORD_HOURS: dict[str, int] = {
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}

_WEEKLY_GOAL_AGREE = (
    "да",
    "ок",
    "окей",
    "подходит",
    "давай",
    "соглас",
    "согласна",
    "верно",
    "хорошо",
    "угу",
    "yes",
    "ага",
    "конечно",
    "именно",
    "точно",
    "беру",
    "возьму",
    "выбираю",
)

_WEEKLY_GOAL_DISAGREE = (
    "нет",
    "не подходит",
    "не то",
    "другой",
    "другая",
    "измени",
    "переделай",
    "не хочу",
    "не надо",
)


def _seed_from_profile(st: dict, profile: dict) -> None:
    lang = _ob_lang(st, profile)
    st.setdefault("name", str(profile.get("name") or _friend_word(lang)).strip())
    st.setdefault("main_goal", str(profile.get("main_goal") or profile.get("final_goal") or "").strip())
    st.setdefault("vision", str(profile.get("vision") or "").strip())
    st.setdefault("weekly_goal", str(profile.get("weekly_goal") or "").strip())
    st.setdefault("morning_time", profile.get("morning_time") or profile.get("daily_time") or "09:30")
    st.setdefault("evening_time", profile.get("evening_time") or "21:00")
    st.setdefault("timezone", profile.get("timezone") or _default_timezone())
    st.setdefault("time_per_day", profile.get("time_per_day") or "30 минут")
    if profile.get("has_kids") is not None:
        st["has_kids"] = profile.get("has_kids")


def change_weekly_opening(profile: dict, lang: str = "en") -> str:
    main = str(
        profile.get("main_goal") or profile.get("final_goal") or ob_text("default_goal", lang)
    ).strip()
    return ob_text("change_weekly_opening", lang, main_goal=main)


def new_week_opening(profile: dict, lang: str = "en") -> str:
    main = str(
        profile.get("main_goal") or profile.get("final_goal") or ob_text("default_goal", lang)
    ).strip()
    return ob_text("new_week_opening", lang, main_goal=main)


def weekly_recap_opening(profile: dict, lang: str = "en") -> str:
    weekly = str(profile.get("weekly_goal") or ob_text("default_goal", lang)).strip()
    return ob_text("weekly_recap_opening", lang, weekly_goal=weekly)


def start_weekly_recap(
    onboarding: dict[int, dict],
    cid: int,
    profile: dict,
    *,
    days_since_start: int,
) -> None:
    lang = _ob_lang(profile=profile)
    st: dict = {
        "step": OB_WEEKLY_RECAP,
        "recap_turns": [],
        "days_since_start": int(days_since_start),
        "lang": lang,
        "language_code": lang,
    }
    _seed_from_profile(st, profile)
    onboarding[cid] = st


async def _kickoff_weekly_options_message(
    bot,
    cid: int,
    st: dict,
    model_names: list[str],
    *,
    opening: str | None = None,
) -> bool:
    lang = _ob_lang(st)
    try:
        if opening:
            await bot.send_message(chat_id=cid, text=opening)
        async with typing_while(bot, cid):
            result = await _claude_weekly_tactics_dialog(
                [],
                model_names,
                main_goal=str(st.get("main_goal") or ""),
                user_message="",
                lang=lang,
            )
        reply = (
            result.get("message") or ob_text("weekly_dialog_fallback", lang)
        ).strip()
        st["weekly_turns"] = [{"role": "assistant", "content": reply[:2000]}]
        await bot.send_message(chat_id=cid, text=reply)
        return True
    except Exception as e:
        log.warning("kickoff_weekly_options cid=%s: %s", cid, e)
        return False


async def kickoff_change_weekly_dialog(
    bot,
    cid: int,
    profile: dict,
    onboarding: dict[int, dict],
    model_names: list[str],
) -> bool:
    """Manual weekly goal change: short intro + 2-3 options from main_goal."""
    start_change_weekly(onboarding, cid, profile, from_week_start=False)
    lang = _ob_lang(profile=profile)
    opening = change_weekly_opening(profile, lang)
    return await _kickoff_weekly_options_message(
        bot, cid, onboarding[cid], model_names, opening=opening
    )


async def kickoff_new_week_goal_after_recap(
    bot,
    cid: int,
    profile: dict,
    onboarding: dict[int, dict],
    model_names: list[str],
) -> bool:
    """Day 7 evening: after recap, help user set next week's goal (proactive dialog)."""
    start_change_weekly(onboarding, cid, profile, from_week_start=True)
    lang = _ob_lang(profile=profile)
    opening = new_week_opening(profile, lang)
    return await _kickoff_weekly_options_message(
        bot, cid, onboarding[cid], model_names, opening=opening
    )


def start_change_weekly(
    onboarding: dict[int, dict],
    cid: int,
    profile: dict,
    *,
    from_week_start: bool = False,
) -> None:
    lang = _ob_lang(profile=profile)
    st: dict = {
        "step": OB_CHANGE_WEEKLY,
        "change_mode": "weekly_only",
        "weekly_turns": [],
        "from_week_start": bool(from_week_start),
        "weekly_flow_started_at": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "language_code": lang,
    }
    _seed_from_profile(st, profile)
    onboarding[cid] = st


def change_12w_choice_prompt(lang: str = "en") -> str:
    return ob_text("change_12w_choice", lang)


def change_12w_adjust_opening(main_goal: str, lang: str = "en") -> str:
    g = (main_goal or ob_text("default_goal", lang)).strip()
    return ob_text("change_12w_adjust", lang, goal=g)


def start_change_12w(onboarding: dict[int, dict], cid: int, profile: dict) -> None:
    lang = _ob_lang(profile=profile)
    st: dict = {
        "step": OB_CHANGE_12W,
        "change_12w_phase": "choice",
        "lang": lang,
        "language_code": lang,
    }
    _seed_from_profile(st, profile)
    onboarding[cid] = st


def _wants_new_cycle_reply(raw: str) -> bool:
    low = (raw or "").strip().lower()
    return any(
        w in low
        for w in (
            "новый цикл",
            "новый",
            "заново",
            "сначала",
            "с нуля",
            "помечта",
            "мечта",
            "vision",
            "12 недель заново",
        )
    )


def _wants_adjust_reply(raw: str) -> bool:
    low = (raw or "").strip().lower()
    return any(
        w in low
        for w in (
            "скоррект",
            "уточн",
            "подправ",
            "изменить",
            "поменять",
            "текущ",
            "чуть",
            "немного",
            "поправ",
        )
    )


def greeting_after_name(name: str, lang: str = "en") -> str:
    n = (name or "").strip() or _friend_word(lang)
    return f"{n}, {s('nice_to_meet', lang)}"


def vision_question_message(lang: str = "en") -> str:
    if _is_ru(lang):
        return "Что сейчас сильнее всего хочется изменить в своей жизни?"
    return "What do you most want to change in your life right now?"


def message_vision(name: str, lang: str = "en") -> str:
    """Sync fallback for callers that cannot await. Prefer message_vision_async."""
    return f"{greeting_after_name(name, lang)}\n\n{vision_question_message(lang)}"


def _profile_chatted_today(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    last = str(profile.get("last_user_message_date") or "").strip()[:10]
    if not last:
        return False
    try:
        tz = ZoneInfo(str(profile.get("timezone") or _default_timezone()))
        today = datetime.now(tz).date().isoformat()
    except Exception:
        today = date.today().isoformat()
    return last == today


def _should_skip_greeting(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    if not str(profile.get("name") or "").strip():
        return False
    return _profile_chatted_today(profile)


def _is_dont_know_streak_phrase(raw: str) -> bool:
    low = re.sub(r"[^\w\s']", " ", (raw or "").strip().lower())
    low = re.sub(r"\s+", " ", low).strip()
    if not low:
        return False
    cores = (
        "не знаю",
        "не уверена",
        "не уверен",
        "хз",
        "без понятия",
        "нечего сказать",
        "idk",
        "dont know",
        "don't know",
        "not sure",
        "no idea",
    )
    if low in cores:
        return True
    # "я не знаю" / "ну не знаю пока" — иначе streak не рос, а ответ
    # на "не знаю" повторялся одним и тем же текстом.
    if len(low.split()) <= 6 and any(c in low for c in cores):
        return True
    return (
        low.startswith("не знаю")
        or low.startswith("не уверен")
        or low.startswith("i don't know")
        or low.startswith("i dont know")
    )


def _looks_like_concrete_goal(raw: str) -> bool:
    text = (raw or "").strip()
    if len(text) < 10 or _is_dont_know_streak_phrase(text):
        return False
    low = text.lower()
    markers = (
        "хочу",
        "хотел",
        "хотела",
        "цель",
        "похуд",
        "сброс",
        "набрать",
        "заработ",
        "запуск",
        "клиент",
        "кг",
        "кило",
        "руб",
        "$",
        "долл",
        "видео",
        "проект",
        "want to",
        "i want",
        "lose",
        "earn",
        "launch",
        "goal",
    )
    return any(m in low for m in markers)


def _sphere_label(sphere: str, lang: str = "en") -> str:
    ru = _is_ru(lang)
    mapping = {
        "health": ("здоровье и тело", "health and body"),
        "money": ("деньги и карьеру", "money and career"),
        "relations": ("отношения", "relationships"),
        "creative": ("творчество и проекты", "creative work"),
        "other": ("другую тему", "something else"),
    }
    pair = mapping.get(sphere, mapping["other"])
    return pair[0] if ru else pair[1]


def _update_dont_know_streak(st: dict, raw: str) -> int:
    if _is_dont_know_streak_phrase(raw):
        streak = int(st.get("dont_know_streak") or 0) + 1
    else:
        streak = 0
    st["dont_know_streak"] = streak
    return streak


async def _claude_plain_text(
    system: str,
    user_content: str,
    model_names: list[str],
    *,
    lang: str = "en",
    fallback: str = "",
    max_tokens: int = 220,
) -> str:
    messages = [{"role": "user", "content": (user_content or "Напиши сообщение.").strip()}]

    def call() -> str:
        for mid in model_names or []:
            try:
                text = claude_generate(
                    mid,
                    _messages_with_lang(messages, lang),
                    system=_system_with_lang(system, lang),
                    max_tokens=max_tokens,
                    cache_core=False,
                ).strip()
                text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.I).strip()
                if text:
                    return text[:2000]
            except Exception as e:
                log.warning("onboarding plain text %s: %s", mid, e)
        return (fallback or "").strip()

    return await asyncio.to_thread(call)


async def say_as_friend(
    fallback: str,
    model_names: list[str],
    lang: str = "en",
    *,
    intent: str = "",
    profile: dict | None = None,
) -> str:
    fb = (fallback or "").strip()
    if not fb:
        return fb
    prev_goal = ""
    name = ""
    if isinstance(profile, dict):
        prev_goal = str(
            profile.get("main_goal") or profile.get("final_goal") or ""
        ).strip()
        name = str(profile.get("name") or "").strip()
    returning = bool(prev_goal)
    system = RETURNING_ONBOARDING_SYSTEM if returning else VARY_PHRASE_SYSTEM
    parts: list[str] = []
    if returning:
        parts.append(f"Имя: {name or '—'}")
        parts.append(f"Предыдущая цель: {prev_goal}")
    if intent:
        parts.append(f"Intent: {intent}")
    parts.append(f"Скажи это своими словами:\n{fb}")
    user = "\n".join(parts)
    out = await _claude_plain_text(
        system,
        user,
        model_names,
        lang=lang,
        fallback=fb,
        max_tokens=260,
    )
    return out or fb


async def generate_first_pain_question(
    name: str,
    model_names: list[str],
    lang: str = "en",
    *,
    profile: dict | None = None,
) -> str:
    n = (name or "").strip() or _friend_word(lang)
    if isinstance(profile, dict) and str(profile.get("name") or "").strip():
        n = str(profile.get("name") or "").strip()
    fallback = vision_question_message(lang)
    previous_goal = ""
    if isinstance(profile, dict):
        previous_goal = str(profile.get("main_goal") or "").strip()

    if previous_goal:
        system = (
            FIRST_PAIN_QUESTION_SYSTEM
            + "\n\n"
            + "ВАЖНО: Этот пользователь уже проходил онбординг раньше.\n"
            + f"Его предыдущая цель была: {previous_goal}\n"
            + "НЕ используй стандартные фразы первого знакомства.\n"
            + "Каждый раз формулируй вопрос немного по-другому —\n"
            + "как подруга которая продолжает разговор, а не бот который\n"
            + "запустил скрипт заново."
        )
        user = (
            f"Имя: {n}\nПредыдущая цель: {previous_goal}"
            if _is_ru(lang)
            else f"Name: {n}\nPrevious goal: {previous_goal}"
        )
        return await _claude_plain_text(
            system,
            user,
            model_names,
            lang=lang,
            fallback=fallback,
            max_tokens=120,
        )

    return await _claude_plain_text(
        FIRST_PAIN_QUESTION_SYSTEM,
        f"Имя пользователя: {n}" if _is_ru(lang) else f"User name: {n}",
        model_names,
        lang=lang,
        fallback=fallback,
        max_tokens=120,
    )


async def generate_dont_know_exit(
    model_names: list[str],
    lang: str = "en",
) -> str:
    if _is_ru(lang):
        fallback = (
            "Похоже, сейчас тебе самой ещё неясно — и без этого я не смогу помочь по-настоящему. "
            "Я здесь, когда будешь готова разобраться. Напиши мне тогда."
        )
    else:
        fallback = (
            "It seems things aren't clear for you yet — and without that I can't really help. "
            "I'm here when you're ready to figure it out. Write me then."
        )
    return await _claude_plain_text(
        DONT_KNOW_EXIT_SYSTEM,
        "Напиши прощание." if _is_ru(lang) else "Write the goodbye.",
        model_names,
        lang=lang,
        fallback=fallback,
        max_tokens=180,
    )


def _parse_goal_sphere(text: str) -> str:
    low = re.sub(r"[^\w\s]", " ", (text or "").strip().lower())
    low = re.sub(r"\s+", " ", low).strip()
    for key in ("health", "money", "relations", "creative", "other"):
        if key in low:
            return key
    return "other"


async def classify_goal_sphere(
    goal_text: str,
    model_names: list[str],
    lang: str = "en",
) -> str:
    g = (goal_text or "").strip()
    if not g:
        return "other"
    raw = await _claude_plain_text(
        GOAL_SPHERE_SYSTEM,
        f"Goal: {g}",
        model_names,
        lang="en",
        fallback="other",
        max_tokens=12,
    )
    return _parse_goal_sphere(raw)


async def message_vision_async(
    name: str,
    lang: str,
    model_names: list[str],
    *,
    profile: dict | None = None,
    skip_greeting: bool | None = None,
) -> tuple[str, str]:
    """Return (user-facing message, first assistant turn for vision_turns)."""
    n = (name or "").strip() or _friend_word(lang)
    skip = (
        _should_skip_greeting(profile)
        if skip_greeting is None
        else bool(skip_greeting)
    )
    question = await generate_first_pain_question(
        n, model_names, lang, profile=profile
    )
    if skip:
        return question, question
    greet_fb = greeting_after_name(n, lang)
    greet = await say_as_friend(
        greet_fb,
        model_names,
        lang,
        intent="short greeting after name",
        profile=profile,
    )
    msg = f"{greet}\n\n{question}"
    return msg, question


async def kickoff_vision_opening(
    onboarding: dict[int, dict],
    cid: int,
    name: str,
    lang: str,
    model_names: list[str],
    *,
    profile: dict | None = None,
) -> str:
    opening, first_q = await message_vision_async(
        name, lang, model_names, profile=profile
    )
    st = onboarding.get(cid) or {}
    st["vision_turns"] = [{"role": "assistant", "content": first_q[:2000]}]
    st["dont_know_streak"] = 0
    onboarding[cid] = st
    return opening


async def build_change_goal_dialog_opening(
    profile: dict,
    lang: str,
    model_names: list[str],
    *,
    mode: str,
    previous_goal: str = "",
    user_topic: str = "",
) -> tuple[str, list[dict]]:
    """Opening for new_12w / adjust after choice. Returns (message, seed turns)."""
    name = str(profile.get("name") or "").strip() or _friend_word(lang)
    prev = (previous_goal or str(profile.get("main_goal") or "")).strip()

    if mode == "adjust_12w":
        fb = change_12w_adjust_opening(prev, lang)
        text = await say_as_friend(
            fb,
            model_names,
            lang,
            intent="adjust current 12w goal, no greeting",
            profile=profile,
        )
        return text, [{"role": "assistant", "content": text[:2000]}]

    # new_12w — ask about the new goal only; never mention the previous one
    question = await generate_first_pain_question(
        name,
        model_names,
        lang,
        profile={"name": name} if name else None,
    )
    return question, [{"role": "assistant", "content": question[:2000]}]


def greeting_returning(name: str, lang: str = "en") -> str:
    n = (name or "").strip() or _friend_word(lang)
    return ob_text("greeting_returning", lang, name=n)


def _default_timezone() -> str:
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def _detect_kids_from_text(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _KIDS_HINTS)


def _note_kids_from_answer(st: dict, raw: str) -> None:
    if _detect_kids_from_text(raw):
        st["has_kids"] = True


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _questions_roughly_same(a: str, b: str) -> bool:
    na = _normalize_text(a)
    nb = _normalize_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 24 and shorter in longer:
        return True
    # Compare question tails after stripping soft openers
    def _core(s: str) -> str:
        s = re.sub(
            r"^(окей|ок|хорошо|поняла|понял|ясно|слушай|okay|ok|got it|right|so)[,.\s:—-]+",
            "",
            s,
        )
        return s.strip(" ?!.…")

    ca, cb = _core(na), _core(nb)
    return bool(ca and cb and (ca == cb or (len(ca) >= 20 and ca in cb) or (len(cb) >= 20 and cb in ca)))


def _assistant_same_question_streak(turns: list[dict]) -> int:
    """Trailing assistant replies that ask essentially the same question."""
    last = ""
    streak = 0
    for turn in reversed(turns):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        if not last:
            last = content
            streak = 1
            continue
        if _questions_roughly_same(content, last):
            streak += 1
            continue
        break
    return streak


def user_wants_onboarding_pause(raw: str) -> bool:
    low = re.sub(r"[^\w\s]", " ", (raw or "").strip().lower())
    low = re.sub(r"\s+", " ", low).strip()
    if not low:
        return False
    exact = {
        "стоп",
        "stop",
        "хватит",
        "enough",
        "потом",
        "later",
        "отмена",
        "cancel",
        "nevermind",
        "never mind",
    }
    if low in exact:
        return True
    phrases = (
        "не сейчас",
        "not now",
        "maybe later",
        "another time",
        "в другой раз",
        "давай потом",
        "давай не сейчас",
        "хватит",
        "стоп",
        "stop",
    )
    return any(p in low for p in phrases)


def onboarding_pause_message(lang: str = "en") -> str:
    if _is_ru(lang):
        return (
            "Хорошо, вернёмся к этому когда будешь готова. "
            "Просто напиши мне когда захочешь."
        )
    return (
        "Okay, we'll come back to this when you're ready. "
        "Just write me whenever you want."
    )


def _user_looks_unsure_about_goal(raw: str) -> bool:
    low = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if not low:
        return False
    markers = (
        "не знаю",
        "не уверена",
        "не уверен",
        "запутал",
        "помоги",
        "не понимаю",
        "хз",
        "без понятия",
        "нечего сказать",
        "idk",
        "i don't know",
        "i dont know",
        "dont know",
        "don't know",
        "not sure",
        "no idea",
        "help me",
        "confused",
        "stuck",
    )
    return any(m in low for m in markers)


DONT_KNOW_CLARIFY_SYSTEM = """Ты Спейс. Пользователь сказал что не знает / не уверена.
Задай ОДИН другой уточняющий вопрос про её текущую жизнь —
что занимает время и энергию, что беспокоит, чего не хватает.
Примеры тона: "Что сейчас занимает больше всего твоего времени и энергии?"
НЕ предлагай списки вариантов (здоровье/деньги/отношения).
НЕ повторяй свой предыдущий вопрос если он передан ниже.
Один вопрос, 1-2 предложения. Только текст, без JSON."""


async def generate_dont_know_clarifying_question(
    turns: list[dict],
    model_names: list[str],
    lang: str = "en",
) -> str:
    prev = _last_assistant_reply(turns)
    if _is_ru(lang):
        fallback = "Что сейчас занимает больше всего твоего времени и энергии?"
    else:
        fallback = "What takes most of your time and energy right now?"
    history = _format_dialog_history(turns[-6:], lang=lang) if turns else ""
    user = (
        f"История (кратко):\n{history}\n\n"
        f"Твой предыдущий вопрос (НЕ повторяй):\n{prev or '—'}\n\n"
        "Задай другой один уточняющий вопрос."
    )
    text = await _claude_plain_text(
        DONT_KNOW_CLARIFY_SYSTEM,
        user,
        model_names,
        lang=lang,
        fallback=fallback,
        max_tokens=120,
    )
    if prev and _questions_roughly_same(text, prev):
        text = await _claude_plain_text(
            DONT_KNOW_CLARIFY_SYSTEM,
            user
            + "\n\nКРИТИЧНО: предыдущий вопрос совпал — сформулируй СОВСЕМ другой.",
            model_names,
            lang=lang,
            fallback=fallback,
            max_tokens=120,
        )
    if prev and _questions_roughly_same(text, prev):
        return fallback
    return text or fallback


def _switch_approach_hint(lang: str = "en") -> str:
    if _is_ru(lang):
        return (
            "Ты уже задавала похожий вопрос. Смени подход полностью: "
            "задай один совсем другой уточняющий вопрос про текущую жизнь "
            "(время, энергия, что беспокоит). Без списков вариантов. "
            "Не повторяй прошлый вопрос."
        )
    return (
        "You already asked a similar question. Switch approach completely: "
        "ask one clearly different clarifying question about her current life "
        "(time, energy, what worries her). No option lists. "
        "Do not repeat the previous question."
    )


def _last_assistant_reply(turns: list[dict]) -> str:
    for turn in reversed(turns):
        if turn.get("role") == "assistant":
            return str(turn.get("content", "")).strip()
    return ""


def _bool_flag(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "да")
    return False


def _parse_json_dialog(text: str, message_key: str = "message") -> dict | None:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    reply = str(
        data.get(message_key) or data.get("reply") or data.get("message") or ""
    ).strip()
    if not reply:
        return None
    return data


def _parse_vision_dialog_json(text: str) -> dict | None:
    data = _parse_json_dialog(text)
    if not data:
        return None
    reply = str(data.get("message") or data.get("reply") or "").strip()
    if not reply:
        return None
    return {
        "message": reply,
        "ready_for_goal": _bool_flag(data.get("ready_for_goal")),
    }


def _parse_weekly_change_json(text: str) -> dict | None:
    data = _parse_json_dialog(text)
    if not data:
        return None
    reply = str(data.get("message") or data.get("reply") or "").strip()
    if not reply:
        return None
    ready = _bool_flag(data.get("ready")) or _bool_flag(data.get("goal_ready"))
    return {
        "message": reply,
        "ready": ready,
        "weekly_goal": str(data.get("weekly_goal") or data.get("goal") or "").strip(),
    }


def _parse_goal_dialog_json(text: str) -> dict | None:
    data = _parse_json_dialog(text)
    if not data:
        return None
    reply = str(data.get("message") or data.get("reply") or "").strip()
    if not reply:
        return None
    ready = _bool_flag(data.get("ready")) or _bool_flag(data.get("goal_ready"))
    return {
        "message": reply,
        "ready": ready,
        "goal": str(data.get("goal", "")).strip(),
    }


def _collect_vision_from_turns(turns: list[dict]) -> str:
    parts = [
        str(t.get("content", "")).strip()
        for t in turns
        if t.get("role") == "user" and str(t.get("content", "")).strip()
    ]
    return "\n\n".join(parts)[:4000]


def _fallback_vision_reply(turns: list[dict], lang: str = "en") -> dict:
    user_texts = [t["content"] for t in turns if t.get("role") == "user"]
    n = len(user_texts)
    if n >= 2:
        return {"message": s("vision_ready", lang), "ready_for_goal": True}
    return {
        "message": s("vision_detail_fallback", lang),
        "ready_for_goal": False,
    }


def _format_dialog_history(
    turns: list[dict], *, exclude_last: bool = False, lang: str = "en"
) -> str:
    use_turns = turns[:-1] if exclude_last and turns else turns
    lines: list[str] = []
    user_lbl = s("dialog_user", lang)
    space_lbl = s("dialog_space", lang)
    for t in use_turns:
        if t.get("role") not in ("user", "assistant"):
            continue
        content = str(t.get("content", "")).strip()
        if not content:
            continue
        label = user_lbl if t.get("role") == "user" else space_lbl
        lines.append(f"{label}: {content}")
    return "\n".join(lines)[:4000] if lines else s("dialog_start", lang)


def _fallback_goal_reply(turns: list[dict], lang: str = "en") -> dict:
    user_texts = [t["content"] for t in turns if t["role"] == "user"]
    last = (user_texts[-1] if user_texts else "").strip()
    if len(user_texts) >= 3 and len(last) >= 20:
        return {
            "message": s("goal_proposed", lang, goal=last),
            "ready": False,
            "goal": "",
        }
    return {
        "message": s("goal_detail_fallback", lang),
        "ready": False,
        "goal": "",
    }


async def _extract_name(
    user_message: str,
    model_names: list[str],
    *,
    lang: str = "en",
) -> str:
    raw = (user_message or "").strip()
    if not raw:
        return _friend_word(lang)
    prompt = NAME_EXTRACT_PROMPT.format(
        user_message=raw.replace('"', "'")[:500],
    )
    name_system = s("name_extract_system", lang)

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": _user_content_with_lang(prompt, lang)}],
                    system=_system_with_lang(name_system, lang),
                    max_tokens=60,
                    cache_core=False,
                ).strip()
                if text:
                    name = text.strip().strip('"').strip("'")[:120]
                    if name:
                        return name
            except Exception as e:
                log.warning("extract_name %s: %s", mid, e)
        parts = [p for p in raw.split() if p.strip()]
        if parts:
            return parts[-1][:120]
        return _friend_word(lang)

    return await asyncio.to_thread(call)


async def _claude_vision_dialog(
    vision_turns: list[dict],
    model_names: list[str],
    *,
    extra_user_hint: str = "",
    lang: str = "en",
    system_prompt: str | None = None,
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in vision_turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    system = system_prompt or WHY_DIG_SYSTEM

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    _messages_with_lang(messages, lang),
                    system=_system_with_lang(system, lang),
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_vision_dialog_json(text)
                if parsed:
                    return parsed
                log.warning("vision_dialog bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("vision_dialog %s: %s", mid, e, exc_info=True)
        return _fallback_vision_reply(vision_turns, lang)

    return await asyncio.to_thread(call)


def _parse_weekly_recap_json(text: str) -> dict | None:
    parsed = _parse_json_dialog(text, "message")
    if not parsed:
        return None
    return {
        "message": str(parsed.get("message") or "").strip(),
        "ready": _bool_flag(parsed.get("ready")),
    }


async def _claude_weekly_recap_dialog(
    turns: list[dict],
    model_names: list[str],
    *,
    profile: dict,
    week_number: int,
    week_context: str,
    lang: str = "en",
    extra_user_hint: str = "",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    name = str(profile.get("name") or _friend_word(lang)).strip()
    system = _system_with_lang(
        WEEKLY_RECAP_DIALOG_SYSTEM.format(
            name=name,
            weekly_goal=str(profile.get("weekly_goal") or ("не указана" if _is_ru(lang) else "not set")),
            main_goal=str(profile.get("main_goal") or ("не указана" if _is_ru(lang) else "not set")),
            week_number=max(1, int(week_number)),
            week_context=week_context or ("мало записей" if _is_ru(lang) else "few notes"),
        ),
        lang,
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    _messages_with_lang(messages, lang),
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_weekly_recap_json(text)
                if parsed:
                    return parsed
                log.warning("weekly_recap bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("weekly_recap %s: %s", mid, e, exc_info=True)
        ask = (
            "What felt like the main win this week — and what didn't work?"
            if not _is_ru(lang)
            else "Что было главной победой на этой неделе — и что не сработало?"
        )
        return {"message": ask, "ready": False}

    return await asyncio.to_thread(call)


async def _claude_change_weekly_dialog(
    turns: list[dict],
    model_names: list[str],
    *,
    main_goal: str,
    extra_user_hint: str = "",
    lang: str = "en",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    mg = main_goal or ("не указана" if _is_ru(lang) else "not specified")
    system = _system_with_lang(
        CHANGE_WEEKLY_GOAL_SYSTEM.format(main_goal=mg), lang
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    _messages_with_lang(messages, lang),
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_weekly_change_json(text)
                if parsed:
                    return parsed
                log.warning("change_weekly bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("change_weekly %s: %s", mid, e, exc_info=True)
        last_user = ""
        for t in reversed(turns):
            if t.get("role") == "user":
                last_user = str(t.get("content", "")).strip()
                break
        if len(last_user) >= 8:
            msg_saved = (
                "Saved — sounds specific."
                if not _is_ru(lang)
                else "Записала — звучит конкретно."
            )
            return {
                "message": msg_saved,
                "ready": True,
                "weekly_goal": last_user[:2000],
            }
        ask = (
            "What do you want to do this week — in one sentence?"
            if not _is_ru(lang)
            else "Что именно хочешь сделать на этой неделе — одним предложением?"
        )
        return {
            "message": ask,
            "ready": False,
            "weekly_goal": "",
        }

    return await asyncio.to_thread(call)


def _is_goal_confirm_yes(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if low in _GOAL_CONFIRM_YES or low.startswith("да"):
        return True
    return any(w in low for w in ("верно", "подходит", "соглас", "записывай", "запиши"))


def _is_goal_confirm_no(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    return any(low.startswith(w) or w in low for w in _GOAL_CONFIRM_NO)


async def _polish_goal(
    raw_goal: str,
    goal_type: str,
    model_names: list[str],
    *,
    lang: str = "en",
) -> str:
    raw = (raw_goal or "").strip()
    if not raw:
        return ""
    prompt = goal_polish_prompt_template(lang).format(
        raw_goal=raw.replace('"', "'")[:1500],
        goal_type=_goal_type_label(goal_type, lang),
    )

    polish_system = (
        "Return only the polished goal text, no quotes or explanations."
        if not _is_ru(lang)
        else "Верни только отшлифованную цель, без кавычек и пояснений."
    )

    def call() -> str:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    [{"role": "user", "content": _user_content_with_lang(prompt, lang)}],
                    system=_system_with_lang(polish_system, lang),
                    max_tokens=220,
                    cache_core=False,
                ).strip()
                if text:
                    return text.strip().strip('"').strip()[:2000]
            except Exception as e:
                log.warning("polish_goal %s: %s", mid, e)
        return raw[:2000]

    return await asyncio.to_thread(call)


async def _propose_goal_confirm(
    msg,
    st: dict,
    *,
    field: str,
    raw: str,
    goal_type: str,
    model_names: list[str],
    after: str,
) -> None:
    bot = msg.get_bot()
    async with typing_while(bot, msg.chat_id):
        polished = await _polish_goal(
            raw, goal_type, model_names, lang=_ob_lang(st)
        )
    if not polished.strip():
        polished = (raw or "").strip()[:2000]
    st["goal_confirm"] = {
        "field": field,
        "polished": polished,
        "raw": (raw or "").strip()[:2000],
        "after": after,
        "goal_type": goal_type,
    }
    lang = _ob_lang(st)
    polished_s = polished.strip()
    if goal_type == GOAL_TYPE_WEEKLY:
        confirm_msg = _weekly_confirmation_text(polished_s, lang)
    else:
        confirm_msg = _confirmation_text(polished_s, lang)
    await msg.reply_text(confirm_msg)


async def _complete_change_weekly_from_dialog(
    msg,
    context: ContextTypes.DEFAULT_TYPE,
    cid: int,
    st: dict,
    onboarding: dict[int, dict],
    user_profiles: dict[str, dict],
    weekly_goal: str,
    model_names: list[str],
) -> None:
    lang = _ob_lang(st)
    weekly = (weekly_goal or "").strip()
    from_week_start = bool(st.get("from_week_start"))
    await _finish_change_weekly(cid, weekly, user_profiles, onboarding)
    prof = user_profiles.get(str(cid)) or db.get_profile(cid) or {}
    after_cb = context.bot_data.get("after_weekly_goal_saved")
    if after_cb:
        done_msg = await after_cb(
            context.bot,
            cid,
            prof,
            model_names,
            weekly,
            from_week_start,
        )
    else:
        done_msg = ob_text("weekly_saved", lang, weekly=weekly)
    await msg.reply_text(done_msg)


async def _finish_change_weekly(
    cid: int,
    new_weekly: str,
    user_profiles: dict[str, dict],
    onboarding: dict[int, dict],
) -> str:
    weekly = new_weekly.strip()
    profile = db.update_profile(
        cid,
        {
            "weekly_goal": weekly[:2000],
            "weekly_score": 0,
        },
    )
    user_profiles[str(cid)] = profile
    onboarding.pop(cid, None)
    lang = _ob_lang(profile=profile)
    return ob_text("weekly_saved", lang, weekly=weekly)


async def _dispatch_goal_confirm_after(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cid: int,
    st: dict,
    onboarding: dict[int, dict],
    histories: dict[int, list],
    user_profiles: dict[str, dict],
    subscribers: set[int],
    model_names: list[str],
    after: str,
) -> None:
    msg = update.message
    if not msg:
        return

    if after == "main_to_weekly":
        st["step"] = OB_WEEKLY_TACTICS
        if st.get("weekly_turns"):
            return
        main = str(st.get("main_goal") or "").strip()
        lang = _ob_lang(st)
        await msg.reply_text(ob_text("main_to_weekly", lang))
        await _start_weekly_tactics_dialog(msg, st, model_names)
        return

    if after == "weekly_to_morning":
        st["step"] = OB_MORNING_TIME
        await msg.reply_text(morning_time_question(_ob_lang(st)))
        return

    if after == "finish_weekly":
        weekly = str(st.get("weekly_goal") or "").strip()
        from_week_start = bool(st.get("from_week_start"))
        await _finish_change_weekly(cid, weekly, user_profiles, onboarding)
        prof = user_profiles.get(str(cid)) or db.get_profile(cid) or {}
        after_cb = context.bot_data.get("after_weekly_goal_saved")
        if after_cb:
            done_msg = await after_cb(
                context.bot,
                cid,
                prof,
                model_names,
                weekly,
                from_week_start,
            )
        else:
            done_msg = ob_text("weekly_saved", _ob_lang(st), weekly=weekly)
        await msg.reply_text(done_msg)
        return

    if after == "finish_12w":
        done_msg = await _finish_change_12w(cid, st, user_profiles, onboarding)
        await msg.reply_text(done_msg)
        return

    if after == "finish_reengagement":
        done_msg = await _finish_reengagement(
            cid, st, user_profiles, onboarding, subscribers
        )
        await msg.reply_text(done_msg)
        return

    log.warning("unknown goal_confirm after=%s cid=%s", after, cid)


async def _finish_change_12w(
    cid: int,
    st: dict,
    user_profiles: dict[str, dict],
    onboarding: dict[int, dict],
) -> str:
    main_goal = str(st.get("main_goal") or "").strip()
    weekly_goal = str(st.get("weekly_goal") or "").strip()
    fields: dict = {
        "main_goal": main_goal[:2000],
        "weekly_goal": weekly_goal[:2000],
        "raw_goal": main_goal[:2000],
        "final_goal": main_goal[:2000],
        "current_week": 1,
        "weekly_score": 0,
    }
    vision = str(st.get("vision") or "").strip()
    if vision:
        fields["vision"] = vision[:4000]
    profile = db.update_profile(cid, fields)
    user_profiles[str(cid)] = profile
    onboarding.pop(cid, None)
    lang = _ob_lang(st, profile)
    return ob_text(
        "finish_12w",
        lang,
        main=main_goal,
        weekly=weekly_goal,
    )


def start_reengagement_goal(
    onboarding: dict[int, dict],
    cid: int,
    profile: dict,
) -> None:
    lang = _ob_lang(profile=profile)
    onboarding[cid] = {
        "step": OB_REENGAGE_GOAL,
        "reengagement": True,
        "lang": lang,
        "language_code": lang,
        "goal_turns": [],
        "weekly_turns": [],
        "main_goal": str(profile.get("main_goal") or profile.get("final_goal") or "").strip(),
    }


async def _finish_reengagement(
    cid: int,
    st: dict,
    user_profiles: dict[str, dict],
    onboarding: dict[int, dict],
    subscribers: set[int],
) -> str:
    main_goal = str(st.get("main_goal") or "").strip()
    weekly_goal = str(st.get("weekly_goal") or "").strip()
    profile = user_profiles.get(str(cid)) or db.get_profile(cid) or {}
    tz_name = str(profile.get("timezone") or "Asia/Ho_Chi_Minh")
    try:
        today_iso = datetime.now(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        today_iso = date.today().isoformat()
    fields: dict = {
        "main_goal": main_goal[:2000],
        "weekly_goal": weekly_goal[:2000],
        "raw_goal": main_goal[:2000],
        "final_goal": main_goal[:2000],
        "daily_enabled": True,
        "last_user_message_date": today_iso,
        "reengagement_sent_date": "",
    }
    db.save_subscriber(cid, True)
    subscribers.add(cid)
    profile = db.update_profile(cid, fields)
    user_profiles[str(cid)] = profile
    onboarding.pop(cid, None)
    lang = _ob_lang(st, profile)
    return ob_text(
        "reengagement_goal_saved",
        lang,
        main=main_goal,
        weekly=weekly_goal,
    )


async def _claude_goal_dialog(
    goal_turns: list[dict],
    model_names: list[str],
    *,
    vision: str = "",
    extra_user_hint: str = "",
    lang: str = "en",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in goal_turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    dialog_history = _format_dialog_history(goal_turns, exclude_last=True, lang=lang)
    vision_label = (vision or s("not_specified", lang)).strip()[:2000]
    system = _system_with_lang(
        GOAL_DIALOG_SYSTEM.format(
            vision=vision_label,
            dialog_history=dialog_history,
        ),
        lang,
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    _messages_with_lang(messages, lang),
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_goal_dialog_json(text)
                if parsed:
                    return parsed
                log.warning("goal_dialog bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("goal_dialog %s: %s", mid, e, exc_info=True)
        return _fallback_goal_reply(goal_turns, lang)

    return await asyncio.to_thread(call)


def _fallback_weekly_tactics_reply(
    turns: list[dict],
    *,
    main_goal: str,
    user_message: str,
    lang: str = "en",
) -> dict:
    last_user = (user_message or "").strip()
    default_g = ob_text("default_goal", lang)
    if not last_user:
        g = (main_goal or default_g)[:80]
        if _is_ru(lang):
            msg = (
                f"Что хочешь сделать на этой неделе чтобы приблизиться к «{g}»? "
                f"Можно: собрать первых тестировщиков / настроить оплату / "
                f"запустить первый контент. Или предложи своё."
            )
        else:
            msg = (
                f"What do you want to do this week to move toward «{g}»? "
                f"Examples: recruit first testers / set up payments / ship first content — "
                f"or suggest your own."
            )
        return {"message": msg, "ready": False, "weekly_goal": ""}
    if len(last_user) >= 12:
        if _is_ru(lang):
            msg = (
                f"Окей, как поймёшь что «{last_user[:80]}» на этой неделе выполнено?"
            )
        else:
            msg = (
                f"How will you know «{last_user[:80]}» is done by the end of this week?"
            )
        return {"message": msg, "ready": False, "weekly_goal": ""}
    return {
        "message": ob_text("weekly_dialog_fallback", lang),
        "ready": False,
        "weekly_goal": "",
    }


async def _claude_weekly_tactics_dialog(
    weekly_turns: list[dict],
    model_names: list[str],
    *,
    main_goal: str,
    user_message: str,
    extra_user_hint: str = "",
    lang: str = "en",
) -> dict:
    messages = [
        {"role": t["role"], "content": t["content"]}
        for t in weekly_turns
        if t.get("role") in ("user", "assistant") and t.get("content")
    ]
    # Claude needs at least one message — add system instruction as user message if empty
    if not messages:
        if _is_ru(lang):
            seed = (
                f"Предложи 2-3 варианта задач на первую неделю для цели: {main_goal}"
            )
        else:
            seed = (
                f"Suggest 2-3 first-week tasks for this goal: {main_goal}"
            )
        messages = [{"role": "user", "content": seed}]
    if extra_user_hint:
        messages.append({"role": "user", "content": extra_user_hint})

    dialog_history = _format_dialog_history(weekly_turns, exclude_last=True, lang=lang)
    if dialog_history == s("dialog_start", lang):
        dialog_history = ""
    mg = (main_goal or s("not_specified", lang)).strip()[:2000]
    system = _system_with_lang(
        WEEKLY_TACTICS_DIALOG_SYSTEM.format(
            main_goal=mg,
            user_message=(user_message or "").strip()[:2000] or "",
            dialog_history=dialog_history,
        ),
        lang,
    )

    def call() -> dict:
        for mid in model_names:
            try:
                text = claude_generate(
                    mid,
                    _messages_with_lang(messages, lang),
                    system=system,
                    max_tokens=400,
                    cache_core=False,
                ).strip()
                parsed = _parse_weekly_change_json(text)
                if parsed:
                    return parsed
                log.warning("weekly_tactics_dialog bad JSON model=%s raw=%s", mid, text[:400])
            except Exception as e:
                log.warning("weekly_tactics_dialog %s: %s", mid, e, exc_info=True)
        log.error(
            "weekly_tactics_dialog: all models failed, using fallback. main_goal=%s",
            (main_goal or "")[:100],
        )
        return _fallback_weekly_tactics_reply(
            weekly_turns,
            main_goal=main_goal,
            user_message=user_message,
            lang=lang,
        )

    return await asyncio.to_thread(call)


async def _start_weekly_tactics_dialog(
    msg,
    st: dict,
    model_names: list[str],
) -> None:
    bot = msg.get_bot()
    lang = _ob_lang(st)
    st["weekly_turns"] = []
    async with typing_while(bot, msg.chat_id):
        result = await _claude_weekly_tactics_dialog(
            [],
            model_names,
            main_goal=str(st.get("main_goal") or ""),
            user_message="",
            lang=lang,
        )
    reply = (
        result.get("message") or ob_text("weekly_dialog_fallback", lang)
    ).strip()
    st["weekly_turns"].append({"role": "assistant", "content": reply[:2000]})
    await msg.reply_text(reply)


def parse_time_nl(raw: str, context: str = "morning") -> str | None:
    """Parse natural-language time. context: 'morning' or 'evening'."""
    text = (raw or "").strip().lower()
    if not text:
        return None

    ctx = context if context in ("morning", "evening") else "morning"

    def fmt(h: int, mi: int = 0) -> str | None:
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
        return None

    def resolve_ambiguous(h: int, mi: int = 0) -> str | None:
        if ctx == "evening":
            if 1 <= h <= 11:
                return fmt(h + 12, mi)
            return fmt(h, mi)
        if 1 <= h <= 12:
            return fmt(h, mi)
        return fmt(h, mi)

    def resolve_marked(h: int, mi: int, marker: str | None) -> str | None:
        mk = (marker or "").strip()
        if mk in ("утра", "утром"):
            return fmt(h, mi)
        if mk in ("вечера", "вечером", "ночи", "ночью"):
            if 1 <= h <= 11:
                return fmt(h + 12, mi)
            return fmt(h, mi)
        return resolve_ambiguous(h, mi)

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return fmt(h, mi)

    m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return fmt(h, mi)

    m = re.fullmatch(r"(\d{1,2})(?:\s*[:.]\s*(\d{2}))?", text)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        return resolve_ambiguous(h, mi)

    m = re.search(
        r"(?:в\s+)?(\d{1,2})(?:\s*[:.]\s*(\d{2}))?\s*(утра|утром|вечера|вечером|ночи|ночью|час|часов)?",
        text,
    )
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        marker = m.group(3)
        if marker in ("утра", "утром", "вечера", "вечером", "ночи", "ночью"):
            return resolve_marked(h, mi, marker)
        return resolve_ambiguous(h, mi)

    for word, hour in _WORD_HOURS.items():
        if re.search(rf"\b{word}\b", text):
            mi = 30 if "половин" in text else 0
            marker = None
            if re.search(r"(утра|утром)", text):
                marker = "утра"
            elif re.search(r"(вечера|вечером|ночи|ночью)", text):
                marker = "вечера"
            if "половин" in text and "девят" in text:
                return "09:30"
            if marker:
                return resolve_marked(hour, mi, marker)
            return resolve_ambiguous(hour, mi)

    if "полдевят" in text or "пол 9" in text:
        return "08:30"
    if "полдесят" in text or "пол 10" in text:
        return "09:30"

    return None


def _is_weekly_goal_agreement(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if low in _WEEKLY_GOAL_AGREE or low.startswith("да"):
        return True
    return any(w in low for w in ("подходит", "давай", "соглас", "окей", "беру", "возьму"))


def _is_weekly_goal_disagreement(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    return any(low.startswith(w) or w in low for w in _WEEKLY_GOAL_DISAGREE)


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
    if _is_weekly_goal_agreement(raw) and parts:
        return parts[0][:2000]
    return text[:2000]


def looks_like_time_update_request(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if not low:
        return False
    if any(
        p in low
        for p in (
            "обновить время",
            "изменить время",
            "поменять время",
            "сменить время",
            "утреннее время",
            "вечернее время",
            "время утр",
            "время вечер",
            "поменять утро",
            "поменять вечер",
        )
    ):
        return True
    if "время" in low and any(
        v in low for v in ("обнов", "измен", "помен", "смен", "настро", "постав")
    ):
        return True
    if ("утр" in low or "вечер" in low) and any(
        v in low for v in ("обнов", "измен", "помен", "смен", "время")
    ):
        return True
    return False


def _mini_app_reply_markup(
    context: ContextTypes.DEFAULT_TYPE,
    lang: str = "en",
    cid: int | None = None,
) -> InlineKeyboardMarkup:
    base = str(context.bot_data.get("mini_app_url") or os.getenv("MINI_APP_URL") or "").strip()
    if not base:
        base = "https://spicespace-production.up.railway.app/webapp"
    if cid is not None:
        url = mini_app_webapp_url(base, cid, lang)
    else:
        root = base.rstrip("/")
        if not root.endswith("/webapp"):
            root = f"{root}/webapp"
        lang_q = "ru" if _is_ru(lang) else "en"
        url = f"{root}/?lang={lang_q}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                open_spicespace_button_label(lang),
                web_app=WebAppInfo(url=url),
            )
        ],
    ])


async def _reply_time_update_via_miniapp(
    msg,
    context: ContextTypes.DEFAULT_TYPE,
    lang: str = "en",
) -> None:
    cid = msg.chat_id if msg else None
    await msg.reply_text(
        ob_text("time_update_miniapp", lang),
        reply_markup=_mini_app_reply_markup(
            context, lang, cid=cid if cid is not None else None
        ),
    )


def looks_like_restart_onboarding(raw: str) -> bool:
    if looks_like_time_update_request(raw):
        return False
    low = (raw or "").strip().lower()
    return any(
        w in low
        for w in (
            "обнов",
            "обновить",
            "заново",
            "сначала",
            "профиль",
            "перезап",
            "изменить профиль",
            "новый профиль",
            "цели",
            "цель",
            "настроить",
            "онбординг",
            "restart",
            "setup",
            "goals",
        )
    )


def looks_like_just_chat(raw: str) -> bool:
    low = (raw or "").strip().lower()
    if looks_like_restart_onboarding(raw):
        return False
    return any(
        w in low
        for w in (
            "поговор",
            "просто",
            "давай",
            "не надо",
            "не хочу обнов",
            "продолж",
            "поболта",
        )
    )


def profile_has_main_goal(prof: dict | None) -> bool:
    if not isinstance(prof, dict):
        return False
    goal = str(
        prof.get("main_goal")
        or prof.get("final_goal")
        or prof.get("raw_goal")
        or ""
    ).strip()
    return bool(goal)


def profile_onboarding_complete(prof: dict | None) -> bool:
    """True only after full flow: name, 12w goal, week 1 goal, cycle start (times saved)."""
    if not isinstance(prof, dict):
        return False
    if not str(prof.get("name") or "").strip():
        return False
    if not profile_has_main_goal(prof):
        return False
    if not str(prof.get("weekly_goal") or "").strip():
        return False
    if not str(prof.get("cycle_start_date") or "").strip():
        return False
    return True


def start_resume_incomplete_onboarding(
    onboarding: dict[int, dict],
    cid: int,
    prof: dict,
    lang: str = "en",
) -> str:
    """
    Resume setup for a partial profile. Returns resume kind:
    vision | weekly | morning | complete
    """
    lc = str(lang or prof.get("language_code") or "en")
    name = str(prof.get("name") or "").strip()

    if not profile_has_main_goal(prof):
        start_reonboarding(onboarding, cid, name, lc)
        return "vision"

    if not str(prof.get("weekly_goal") or "").strip():
        onboarding[cid] = {
            "step": OB_WEEKLY_TACTICS,
            "name": name,
            "main_goal": str(
                prof.get("main_goal")
                or prof.get("final_goal")
                or prof.get("raw_goal")
                or ""
            ).strip()[:2000],
            "vision": str(prof.get("vision") or "").strip()[:4000],
            "weekly_turns": [],
            "lang": lc,
            "language_code": lc,
            "last_activity_at": datetime.now(),
            "reminder_sent": False,
        }
        return "weekly"

    if not str(prof.get("cycle_start_date") or "").strip():
        onboarding[cid] = {
            "step": OB_MORNING_TIME,
            "name": name,
            "main_goal": str(prof.get("main_goal") or "").strip()[:2000],
            "weekly_goal": str(prof.get("weekly_goal") or "").strip()[:2000],
            "vision": str(prof.get("vision") or "").strip()[:4000],
            "lang": lc,
            "language_code": lc,
            "last_activity_at": datetime.now(),
            "reminder_sent": False,
        }
        return "morning"

    return "complete"


def persist_profile(cid: int, st: dict, model_names: list[str]) -> dict:
    morning = str(st.get("morning_time", "09:30"))
    evening = str(st.get("evening_time", "21:00"))
    profile = {
        "name": str(st.get("name", "")).strip()
        or _friend_word(str(st.get("language_code") or "en")),
        "vision": str(st.get("vision", "")).strip()[:4000],
        "main_goal": str(st.get("main_goal", "")).strip()[:2000],
        "morning_time": morning,
        "evening_time": evening,
        "daily_time": morning,
        "timezone": str(st.get("timezone") or _default_timezone()),
        "daily_enabled": True,
        "last_morning_sent_date": None,
        "last_evening_sent_date": None,
        "last_daily_sent_date": None,
        "has_kids": st.get("has_kids"),
        "raw_goal": str(st.get("main_goal", "")).strip()[:2000],
        "final_goal": str(st.get("main_goal", "")).strip()[:2000],
        "goal_type": "qualitative",
        "goal_signals": [],
        "streak": int(st.get("streak") or 0),
        "weekly_score": 0,
        "completed_tasks": [],
        "missed_tasks": [],
        "current_week": 1,
        "weekly_goal": str(st.get("weekly_goal", "")).strip()[:2000],
        "time_per_day": str(st.get("time_per_day") or "30 минут").strip()[:200],
        "language_code": str(st.get("language_code") or "en")[:16],
    }
    try:
        tz = ZoneInfo(str(profile.get("timezone") or _default_timezone()))
    except Exception:
        tz = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
    profile["cycle_start_date"] = datetime.now(tz).date().isoformat()
    profile["trial_start_date"] = profile["cycle_start_date"]
    db.upsert_profile(cid, profile)
    db.save_subscriber(cid, True)
    save_onboarding_summary(cid, profile, model_names)
    fresh = db.get_profile(cid)
    return fresh if isinstance(fresh, dict) else profile


def touch_onboarding_activity(st: dict) -> None:
    st["last_activity_at"] = datetime.now()


def start_new_onboarding(
    onboarding: dict[int, dict], cid: int, lang: str = "en"
) -> None:
    lc = str(lang or "en")
    onboarding[cid] = {
        "step": OB_NAME,
        "lang": lc,
        "language_code": lc,
        "last_activity_at": datetime.now(),
        "reminder_sent": False,
    }


def start_returning_choice(
    onboarding: dict[int, dict], cid: int, lang: str = "en"
) -> None:
    lc = str(lang or "en")
    onboarding[cid] = {"step": OB_RETURNING, "lang": lc, "language_code": lc}


def start_reonboarding(
    onboarding: dict[int, dict], cid: int, name: str, lang: str = "en"
) -> None:
    lc = str(lang or "en")
    onboarding[cid] = {
        "step": OB_VISION_DIALOG,
        "name": name,
        "vision_turns": [],
        "lang": lc,
        "language_code": lc,
    }


async def _complete_onboarding(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cid: int,
    st: dict,
    onboarding: dict[int, dict],
    histories: dict[int, list],
    user_profiles: dict[str, dict],
    subscribers: set[int],
    model_names: list[str],
) -> None:
    msg = update.message
    if not msg:
        return

    profile = await asyncio.to_thread(persist_profile, cid, st, model_names)
    onboarding.pop(cid, None)
    user_profiles[str(cid)] = profile
    subscribers.add(cid)

    if not profile.get("cycle_start_date"):
        try:
            tz = ZoneInfo(str(profile.get("timezone") or _default_timezone()))
        except Exception:
            tz = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
        today_iso = datetime.now(tz).date().isoformat()
        profile["cycle_start_date"] = today_iso
        db.update_profile(cid, {"cycle_start_date": today_iso})
        if not profile.get("trial_start_date"):
            profile["trial_start_date"] = today_iso
            db.update_profile(cid, {"trial_start_date": today_iso})

    name = profile.get("name", "")
    mt = profile.get("morning_time", "09:30")
    et = profile.get("evening_time", "21:00")
    main_goal = str(profile.get("main_goal", ""))
    weekly_goal = str(profile.get("weekly_goal", ""))
    lang = _ob_lang(st, profile)

    histories[cid] = [
        {
            "role": "user",
            "parts": [
                s(
                    "history_onboarding",
                    lang,
                    name=name,
                    main=main_goal,
                    weekly=weekly_goal,
                    mt=mt,
                    et=et,
                )
            ],
        }
    ]

    progress_kb = None
    fn = context.bot_data.get("progress_reply_keyboard")
    if callable(fn):
        progress_kb = fn()

    await msg.reply_text(
        ob_text(
            "onboarding_complete",
            lang,
            main_goal=main_goal,
            weekly_goal=weekly_goal,
            mt=mt,
            et=et,
        ),
        reply_markup=progress_kb,
    )

    lang_note = (
        ""
        if _is_ru(lang)
        else "\nUser speaks English — write the message in English.\n"
    )
    first_msg_prompt = f"""Ты — Спейс. Онбординг только что завершился.{lang_note}
Пользователь поставила цель на 12 недель: {st.get('main_goal', '')}
Цель первой недели: {st.get('weekly_goal', '')}

Напиши одно живое сообщение — продолжи разговор как подруга.
Не повторяй цели — она их только что видела.
Задай один конкретный вопрос про первую неделю — что уже сделала, с кого начнёшь, что мешает.
Тон: тёплый, живой, без коуч-языка. 1-2 предложения максимум.
ЗАПРЕЩЕНО: приветствия ("Привет", "Доброе утро"), обращение по имени, "рада познакомиться".
ЗАПРЕЩЕНО: "Удачи!", "Увидимся", прощания, markdown."""

    def get_first_msg() -> str:
        first_system = _system_with_lang(
            "You are Space. Write one short Telegram message after onboarding.",
            lang,
        )
        for mid in model_names:
            try:
                return claude_generate(
                    mid,
                    [
                        {
                            "role": "user",
                            "content": _user_content_with_lang(first_msg_prompt, lang),
                        }
                    ],
                    system=first_system,
                    max_tokens=150,
                    cache_core=False,
                ).strip()
            except Exception:
                pass
        return ob_text("first_msg_fallback", lang)

    await asyncio.sleep(1)
    webapp_base = os.getenv(
        "WEBAPP_URL", "https://spicespace-production.up.railway.app/webapp"
    )
    webapp_url = mini_app_webapp_url(webapp_base, cid, lang)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    open_spicespace_button_label(lang, prefix="✦ "),
                    web_app=WebAppInfo(url=webapp_url),
                )
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=cid,
        text=s("open_miniapp", lang),
        reply_markup=keyboard,
    )
    await asyncio.sleep(1)
    async with typing_while(context.bot, cid):
        first_msg = await asyncio.to_thread(get_first_msg)
    await msg.reply_text(first_msg)


async def handle_returning_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
    onboarding: dict[int, dict],
    user_profiles: dict[str, dict],
) -> bool:
    cid = update.effective_chat.id
    msg = update.message
    if not msg:
        return True

    st = onboarding.get(cid) or {}
    if int(st.get("step") or 0) != OB_RETURNING:
        return False

    prof = user_profiles.get(str(cid)) or {}
    lang = _ob_lang(st, prof)
    name = str(prof.get("name", "")).strip() or _friend_word(lang)

    if looks_like_time_update_request(raw):
        onboarding.pop(cid, None)
        await _reply_time_update_via_miniapp(msg, context, lang)
        return True

    if looks_like_restart_onboarding(raw):
        start_reonboarding(onboarding, cid, name, lang)
        model_names = context.bot_data.get("claude_model_names") or []
        opening, first_q = await message_vision_async(
            name, lang, model_names, profile=prof
        )
        onboarding[cid]["vision_turns"] = [
            {"role": "assistant", "content": first_q[:2000]}
        ]
        await msg.reply_text(opening)
        return True

    if looks_like_just_chat(raw):
        if not profile_onboarding_complete(prof):
            hint = await say_as_friend(
                ob_text("returning_hint", lang),
                context.bot_data.get("claude_model_names") or [],
                lang,
                intent="returning hint",
                profile=prof,
            )
            await flow_reply_text(msg, hint)
            return True
        onboarding.pop(cid, None)
        chat_msg = await say_as_friend(
            ob_text("returning_just_chat", lang),
            context.bot_data.get("claude_model_names") or [],
            lang,
            intent="just chat",
            profile=prof,
        )
        await msg.reply_text(chat_msg)
        return True

    hint = await say_as_friend(
        ob_text("returning_hint", lang),
        context.bot_data.get("claude_model_names") or [],
        lang,
        intent="returning hint",
        profile=prof,
    )
    await flow_reply_text(msg, hint)
    return True


async def handle_onboarding_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
    onboarding: dict[int, dict],
    histories: dict[int, list],
    user_profiles: dict[str, dict],
    subscribers: set[int],
) -> None:
    cid = update.effective_chat.id
    msg = update.message
    if not msg:
        return

    if await handle_returning_choice(update, context, raw, onboarding, user_profiles):
        return

    st = onboarding.setdefault(cid, {"step": OB_NAME})
    if update.effective_user and not st.get("lang") and not st.get("language_code"):
        lc = update.effective_user.language_code or "en"
        st["lang"] = lc
        st["language_code"] = lc
    lang = _ob_lang(st, user_profiles.get(str(cid)))
    st["lang"] = lang
    st["language_code"] = lang
    touch_onboarding_activity(st)
    step = int(st.get("step") or OB_NAME)
    _note_kids_from_answer(st, raw)
    model_names = context.bot_data.get("claude_model_names") or []

    if user_wants_onboarding_pause(raw) and step in (
        OB_NAME,
        OB_VISION_DIALOG,
        OB_GOAL_DIALOG,
        OB_GOAL_12W,
        OB_WEEKLY_TACTICS,
        OB_MORNING_TIME,
        OB_EVENING_TIME,
        OB_CHANGE_12W,
        OB_REENGAGE_GOAL,
    ):
        onboarding.pop(cid, None)
        pause_msg = onboarding_pause_message(lang)
        await msg.reply_text(pause_msg)
        return

    if st.get("goal_confirm"):
        confirm = st["goal_confirm"]
        if _is_goal_confirm_yes(raw):
            field = str(confirm.get("field") or "weekly_goal")
            st[field] = str(confirm.get("polished") or "")[:2000]
            after = str(confirm.get("after") or "")
            st.pop("goal_confirm", None)
            await _dispatch_goal_confirm_after(
                update,
                context,
                cid,
                st,
                onboarding,
                histories,
                user_profiles,
                subscribers,
                model_names,
                after,
            )
            return
        if _is_goal_confirm_no(raw):
            st.pop("goal_confirm", None)
            key = (
                "goal_rewrite_12w"
                if confirm.get("goal_type") == GOAL_TYPE_12W
                else "goal_rewrite_weekly"
            )
            rewrite = await say_as_friend(
                ob_text(key, lang),
                model_names,
                lang,
                intent="ask to rewrite goal",
                profile=user_profiles.get(str(cid)),
            )
            await msg.reply_text(rewrite)
            return
        confirm_hint = await say_as_friend(
            ob_text("goal_confirm_yes_no", lang),
            model_names,
            lang,
            intent="ask yes or no on goal",
            profile=user_profiles.get(str(cid)),
        )
        await flow_reply_text(msg, confirm_hint)
        return

    if step == OB_WEEKLY_RECAP:
        onboarding.pop(cid, None)
        hint = (
            "Итоги недели теперь приходят одним сообщением от меня. "
            "Если пропустила — напиши /weektest recap"
            if _is_ru(lang)
            else "Week recap is now one message from me. If you missed it — /weektest recap"
        )
        await msg.reply_text(hint)
        return

    if step == OB_CHANGE_WEEKLY:
        turns = st.setdefault("weekly_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        prev_reply = _last_assistant_reply(turns)
        wt_hint = (
            "Do not repeat your last reply. Use what the user wrote. "
            "ready=true ONLY if user clearly said yes/да after your «Right?». "
            "Otherwise propose 2-3 options or one «Записываю: X. Верно?» with ready=false."
            if not _is_ru(lang)
            else (
                "Не повторяй прошлый ответ. Учти что написала пользователь. "
                "ready=true ТОЛЬКО если после твоего «Верно?» она явно написала да/верно. "
                "Иначе предложи 2-3 варианта или одно «Записываю: X. Верно?» с ready=false."
            )
        )
        async with typing_while(context.bot, cid):
            result = await _claude_weekly_tactics_dialog(
                turns,
                model_names,
                main_goal=str(st.get("main_goal") or ""),
                user_message=raw,
                lang=lang,
            )
            reply = (
                result.get("message") or ob_text("weekly_dialog_fallback", lang)
            ).strip()

            if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
                result = await _claude_weekly_tactics_dialog(
                    turns,
                    model_names,
                    main_goal=str(st.get("main_goal") or ""),
                    user_message=raw,
                    extra_user_hint=wt_hint,
                    lang=lang,
                )
                reply = (result.get("message") or "").strip() or reply

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready") and result.get("weekly_goal"):
            await _complete_change_weekly_from_dialog(
                msg,
                context,
                cid,
                st,
                onboarding,
                user_profiles,
                str(result["weekly_goal"]),
                model_names,
            )
        else:
            await msg.reply_text(reply)
        return

    if step == OB_CHANGE_12W:
        phase = str(st.get("change_12w_phase") or "choice")
        if phase == "choice":
            return
        await msg.reply_text(ob_text("change_12w_broken", lang))
        return

    if step == OB_NAME:
        async with typing_while(context.bot, cid):
            name = (
                await _extract_name(raw, model_names, lang=lang)
            ).strip()[:120] or _friend_word(lang)
            opening, first_q = await message_vision_async(
                name,
                lang,
                model_names,
                profile=user_profiles.get(str(cid)),
                skip_greeting=False,
            )
            privacy = await say_as_friend(
                ob_text("vision_privacy", lang),
                model_names,
                lang,
                intent="privacy note",
                profile=user_profiles.get(str(cid)),
            )
        st["name"] = name
        st["step"] = OB_VISION_DIALOG
        st["vision_turns"] = [{"role": "assistant", "content": first_q[:2000]}]
        st["dont_know_streak"] = 0
        await msg.reply_text(opening)
        await asyncio.sleep(1)
        await msg.reply_text(privacy)
        return

    if step == OB_VISION_DIALOG:
        turns = st.setdefault("vision_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        streak = _update_dont_know_streak(st, raw)
        if streak >= 3:
            async with typing_while(context.bot, cid):
                bye = await generate_dont_know_exit(model_names, lang)
            onboarding.pop(cid, None)
            await msg.reply_text(bye)
            return

        if _user_looks_unsure_about_goal(raw) and streak < 3:
            async with typing_while(context.bot, cid):
                reply = await generate_dont_know_clarifying_question(
                    turns, model_names, lang
                )
            prev_reply = _last_assistant_reply(turns)
            if prev_reply and _questions_roughly_same(reply, prev_reply):
                reply = (
                    "А если без цели — что в жизни сейчас сильнее всего напрягает или тянет?"
                    if _is_ru(lang)
                    else "Forget the goal label — what in life feels most heavy or pulling right now?"
                )
            turns.append({"role": "assistant", "content": reply[:2000]})
            await msg.reply_text(reply)
            return

        prev_reply = _last_assistant_reply(turns)
        same_streak = _assistant_same_question_streak(turns)
        dig_hint = ""
        if _looks_like_concrete_goal(raw):
            dig_hint = (
                "User already named a concrete goal. Dig into WHY — "
                "ask what changes emotionally when it happens. ready_for_goal=false until essence is clear."
                if not _is_ru(lang)
                else (
                    "Пользователь уже назвал конкретную цель. Копай ЗАЧЕМ — "
                    "что изменится эмоционально когда это случится. ready_for_goal=false пока суть не ясна."
                )
            )
        vis_hint = dig_hint or _switch_approach_hint(lang)
        async with typing_while(context.bot, cid):
            if same_streak >= 2 or dig_hint:
                result = await _claude_vision_dialog(
                    turns,
                    model_names,
                    extra_user_hint=vis_hint,
                    lang=lang,
                )
            else:
                result = await _claude_vision_dialog(turns, model_names, lang=lang)
            reply = (result.get("message") or ob_text("vision_fallback", lang)).strip()

            if prev_reply and _questions_roughly_same(reply, prev_reply):
                result = await _claude_vision_dialog(
                    turns,
                    model_names,
                    extra_user_hint=_switch_approach_hint(lang),
                    lang=lang,
                )
                reply = (result.get("message") or "").strip() or reply
            if prev_reply and _questions_roughly_same(reply, prev_reply):
                reply = await generate_dont_know_clarifying_question(
                    turns, model_names, lang
                )
                result = {"message": reply, "ready_for_goal": False}

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready_for_goal"):
            st["vision"] = _collect_vision_from_turns(turns)
            st["step"] = OB_GOAL_DIALOG
            st["goal_turns"] = []
            st["dont_know_streak"] = 0
            await msg.reply_text(reply)
        else:
            await msg.reply_text(reply)
        return

    if step == OB_REENGAGE_GOAL:
        st["goal_turns"] = [{"role": "user", "content": raw.strip()[:2000]}]
        st["step"] = OB_GOAL_DIALOG
        st["dont_know_streak"] = 0
        step = OB_GOAL_DIALOG

    if step == OB_GOAL_DIALOG:
        turns = st.setdefault("goal_turns", [])
        if not turns or turns[-1].get("role") != "user":
            turns.append({"role": "user", "content": raw.strip()[:2000]})

        streak = _update_dont_know_streak(st, raw)
        if streak >= 3:
            async with typing_while(context.bot, cid):
                bye = await generate_dont_know_exit(model_names, lang)
            onboarding.pop(cid, None)
            await msg.reply_text(bye)
            return

        if _user_looks_unsure_about_goal(raw) and streak < 3:
            async with typing_while(context.bot, cid):
                reply = await generate_dont_know_clarifying_question(
                    turns, model_names, lang
                )
            prev_reply = _last_assistant_reply(turns)
            if prev_reply and _questions_roughly_same(reply, prev_reply):
                reply = (
                    "А если без цели — что в жизни сейчас сильнее всего напрягает или тянет?"
                    if _is_ru(lang)
                    else "Forget the goal label — what in life feels most heavy or pulling right now?"
                )
            turns.append({"role": "assistant", "content": reply[:2000]})
            await msg.reply_text(reply)
            return

        prev_reply = _last_assistant_reply(turns)
        same_streak = _assistant_same_question_streak(turns)
        goal_hint = _switch_approach_hint(lang)
        why_extra = (
            "Keep digging for emotional why before locking the goal wording. "
            "ready=true only after user confirms a concrete goal."
            if not _is_ru(lang)
            else (
                "Продолжай копать эмоциональное зачем до фиксации формулировки. "
                "ready=true только после подтверждения конкретной цели."
            )
        )
        async with typing_while(context.bot, cid):
            extra = goal_hint if same_streak >= 2 else why_extra
            result = await _claude_goal_dialog(
                turns,
                model_names,
                vision=str(st.get("vision") or ""),
                extra_user_hint=extra if (same_streak >= 2 or len(turns) <= 4) else "",
                lang=lang,
            )
            reply = (result.get("message") or ob_text("goal_fallback", lang)).strip()

            if prev_reply and _questions_roughly_same(reply, prev_reply):
                result = await _claude_goal_dialog(
                    turns,
                    model_names,
                    vision=str(st.get("vision") or ""),
                    extra_user_hint=goal_hint,
                    lang=lang,
                )
                reply = (result.get("message") or "").strip() or reply
            if prev_reply and _questions_roughly_same(reply, prev_reply):
                reply = await generate_dont_know_clarifying_question(
                    turns, model_names, lang
                )
                result = {"message": reply, "ready": False, "goal": ""}

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready") and result.get("goal"):
            await _propose_goal_confirm(
                msg,
                st,
                field="main_goal",
                raw=result["goal"],
                goal_type=GOAL_TYPE_12W,
                model_names=model_names,
                after="main_to_weekly",
            )
        else:
            await msg.reply_text(reply)
        return

    async def _complete_weekly_tactics_pick(weekly_goal: str) -> None:
        if st.get("reengagement"):
            after = "finish_reengagement"
        else:
            mode = str(st.get("change_mode") or "")
            after = (
                "finish_12w"
                if mode in ("adjust_12w", "new_12w")
                else "weekly_to_morning"
            )
        await _propose_goal_confirm(
            msg,
            st,
            field="weekly_goal",
            raw=weekly_goal,
            goal_type=GOAL_TYPE_WEEKLY,
            model_names=model_names,
            after=after,
        )

    if step == OB_MORNING_TIME:
        parsed = parse_time_nl(raw, "morning")
        if not parsed:
            await msg.reply_text(ob_text("time_morning_unclear", lang))
            return
        st["morning_time"] = parsed
        st["step"] = OB_EVENING_TIME
        await msg.reply_text(evening_time_question(lang))
        return

    if step == OB_EVENING_TIME:
        parsed = parse_time_nl(raw, "evening")
        if not parsed:
            await msg.reply_text(ob_text("time_evening_unclear", lang))
            return
        st["evening_time"] = parsed
        await _complete_onboarding(
            update,
            context,
            cid,
            st,
            onboarding,
            histories,
            user_profiles,
            subscribers,
            model_names,
        )
        return

    if step == OB_WEEKLY_TACTICS:
        turns = st.setdefault("weekly_turns", [])
        turns.append({"role": "user", "content": raw.strip()[:2000]})

        prev_reply = _last_assistant_reply(turns)
        wt_hint = (
            "Do not repeat your last reply. Use what the user wrote. "
            "If they rejected your options — work only with their wording."
            if not _is_ru(lang)
            else (
                "Не повторяй прошлый ответ. Учти что написал пользователь. "
                "Если отверг твои варианты — работай только с её формулировкой."
            )
        )
        async with typing_while(context.bot, cid):
            result = await _claude_weekly_tactics_dialog(
                turns,
                model_names,
                main_goal=str(st.get("main_goal") or ""),
                user_message=raw,
                lang=lang,
            )
            reply = (
                result.get("message") or ob_text("weekly_dialog_fallback", lang)
            ).strip()

            if prev_reply and _normalize_text(reply) == _normalize_text(prev_reply):
                result = await _claude_weekly_tactics_dialog(
                    turns,
                    model_names,
                    main_goal=str(st.get("main_goal") or ""),
                    user_message=raw,
                    extra_user_hint=wt_hint,
                    lang=lang,
                )
                reply = (result.get("message") or "").strip() or reply

        turns.append({"role": "assistant", "content": reply[:2000]})

        if result.get("ready") and result.get("weekly_goal"):
            await _complete_weekly_tactics_pick(result["weekly_goal"])
        else:
            await msg.reply_text(reply)
        return

    await msg.reply_text(ob_text("something_wrong", lang))
