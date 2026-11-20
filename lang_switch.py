"""Which language to talk in (Anya, 21.11.2026): she writes in Russian -> Russian;
she asks to switch ("пиши по-английски", "speak Russian") -> that language.
The Mini App has its own switch in settings. Pure functions; main.py saves the choice."""

from __future__ import annotations

import re

_CYR = re.compile(r"[а-яё]", re.I)
_TO_EN = re.compile(
    r"(перейд|переключ|говори|пиши|отвечай|давай|можно)\w*.{0,20}(по[- ]?англ|на англ|english)"
    r"|(speak|write|switch|answer|reply|talk|change)\w*.{0,20}english",
    re.I,
)
_TO_RU = re.compile(
    r"(перейд|переключ|говори|пиши|отвечай|давай|можно)\w*.{0,20}(по[- ]?русск|на русск)"
    r"|(speak|write|switch|answer|reply|talk|change)\w*.{0,20}russian",
    re.I,
)

CONFIRM = {
    "ru": "Хорошо, дальше пишу по-русски 🤍",
    "en": "Sure, I'll write in English from now on 🤍",
}


def is_ru(lang: str) -> bool:
    return str(lang or "").lower().startswith("ru")


def requested(text: str) -> str:
    """'en' / 'ru' when she asks to switch the language, else ''."""
    t = str(text or "")
    if len(t) > 160:
        return ""  # a long message that mentions English is about something else
    if _TO_EN.search(t):
        return "en"
    if _TO_RU.search(t):
        return "ru"
    return ""


def implied(text: str, current: str) -> str:
    """'ru' when she writes in Russian while the bot speaks another language, else ''."""
    if is_ru(current):
        return ""
    return "ru" if len(_CYR.findall(str(text or ""))) >= 3 else ""
