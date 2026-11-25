"""Which day a reminder is for (Anya, 25.11.2026): «3 декабря», «в пятницу», «через 2 недели».
A day that was named but not understood is never replaced by today. Pure functions."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

NONE, OK, UNKNOWN, PAST = "none", "ok", "unknown", "past"

_MON = (
    r"январ[яь]|янв", r"феврал[яь]|фев", r"марта|март", r"апрел[яь]|апр", r"ма[яй]", r"июн[яь]",
    r"июл[яь]", r"августа?|авг", r"сентябр[яь]|сен", r"октябр[яь]|окт", r"ноябр[яь]|ноя",
    r"декабр[яь]|дек",
)
_MON_EN = (
    r"january|jan", r"february|feb", r"march|mar", r"april|apr", r"may", r"june|jun", r"july|jul",
    r"august|aug", r"september|sept|sep", r"october|oct", r"november|nov", r"december|dec",
)
_ALL_MON = "|".join(f"(?:{a})|(?:{b})" for a, b in zip(_MON, _MON_EN))
_WD = (
    r"понедельник\w*|пн", r"вторник\w*|вт", r"сред[ауеы]|ср", r"четверг\w*|чт", r"пятниц\w*|пт",
    r"суббот\w*|сб", r"воскресень\w*|вс",
)
_WD_EN = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WD_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ALL_WD = "|".join(f"{a}|{b}" for a, b in zip(_WD, _WD_EN))
_NUM = {
    "один": 1, "одну": 1, "два": 2, "две": 2, "пару": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "a": 1, "one": 1, "two": 2,
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUMS = "|".join(sorted(_NUM, key=len, reverse=True))
_UNIT = r"(?P<d>дн(?:я|ей)|день|days?)|(?P<w>недел\w+|weeks?)|(?P<m>месяц\w*|months?)|(?P<y>год\w*|лет|years?)"
_B, _E = r"(?<![a-zа-яё0-9])", r"(?![a-zа-яё0-9])"

_RE = re.compile(
    "|".join((
        rf"(?P<rel>{_B}(?:через|in)\s+(?:(?P<n>\d{{1,3}}|{_NUMS})\s+)?(?:{_UNIT}){_E})",
        rf"(?P<near>{_B}(?:(?P<dat>послезавтра|(?:the\s+)?day\s+after\s+tomorrow)|"
        rf"(?P<tom>завтра|tomorrow)|(?P<tod>сегодня|today)|(?P<yes>вчера|yesterday)){_E})",
        rf"(?P<iso>{_B}(?P<iy>20\d\d)-(?P<im>\d\d)-(?P<id>\d\d){_E})",
        rf"(?P<num>{_B}(?P<nd>\d{{1,2}})[./](?P<nm>\d{{1,2}})[./](?P<ny>20\d\d|\d\d)(?!\d))",
        rf"(?P<dm>{_B}(?:(?:на|on)\s+)?(?P<dd>\d{{1,2}})(?:-?го|st|nd|rd|th)?\s+(?:of\s+)?(?P<dmon>{_ALL_MON})"
        rf"(?:,?\s+(?P<dy>20\d\d)(?!\d)(?:\s*(?:года|г\.?))?)?{_E})",
        rf"(?P<md>{_B}(?:on\s+)?(?P<mmon>{'|'.join(_MON_EN)})\s+(?P<mday>\d{{1,2}})(?:st|nd|rd|th)?"
        rf"(?![\d:])(?:,?\s+(?P<my>20\d\d))?)",
        rf"(?P<chis>{_B}(?P<cd>\d{{1,2}})(?:-?го)?\s+числ[ао]{_E})",
        rf"(?P<wd>{_B}(?:(?:в|во|на|on)\s+)?(?:(?P<nxt>следующ\w+|next)\s+|(?:эт[уо]\w*|ближайш\w+|this)\s+)?"
        rf"(?P<wday>{_ALL_WD}){_E})",
    )),
    re.I,
)
# a day is meant, but we cannot say which one
_VAGUE = re.compile(
    rf"{_B}(?:через\s+\S+\s+(?:дн|день|недел|месяц)\w*|"
    rf"(?:на|в)\s+(?:следующ|эт|ближайш)\w+\s+(?:недел|месяц|год|выходн)\w*|на\s+недел[еи]|"
    rf"на\s+выходных|в\s+выходные|(?:в\s+)?(?:конце|начале|середине)\s+(?:недел|месяц|год)\w*|"
    rf"в\s+(?:январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)|"
    rf"(?:next|this)\s+(?:week|month|year|weekend)|on\s+the\s+weekend|"
    rf"\d{{1,2}}/\d{{1,2}}|(?<!в\s)\d{{1,2}}\.\d{{1,2}}(?=\s+(?:в|at)\s+\d{{1,2}}[:.]\d\d)){_E}",
    re.I,
)
_CLAUSE = re.compile(r"(?i)(?<![a-zа-яё])(?:про|что|чтобы|about|that)(?![a-zа-яё])|\bto\s+(?=[a-z])")
EVERY = re.compile(rf"(?i){_B}(?:кажд\w+|every)\s+(?P<day>{_ALL_WD}){_E}")


def _index(text: str, *tables) -> int | None:
    for table in tables:
        for i, pat in enumerate(table):
            if re.fullmatch(pat, text, re.I):
                return i
    return None


def weekday_key(text: str) -> str | None:
    i = _index(text.strip(), _WD, _WD_EN)
    return WD_KEYS[i] if i is not None else None


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _no_year(m: int, d: int, today: date) -> date | None:
    for y in range(today.year, today.year + 5):
        out = _make(y, m, d)
        if out and out >= today:
            return out
    return None


def _one(m: re.Match, today: date, passed: bool) -> tuple[str, date | None]:
    out: date | None = None
    if m["rel"]:
        n = m["n"]
        k = 1 if not n else int(n) if n.isdigit() else _NUM[n.lower()]
        if m["d"]:
            # «через день» is the day after tomorrow
            k = 2 if not n and m["d"].lower() == "день" else k
            out = today + timedelta(days=k)
        elif m["w"]:
            out = today + timedelta(weeks=k)
        else:
            out = _add_months(today, k if m["m"] else 12 * min(k, 5))
    elif m["near"]:
        if m["yes"]:
            return PAST, None
        out = today + timedelta(days=2 if m["dat"] else 1 if m["tom"] else 0)
    elif m["iso"]:
        out = _make(int(m["iy"]), int(m["im"]), int(m["id"]))
    elif m["num"]:
        a, b, y = int(m["nd"]), int(m["nm"]), int(m["ny"])
        if "/" in m["num"]:
            if a <= 12 and b <= 12 and a != b:
                return UNKNOWN, None  # 3/12: March or December
            if b > 12:
                a, b = b, a
        out = _make(y if y > 99 else 2000 + y, b, a)
    elif m["dm"] or m["md"]:
        mon = _index(m["dmon"] or m["mmon"], _MON, _MON_EN)
        day, year = int(m["dd"] or m["mday"]), m["dy"] or m["my"]
        if mon is not None:
            out = _make(int(year), mon + 1, day) if year else _no_year(mon + 1, day, today)
    elif m["chis"]:
        day = int(m["cd"])
        for k in range(13):
            base = _add_months(today.replace(day=1), k)
            cand = _make(base.year, base.month, day)
            if cand and (cand > today or (cand == today and not passed)):
                out = cand
                break
    elif m["wd"]:
        i = _index(m["wday"], _WD, _WD_EN)
        if i is not None:
            if m["nxt"]:
                out = today + timedelta(days=7 + i - today.weekday())
            else:
                out = today + timedelta(days=(i - today.weekday()) % 7)
                if out == today and passed:
                    out += timedelta(days=7)
    if out is None:
        return UNKNOWN, None
    if out < today or (out == today and passed):
        return PAST, None
    return OK, out


def find(text: str, today: date, passed: bool = False) -> tuple[str, date | None, str]:
    """(status, day, text without the words that named the day). passed: the hour is over today.

    The day before «про / что / чтобы» is the day of the reminder; a day after it may belong
    to the thing itself («напомни завтра про приём 3 декабря») and is used only when there is
    no other."""
    clause = _CLAUSE.search(text)
    cut = clause.start() if clause else len(text)
    hits = list(_RE.finditer(text))
    head = [m for m in hits if m.start() < cut]
    use = head or hits
    if not head and _VAGUE.search(text[:cut]):
        return UNKNOWN, None, text
    if not use:
        return (UNKNOWN if _VAGUE.search(text) else NONE), None, text
    results = [_one(m, today, passed) for m in use]
    days = {d for s, d in results if s == OK}
    bad = [s for s, _ in results if s != OK]
    if bad or len(days) != 1:
        return (bad[0] if bad else UNKNOWN), None, text
    for m in reversed(use):
        text = text[:m.start()] + " " + text[m.end():]
    return OK, days.pop(), text
