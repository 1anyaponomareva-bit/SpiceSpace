"""The name shown to a person. «подруга» / «friend» was saved as the name when Space did
not understand it (Anya, 25.11.2026: «какая я ему подруга?»). The stored value stays: an
empty name means «not onboarded» in many places. It is never shown."""

from __future__ import annotations

import re

_PLACEHOLDERS = frozenset({"подруга", "подружка", "friend", "none", "null", "undefined", "-"})


def real_name(name: object) -> str:
    n = str(name or "").strip()
    return "" if n.lower() in _PLACEHOLDERS else n


def fill(template: str, name: object, **kwargs: object) -> str:
    """template.format(name=...), or the same text without an address when there is no name."""
    n = real_name(name)
    text = str(template)
    if not n:
        text = text.replace("{name}, ", "").replace(", {name}", "").replace("{name} ", "")
    out = text.format(name=n, **kwargs)
    return out if n else cap(out)


def lead(name: object, text: str) -> str:
    """«Катя, доброе утро» or «Доброе утро»."""
    n = real_name(name)
    return f"{n}, {text}" if n else cap(text)


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


_SAID_RU = re.compile(r"(?:^|[.!,\s])(?:меня зовут|зови меня|называй меня|мо[её] имя)\s*[-:—]?\s*([А-ЯЁа-яё]{2,20})(?![А-ЯЁа-яё])", re.I)
_SAID_EN = re.compile(r"\b(?:my name is|call me)\s+([A-Za-z]{2,20})\b", re.I)
_NOT_NAMES = frozenset("не на по просто так как никак это вот мама back later when please maybe not a an the".split())


def said(text: object) -> str:
    """A name the person gives plainly: «меня зовут Катя»."""
    m = _SAID_RU.search(str(text or "")) or _SAID_EN.search(str(text or ""))
    if not m or m.group(1).lower() in _NOT_NAMES or not real_name(m.group(1)):
        return ""
    return m.group(1)[:1].upper() + m.group(1)[1:]
