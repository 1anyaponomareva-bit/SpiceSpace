"""Sentences in bot messages start with a capital letter (Anya, 21.11.2026).

Applied to every outgoing message in PacedBot, so fixed texts and model replies are
covered alike. Conservative: only a lowercase letter right at the start of the text, of
a line, or after ". ", "! ", "? " is raised; abbreviations ("т.е. ", "и т.д. "), an
ellipsis, links, @names and /commands are left alone.
"""

from __future__ import annotations

import re
import unicodedata

_ABBREV = {
    "т.е", "т.д", "т.п", "т.к", "т.н", "др", "пр", "мин", "руб", "коп", "ч", "сек",
    "тыс", "млн", "млрд", "стр", "напр", "см", "e.g", "i.e", "etc", "vs", "approx",
    "mr", "mrs", "ms", "dr", "st",
}
_WORD_BEFORE = re.compile(r"([\w.]+)$")


def _skippable(ch: str) -> bool:
    """Lead-in that may sit before the first word: spaces, quotes, bullets, emoji."""
    if ch in " \t«\"'(*-–—•>":
        return True
    cat = unicodedata.category(ch)
    return cat in ("So", "Sk", "Mn", "Cf") or ch == "️"


def _raise_from(chars: list[str], i: int) -> None:
    n = len(chars)
    while i < n and _skippable(chars[i]):
        i += 1
    if i >= n:
        return
    ch = chars[i]
    if not ch.isalpha() or not ch.islower():
        return
    # a link or a word glued to a dot ("spicespacebot.ru") is not a sentence start
    rest = "".join(chars[i:i + 8]).lower()
    if rest.startswith(("http", "www.", "t.me")):
        return
    chars[i] = ch.upper()


def capitalize_sentences(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    chars = list(text)
    n = len(chars)
    _raise_from(chars, 0)
    for i, ch in enumerate(chars):
        if ch == "\n":
            _raise_from(chars, i + 1)
        elif ch in ".!?" and i + 1 < n and chars[i + 1] in " \t":
            if ch == "." and i > 0 and chars[i - 1] in ".…":
                continue  # ellipsis: the thought goes on
            if ch == ".":
                m = _WORD_BEFORE.search("".join(chars[max(0, i - 12):i]))
                word = (m.group(1) if m else "").lower().lstrip(".")
                if word in _ABBREV or word.endswith((".е", ".д", ".п", ".к", ".н")):
                    continue
                if word.isdigit():
                    continue  # "1. пункт" stays as written; lists start lines anyway
            _raise_from(chars, i + 1)
    return "".join(chars)


_DIGIT_DASH = re.compile(r"(?<=\d)\s*[—–]\s*(?=\d)")
_ANY_DASH = re.compile(r"[ \t]*[—–][ \t]*")


def short_dashes(text: str) -> str:
    """No long dash in anything the bot sends (Anya, 21.11.2026): « - » instead of
    « — » or « – »; between digits a plain hyphen («2-3»)."""
    if not text or not isinstance(text, str) or ("—" not in text and "–" not in text):
        return text
    text = _DIGIT_DASH.sub("-", text)

    def swap(m: re.Match) -> str:
        start = m.start() == 0 or text[m.start() - 1] == "\n"
        return "- " if start else " - "

    return _ANY_DASH.sub(swap, text)
