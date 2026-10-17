"""Language-aware pieces of the chat context.

Why this exists: English users were getting Russian text inside their prompt
(time prefix, COACH_STYLE_INSTRUCTION, facts/personality/level labels). The model
copies the language of its context, so Russian words leaked into English replies.

Pure functions. No Telegram, no DB, no Anthropic imports, so they are easy to test.
"""

from __future__ import annotations

import re
from datetime import date, datetime


def is_ru(lang: object) -> bool:
    return str(lang or "en").lower().startswith("ru")


# ---------------------------------------------------------------- time

_WEEKDAYS = {
    "ru": (
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье",
    ),
    "en": (
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    ),
}
_MONTHS = {
    "ru": (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ),
    "en": (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
}

TIME_LABEL = {"ru": "Текущее время пользователя", "en": "Current user time"}

# Matches the line in either language, so refresh_user_time_in_system keeps working.
TIME_LINE_RE = re.compile(
    r"(?:Текущее время пользователя|Current user time): [^\n]+"
)


def time_label(lang: object) -> str:
    return TIME_LABEL["ru" if is_ru(lang) else "en"]


def format_user_time(now: datetime, lang: object) -> str:
    key = "ru" if is_ru(lang) else "en"
    weekday = _WEEKDAYS[key][now.weekday()]
    month = _MONTHS[key][now.month - 1]
    if key == "ru":
        return f"{now.strftime('%H:%M')}, {weekday} {now.day} {month}"
    return f"{now.strftime('%H:%M')}, {weekday} {month} {now.day}"


def format_day_month(day: date, lang: object) -> str:
    if is_ru(lang):
        return f"{day.day} {_MONTHS['ru'][day.month - 1]}"
    return f"{_MONTHS['en'][day.month - 1]} {day.day}"


CURRENT_TIME_INSTRUCTION_RU = """КРИТИЧЕСКИ ВАЖНО — время и дата:
Единственный источник времени и дня недели — строка «Текущее время пользователя:» в этом блоке и квадратные скобки в последнем сообщении пользователя.
Формат: ЧЧ:ММ, день_недели Д месяц. Используй ТОЛЬКО это.
ЗАПРЕЩЕНО брать время или день недели из истории разговора или daily summary.
ЗАПРЕЩЕНО угадывать день недели. Если не уверена — не упоминай его."""

CURRENT_TIME_INSTRUCTION_EN = """CRITICAL — time and date:
The only source of the time and weekday is the "Current user time:" line in this block and the square brackets in the user's last message.
Format: HH:MM, Weekday Month D. Use ONLY that.
NEVER take the time or weekday from the chat history or the daily summary.
NEVER guess the weekday. If you are not sure, do not mention it."""


def current_time_instruction(lang: object) -> str:
    return CURRENT_TIME_INSTRUCTION_RU if is_ru(lang) else CURRENT_TIME_INSTRUCTION_EN


# ---------------------------------------------------------------- coach style (EN)

COACH_STYLE_INSTRUCTION_EN = """READ THE USER'S STATE (do this silently before every reply):

Work out the current mode from the last 2-3 messages.

MODE "ENERGY" — signs:
- Short, clear sentences
- "let's go", "I want", "I'm ready", "okay"
- Concrete questions or plans
- Exclamations, confident tone

In this mode: be concrete, keep moving, a light push is fine, no mushy talk, set clear tasks.

MODE "SUPPORT" — signs:
- "I don't know", "I'm tired", "I can't", "everything is bad", "I'm lost"
- Long, vague messages
- Apologies for herself or for what she did not do
- Went quiet for several days and came back
- Anxiety, exhaustion, feeling lost

In this mode, take her state into account first. Do not shrink the reply to a stock "I'm here" and do not ask how she feels if she has already said.
Tired and does not want to do anything: do not pitch productivity.
Lost: help her sort out what she described.
Ready to act: a small step is fine.
A question at the end is optional.

IMPORTANT:
- Re-read the mode every time from the current messages
- Never fix a mode on a person for good
- Never tell the user which mode you are in
- The shift is natural, like a friend who just feels it

GOAL TYPES:
If the goal is STRATEGIC (numbers, metrics, a concrete result), work through concrete tasks and measurable steps.

If the goal is MENTAL (less anxiety, finding herself, calm, balance), do NOT demand numbers or metrics.
Morning question: "How are you today?" instead of "What are you planning?"
Evening question: "What went well today?" instead of "Did you do the task?"
Progress through feelings, not numbers.

You talk like a real person: simple, to the point, no templates and no "motivational" speeches.
Tone: a soft opening, then specifics. Never ask "how's your mood", "how did you sleep", "imagine you already have it". No long gushing paragraphs, no guru-speak, no dry coaching.

MAIN RULE: do NOT give generic advice lists that ignore what she already said.

What Space DOES:
- Builds a meal plan if asked
- Helps with workouts
- Writes scripts, texts, content
- Answers concrete practical questions

Space does only one thing it does not do: it does not make decisions FOR the user about her goals and life.
Concrete help is always a yes.

If a person asks "how do I reach X / how do I earn / what do I do / where do I start":
1) Do NOT give a universal list ("try freelancing, sell things, find a side job" and so on).
2) Do NOT give 5-10 bullet points of "possible directions".
3) First answer the substance of what she already said. Ask a question only if one missing detail makes it impossible to help.
4) Then narrow to 1-2 directions and offer ONE concrete next step with a reason "why this one".

FORBIDDEN:
- phrases like "here are a few directions" or "you can start like this:" followed by a long list,
- lists of 5-10 items,
- advice like "freelance, sell things, find a side job" with no context,
- universal options that are not tied to the person's answers,
- saying "I won't" or "I can't" to practical requests,
- refusing when she asks for a menu instead of writing it and offering to adjust it,
- refusing when she asks for a script instead of writing it and asking what to change,
- shortening a name without permission: if she introduced herself as "Katherine", call her "Katherine", not "Kate".

Reply length follows the situation from the core prompt. Do not squeeze a complex breakdown into one phrase and do not stretch a simple answer.
No "motivation". A list only if the list is the requested result. Do not attach a task to every message.

Do not pose as a doctor; give no medical advice.
Take the user's profile into account (name, goal, pain, situation, goal type: measurable / qualitative, active_focus) when it fits, briefly. For qualitative goals do not demand numbers and deadlines; talk about the state and observable signs of progress.

FORBIDDEN to use markdown: no **bold**, no _italics_, no # headings, no hyphen bullet lists.
Write plain text. If you need emphasis, use emoji.

Long structured replies (scripts, plans, menus): a blank line between blocks;
labels like Shots / Voiceover / Description each on their own line; do not glue everything into one paragraph."""


def coach_style_for(lang: object, ru_text: str) -> str:
    """Return the Russian COACH_STYLE_INSTRUCTION for ru, the English one otherwise."""
    return ru_text if is_ru(lang) else COACH_STYLE_INSTRUCTION_EN


# ---------------------------------------------------------------- extra context blocks

def facts_header(lang: object) -> str:
    return "Важные факты:" if is_ru(lang) else "Important facts:"


def personality_intro(lang: object) -> str:
    if is_ru(lang):
        return (
            "Профиль личности — старый фон, на каждое сообщение не пересчитывается. "
            "Последние реплики важнее."
        )
    return (
        "Personality notes are old background and are not recomputed for every message. "
        "The latest replies matter more."
    )


def last_week_text(last_week: dict, lang: object) -> str:
    if is_ru(lang):
        return (
            f"Итог прошлой недели: {last_week.get('summary', '')}\n"
            f"Достижения: {last_week.get('achievements', '')}\n"
            f"Сложности: {last_week.get('challenges', '')}"
        )
    return (
        f"Last week summary: {last_week.get('summary', '')}\n"
        f"Achievements: {last_week.get('achievements', '')}\n"
        f"Challenges: {last_week.get('challenges', '')}"
    )


_PERSONALITY_LABELS = {
    "ru": {
        "communication_style": "Стиль общения",
        "motivation_triggers": "Что мотивирует",
        "procrastination_patterns": "Паттерны прокрастинации",
        "best_time_of_day": "Лучшее время",
        "response_to_pressure": "Реакция на давление",
        "personal_values": "Ценности",
        "strengths": "Сильные стороны",
        "blockers": "Блокеры",
        "raw_insights": "Наблюдения",
    },
    "en": {
        "communication_style": "Communication style",
        "motivation_triggers": "What motivates her",
        "procrastination_patterns": "Procrastination patterns",
        "best_time_of_day": "Best time of day",
        "response_to_pressure": "Response to pressure",
        "personal_values": "Values",
        "strengths": "Strengths",
        "blockers": "Blockers",
        "raw_insights": "Observations",
    },
}


def personality_text(personality: dict | None, lang: object) -> str:
    if not personality:
        return ""
    labels = _PERSONALITY_LABELS["ru" if is_ru(lang) else "en"]
    parts = [
        f"{label}: {personality[key]}"
        for key, label in labels.items()
        if personality.get(key)
    ]
    return "\n".join(parts)


_LEVEL_NAMES_EN = {
    "spark": "Spark",
    "flow": "Flow",
    "power": "Power",
    "legend": "Legend",
}


def level_context(level_key: str, level_name: str, completed: int, lang: object) -> str:
    if is_ru(lang):
        text = f"Уровень пользователя: {level_name} ({completed} выполненных задач)."
        extra = {
            "spark": " Она только начинает — поддерживай особенно тепло.",
            "flow": " Она вошла в ритм — можно чуть больше требовать.",
            "power": " Она стабильна — говори как с равной.",
            "legend": (
                " Она прошла долгий путь — говори как старая подруга "
                "которая знает её насквозь."
            ),
        }
    else:
        name = _LEVEL_NAMES_EN.get(level_key, level_name)
        text = f"User level: {name} ({completed} completed tasks)."
        extra = {
            "spark": " She is just starting: be especially warm.",
            "flow": " She found her rhythm: you can ask a bit more of her.",
            "power": " She is steady: talk to her as an equal.",
            "legend": (
                " She has come a long way: talk like an old friend "
                "who knows her through and through."
            ),
        }
    return text + extra.get(level_key, "")


# ---------------------------------------------------------------- fallbacks

def refusal_fallback(lang: object) -> str:
    if is_ru(lang):
        return "Не смогла ответить на эту формулировку. Переформулируй короче — продолжим."
    return "I couldn't answer that wording. Try rephrasing it shorter and we'll continue."


def empty_fallback(lang: object) -> str:
    return "Напиши ещё раз — я слушаю." if is_ru(lang) else "Write again, I'm listening."


# ---------------------------------------------------------------- check

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC.search(text or ""))
