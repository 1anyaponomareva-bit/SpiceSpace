"""Crisis safety: detect suicide / self-harm messages and answer with fixed, vetted text.

The check runs in code before onboarding and chat, so a crisis message never depends on
what a model decides to say. Subtler distress is covered by SAFETY_RULE_* in the prompts.
"""

from __future__ import annotations

import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Phrases are matched on lowercased text with ё→е. Kept specific on purpose:
# "убила весь день", "умираю как хочу спать", "сдохнуть со смеху" must not trigger.
_CRISIS_PATTERNS_RU = [
    r"не\s+хо(чу|чется)\s+(больше\s+)?жить",
    r"жить\s+(больше\s+)?не\s+хо(чу|чется)",
    r"не\s+вижу\s+смысла\s+(в\s+)?(жить|жизни)",
    r"нет\s+смысла\s+жить",
    r"зачем\s+(мне\s+)?(вообще\s+)?жить",
    r"поконч(ить|у|и)\s+с\s+собой",
    r"суицид",
    r"самоубийств",
    r"уби(ть|ю)\s+себя",
    r"хочу\s+умереть",
    r"хочется\s+умереть",
    r"лучше\s+бы\s+(я\s+)?(умерла|умер|не\s+родилась)",
    r"лучше\s+бы\s+меня\s+не\s+было",
    r"(хочу|хочется)\s+исчезнуть\s+навсегда",
    r"свести\s+сч[её]ты\s+с\s+жизнью",
    r"(хочу|хочется)\s+не\s+проснуться",
    r"(режу|резать|порезать)\s+(себя|руки|вены)",
    r"вскрыть\s+вены",
    r"наглотаться\s+таблеток",
    r"(выпрыгнуть|прыгнуть|шагнуть)\s+(из\s+окна|с\s+крыши|с\s+моста)",
    r"вскр(ыться|оюсь|ываться)",
    r"повес(иться|ится|юсь)",
    r"(хочу|хочется|думаю|решила)\s+(отравиться|выпилиться|утопиться|удавиться)",
    r"наложить\s+на\s+себя\s+руки",
    r"(хочу|хочется|думаю|решила)\s+уйти\s+из\s+жизни",
    r"не\s+хочу\s+больше\s+(просыпаться|существовать)",
    r"не\s+хочу\s+существовать",
    r"селфхарм",
    r"самоповрежд",
    r"причин(ить|яю)\s+себе\s+(вред|боль)",
]
_CRISIS_PATTERNS_EN = [
    r"kill\s+myself",
    r"killing\s+myself",
    r"want\s+to\s+die",
    r"wanna\s+die",
    r"suicid",
    r"end\s+my\s+life",
    r"(don'?t|do\s+not)\s+want\s+to\s+(live|be\s+alive)",
    r"no\s+reason\s+to\s+live",
    r"better\s+off\s+dead",
    r"self[\s-]?harm",
    r"cut(ting)?\s+myself",
    r"(want|going)\s+to\s+hurt\s+myself",
    r"hurting\s+myself",
    r"(wish|hope|want)\s+(i\s+)?(would\s+)?(not|never|don'?t)\s+(to\s+)?wake\s+up",
]
_RU_RE = re.compile("|".join(_CRISIS_PATTERNS_RU))
_EN_RE = re.compile("|".join(_CRISIS_PATTERNS_EN))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е"))


