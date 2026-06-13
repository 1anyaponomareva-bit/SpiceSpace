"""User-facing bot strings — always respect profile language_code."""

from __future__ import annotations

_UI: dict[str, dict[str, str]] = {
    "midday_reminder_ask": {
        "ru": (
            "Напомнить тебе про задачу днём? "
            "Напиши время — например, 14:00. Или «нет» если не нужно."
        ),
        "en": (
            "Want a midday reminder about your task? "
            "Send a time — e.g. 14:00. Or «no» if you don't need one."
        ),
    },
    "ok_short": {"ru": "Окей 💚", "en": "Okay 💚"},
    "reminder_at": {"ru": "Напомню в {time} 💚", "en": "I'll remind you at {time} 💚"},
    "reminder_save_failed": {
        "ru": "Не вышло сохранить напоминание — попробуй ещё раз.",
        "en": "Couldn't save the reminder — try again.",
    },
    "reminder_save_check": {
        "ru": "Не вышло сохранить напоминание — проверь дату и время в сообщении.",
        "en": "Couldn't save the reminder — check the date and time in your message.",
    },
    "reminder_time_prompt": {
        "ru": "Напиши время — например, 14:00 или «напомни в 12:00». Или «нет».",
        "en": "Send a time — e.g. 14:00 or «remind me at 12:00». Or «no».",
    },
    "task_saved_today": {
        "ru": "Записала — на сегодня: {task} 💚",
        "en": "Saved — for today: {task} 💚",
    },
    "task_pick_nudge": {
        "ru": "Выбери задачу на сегодня из вариантов выше — или напиши свою 💚",
        "en": "Pick today's task from the options above — or write your own 💚",
    },
    "task_default_title": {
        "ru": "Задача на сегодня",
        "en": "Today's task",
    },
    "reminder_created": {
        "ru": "Окей ✨ Напомню про «{title}» {tail}.",
        "en": "Okay ✨ I'll remind you about «{title}» {tail}.",
    },
    "reminder_what": {"ru": "Что напомнить?", "en": "What should I remind you about?"},
    "tomorrow_saved": {
        "ru": "Записала ✨ На завтра: {task}",
        "en": "Saved ✨ For tomorrow: {task}",
    },
    "tomorrow_ask": {
        "ru": "Что конкретно сделаешь завтра?",
        "en": "What exactly will you do tomorrow?",
    },
    "gotovo_done": {"ru": "Записала ✨ Красота.", "en": "Saved ✨ Nice."},
    "gotovo_hint": {
        "ru": (
            "Отметь в Mini App в разделе «План» или дождись напоминания от меня — "
            "тогда «готово» сработает сразу."
        ),
        "en": (
            "Mark it in the Mini App under Plan, or wait for my reminder — "
            "then «done» will work right away."
        ),
    },
    "claude_quota": {
        "ru": (
            "У Claude API сейчас лимит запросов (ошибка 429): слишком частые сообщения "
            "или дневная квота исчерпана. Подожди 1–2 минуты и напиши снова.\n\n"
            "Если так постоянно: проверь ключ и лимиты в консоли Anthropic "
            "(https://console.anthropic.com) — при необходимости смени модель в .env (CLAUDE_MODEL)."
        ),
        "en": (
            "Claude API rate limit (429): too many requests or daily quota exhausted. "
            "Wait 1–2 minutes and try again.\n\n"
            "If this keeps happening: check your key and limits at "
            "https://console.anthropic.com — you can change CLAUDE_MODEL in .env."
        ),
    },
    "claude_error": {
        "ru": "Сейчас не получилось связаться с моделью. Попробуй ещё раз через минуту.",
        "en": "Couldn't reach the model right now. Try again in a minute.",
    },
    "claude_quota_short": {
        "ru": "У Claude API сейчас лимит запросов. Подожди 1–2 минуты и напиши снова.",
        "en": "Claude API rate limit right now. Wait 1–2 minutes and try again.",
    },
    "reminder_tail_at": {"ru": "в {time}", "en": "at {time}"},
    "reminder_tail_daily": {"ru": ", каждый день", "en": ", every day"},
    "reminder_tail_weekly": {"ru": ", по {days}", "en": ", on {days}"},
    "onboard_text_only_photo": {
        "ru": "Давай до конца знакомство текстом — фото чуть позже 💛",
        "en": "Let's finish setup in text first — photos a bit later 💛",
    },
    "onboard_text_only_voice": {
        "ru": "Давай до конца знакомство текстом — голос чуть позже 💛",
        "en": "Let's finish setup in text first — voice messages a bit later 💛",
    },
    "voice_not_supported": {
        "ru": "Голосовые сообщения пока не расшифровываю — напиши текстом, так диалог стабильнее.",
        "en": "I can't transcribe voice messages yet — text works best for now.",
    },
    "photo_default_caption": {
        "ru": "Что на фото?",
        "en": "What's in the photo?",
    },
    "photo_parse_failed": {
        "ru": "Не получилось разобрать фото. Попробуй ещё раз или опиши текстом.",
        "en": "Couldn't read the photo. Try again or describe it in text.",
    },
}


def ui_lang(profile: dict | None) -> str:
    lc = str((profile or {}).get("language_code") or "en").strip().lower()
    return "ru" if lc.startswith("ru") else "en"


def ui_text(key: str, lang: str | None = None, *, profile: dict | None = None, **kwargs: str) -> str:
    loc = lang or ui_lang(profile)
    if not loc.startswith("ru"):
        loc = "en"
    else:
        loc = "ru"
    entry = _UI.get(key) or {}
    tpl = entry.get(loc) or entry.get("en") or key
    return tpl.format(**kwargs) if kwargs else tpl