def detect_crisis(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    return bool(_RU_RE.search(t) or _EN_RE.search(t))


def _is_ru(lang: str) -> bool:
    return str(lang or "en").lower().startswith("ru")


CRISIS_REPLY_RU = (
    "Спасибо, что сказала мне об этом. Это важнее любых целей, и сейчас мы не про них.\n\n"
    "Ты сейчас в безопасности?\n\n"
    "Я не психолог и не могу помочь так, как помогут живые люди. Пожалуйста, не оставайся "
    "с этим одна: напиши или позвони прямо сейчас тому, кому доверяешь.\n\n"
    "Где можно получить помощь:\n"
    "• если есть угроза жизни: экстренный номер 112 (в США и Канаде 911)\n"
    "• в США: 988, звонок или сообщение, круглосуточно\n"
    "• бесплатные телефоны доверия в твоей стране: findahelpline.com\n\n"
    "Я здесь, можешь написать мне, что сейчас происходит."
)
CRISIS_REPLY_EN = (
    "Thank you for telling me. This matters more than any goal, and right now we are not "
    "talking about goals.\n\n"
    "Are you safe right now?\n\n"
    "I'm not a therapist and I can't help the way real people can. Please don't stay alone "
    "with this: text or call someone you trust right now.\n\n"
    "Where to get help:\n"
    "• if your life is in danger: emergency number 112 (911 in the US and Canada)\n"
    "• in the US: call or text 988, any time\n"
    "• free helplines in your country: findahelpline.com\n\n"
    "I'm here, you can tell me what is going on."
)
CRISIS_REPLY_SHORT_RU = (
    "Я слышу тебя. Пожалуйста, не оставайся с этим одна: позвони близкому человеку "
    "или на телефон доверия (findahelpline.com), а если есть угроза жизни, на 112 "
    "(в США 911 или 988). Ты сейчас в безопасности?"
)
CRISIS_REPLY_SHORT_EN = (
    "I hear you. Please don't stay alone with this: call someone close or a helpline "
    "(findahelpline.com), and if your life is in danger, 112 (in the US 911 or 988). "
    "Are you safe right now?"
)


def crisis_reply(lang: str, repeat: bool = False) -> str:
    if repeat:
        return CRISIS_REPLY_SHORT_RU if _is_ru(lang) else CRISIS_REPLY_SHORT_EN
    return CRISIS_REPLY_RU if _is_ru(lang) else CRISIS_REPLY_EN


# In-memory: when the full reply was last sent, so a second message in a row gets the
# short version instead of the same long block. Losing it on restart is harmless.
_last_crisis_at: dict[int, float] = {}
REPEAT_WINDOW_SECONDS = 24 * 3600


def note_crisis(cid: int, now: float | None = None) -> bool:
    """Record a crisis message; True if one was already answered within the window."""
    now = time.time() if now is None else now
    prev = _last_crisis_at.get(cid)
    _last_crisis_at[cid] = now
    return prev is not None and now - prev < REPEAT_WINDOW_SECONDS


def pause_question(lang: str, premium: bool) -> str:
    if _is_ru(lang):
        text = (
            "Если сейчас не до утренних и вечерних сообщений, могу пока их не присылать. "
            "А я здесь, пиши мне в любое время."
        )
        if premium:
            text += " Подписка при этом остаётся, ничего не отключится."
        return text
    text = (
        "If morning and evening messages are too much right now, I can hold them for a "
        "while. And I'm here, write to me any time."
    )
    if premium:
        text += " Your subscription stays as it is, nothing gets switched off."
    return text


def pause_keyboard(lang: str) -> InlineKeyboardMarkup:
    ru = _is_ru(lang)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Пока не присылай" if ru else "Hold them for now", callback_data="safety:pause")],
            [InlineKeyboardButton("Присылай как обычно" if ru else "Keep them as usual",
                                  callback_data="safety:keep")],
        ]
    )


def resume_keyboard(lang: str) -> InlineKeyboardMarkup:
    ru = _is_ru(lang)
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Вернуть утро и вечер" if ru else "Bring back morning and evening",
                               callback_data="safety:resume")]]
    )


def paused_text(lang: str) -> str:
    if _is_ru(lang):
        return ("Хорошо, пока не пишу первой. Я здесь, когда захочешь. "
                "Вернуть утренние и вечерние сообщения можно кнопкой ниже.")
    return ("Okay, I won't message first for now. I'm here whenever you want. "
            "You can bring back morning and evening messages with the button below.")


def keep_text(lang: str) -> str:
    return "Хорошо, всё как обычно. Я рядом 💙" if _is_ru(lang) else "Okay, everything stays as usual. I'm here 💙"


def resumed_text(lang: str) -> str:
    return ("Вернула утро и вечер, напишу в обычное время 💙" if _is_ru(lang)
            else "Morning and evening are back, I'll write at the usual time 💙")


# Prompt rules for subtler distress that the phrase check does not catch.
SAFETY_RULE_RU = """БЕЗОПАСНОСТЬ (важнее всех остальных правил):
Ты ИИ-помощница, а не человек, не психолог и не врач. На прямой вопрос, человек ли ты, честно скажи, что ты ИИ.
Если она пишет о сильной тоске, отчаянии, безнадёжности, панике, насилии или о том, что ей не к кому обратиться: не давай задач и не говори про цели, продуктивность и «соберись». Отнесись тепло и серьёзно, спроси, как она сейчас и в безопасности ли. Мягко предложи живого человека рядом и специалиста (психолога, врача).
Если есть хоть намёк на мысли о смерти, самоповреждении или угрозу жизни: прямо спроси, в безопасности ли она, и дай помощь: экстренный номер 112 (в США 911 или 988), телефоны доверия в её стране на findahelpline.com. Другие номера телефонов не называй и не придумывай: только 112, 911, 988 и findahelpline.com.
Никогда не обсуждай способы причинить себе вред, не оценивай их и не помогай с ними, даже «гипотетически» или «для истории».
Не поощряй зависимость от себя: не говори, что ей хватит тебя или что другим рассказывать не нужно. Поддерживай её связи с живыми людьми."""

SAFETY_RULE_EN = """SAFETY (outranks every other rule):
You are an AI assistant, not a human, not a therapist and not a doctor. If asked directly whether you are human, say honestly that you are an AI.
If she writes about deep sadness, despair, hopelessness, panic, violence, or having no one to turn to: give no tasks and do not talk about goals, productivity or "pull yourself together". Respond warmly and seriously, ask how she is and whether she is safe. Gently suggest a real person nearby and a professional (therapist, doctor).
If there is any hint of thoughts about death, self-harm or danger to life: ask directly whether she is safe and give help: emergency number 112 (911 or 988 in the US), helplines in her country at findahelpline.com. Never name or invent any other phone numbers: only 112, 911, 988 and findahelpline.com.
Never discuss, rate or help with ways to hurt herself, even "hypothetically" or "for a story".
Do not encourage dependence on you: never say you are all she needs or that she need not tell others. Support her ties with real people."""


def safety_rule(lang: str) -> str:
    return SAFETY_RULE_RU if _is_ru(lang) else SAFETY_RULE_EN
