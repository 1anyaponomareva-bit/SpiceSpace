"""Her time zone before she tells it (Anya, 21.11.2026).

Telegram does not give a time zone; only the Mini App sends the phone's. Everyone else
got the bot's own zone (Vietnam), so Russian speakers had «morning at 9» at 5 a.m. and
the first «morning» message at 2 a.m. Moscow — 5 of 9 who finished onboarding blocked
the bot. Russian / Ukrainian / Belarusian speakers now start on Moscow time and can
fix it by writing their city. Pure functions; main.py saves the choice.
"""

from __future__ import annotations

import os
import re

MOSCOW = "Europe/Moscow"
_MOSCOW_LANGS = ("ru", "uk", "be")


def bot_timezone() -> str:
    return os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip() or "Asia/Ho_Chi_Minh"


def default_timezone(lang: str | None) -> str:
    """Moscow for Russian-speaking Telegram, the bot's zone otherwise."""
    code = str(lang or "").lower()[:2]
    return MOSCOW if code in _MOSCOW_LANGS else bot_timezone()


# stem (lowercase, ё→е) -> (zone, name for «по времени …»)
_CITIES: tuple[tuple[str, str, str], ...] = (
    (r"москв|мск\b|msk\b|moscow", MOSCOW, "Москвы"),
    (r"питер|спб\b|санкт|петербург|ленинград", MOSCOW, "Петербурга"),
    (r"казан|нижн\w* новгород|нижн\w*|воронеж|ростов|краснодар|сочи|волгоград|ярославл|тул[аеу]\b|рязан|твер|калуг|владимир|смоленск|брянск|курск|белгород|липецк|тамбов|пенз|ставропол|махачкал|грозн|крым|симферопол|севастопол|мурманск|архангельск|вологд|кострома|иванов|чебоксар|йошкар|саранск", MOSCOW, "Москвы"),
    (r"самар(?!канд)|саратов|ульяновск|ижевск|астрахан|тольятти", "Europe/Samara", "Самары"),
    (r"калининград", "Europe/Kaliningrad", "Калининграда"),
    (r"екатеринбург|екб\b|ебург|челябинск|тюмен|перм|уф[аеу]\b|оренбург|курган|сургут|магнитогорск", "Asia/Yekaterinburg", "Екатеринбурга"),
    (r"омск", "Asia/Omsk", "Омска"),
    (r"новосибирск|новосиб|нск\b|барнаул|томск", "Asia/Novosibirsk", "Новосибирска"),
    (r"красноярск|кемерово|новокузнецк|абакан", "Asia/Krasnoyarsk", "Красноярска"),
    (r"иркутск|улан[- ]?удэ|братск", "Asia/Irkutsk", "Иркутска"),
    (r"якутск|чит[аеу]\b", "Asia/Yakutsk", "Якутска"),
    (r"владивосток|хабаровск|благовещенск", "Asia/Vladivostok", "Владивостока"),
    (r"магадан|сахалин|южно[- ]?сахалинск", "Asia/Magadan", "Магадана"),
    (r"камчатк|петропавловск[- ]камчат", "Asia/Kamchatka", "Камчатки"),
    (r"минск|беларус|белорус|гомел|брест|гродн|витебск|могил[её]в", "Europe/Minsk", "Минска"),
    (r"киев|київ|харьков|харків|одесс|днепр|дніпр|львов|львів|запорож|украин|україн", "Europe/Kiev", "Киева"),
    (r"кишин[её]в|молдов", "Europe/Chisinau", "Кишинёва"),
    (r"алмат|астан|казахстан|шымкент|караганд", "Asia/Almaty", "Алматы"),
    (r"ташкент|узбекистан|самарканд", "Asia/Tashkent", "Ташкента"),
    (r"бишкек|киргиз|кыргыз", "Asia/Bishkek", "Бишкека"),
    (r"тбилиси|грузи|батуми", "Asia/Tbilisi", "Тбилиси"),
    (r"ереван|армени", "Asia/Yerevan", "Еревана"),
    (r"баку|азербайджан", "Asia/Baku", "Баку"),
    (r"стамбул|турци|анталь|istanbul", "Europe/Istanbul", "Стамбула"),
    (r"дуба|оаэ|эмират|dubai", "Asia/Dubai", "Дубая"),
    (r"израил|тель[- ]?авив|иерусалим|хайф", "Asia/Jerusalem", "Израиля"),
    (r"кипр|лимассол|никоси|ларнак", "Asia/Nicosia", "Кипра"),
    (r"берлин|герман|мюнхен|варшав|польш|прага|чехи|вен[аеу]\b|австри|париж|франци|рим\b|итали|мадрид|барселон|испани|амстердам|нидерланд|сербия|белград|черногор|будва", "Europe/Berlin", "Европы (Берлин)"),
    (r"лондон|англи|британи|london", "Europe/London", "Лондона"),
    (r"таиланд|тайланд|пхукет|бангкок|паттай", "Asia/Bangkok", "Таиланда"),
    (r"вьетнам|нячанг|хошимин|ханой|дананг|фукуок", "Asia/Ho_Chi_Minh", "Вьетнама"),
    (r"бали|индонези", "Asia/Makassar", "Бали"),
    (r"нью[- ]?йорк|new york|майами|флорид", "America/New_York", "Нью-Йорка"),
    (r"лос[- ]?анджелес|калифорни|los angeles", "America/Los_Angeles", "Лос-Анджелеса"),
)
_CITY_RES = tuple((re.compile(r"(?<!\w)(?:" + p + ")"), tz, label) for p, tz, label in _CITIES)

# what may surround the city in a short answer: «я в Казани», «живу в Екб», «у меня Минск»
_FILLER = re.compile(
    r"\b(я|мы|сейчас|живу|живем|живём|нахожусь|находимся|у|меня|нас|в|во|из|город|г|"
    r"время|по|времени|часовой|пояс|это|тут|здесь|а|ну|да|нет|сама|сам|i|live|in|am|im|from|time)\b"
)


def city_timezone(text: str) -> tuple[str, str] | None:
    """(zone, «Казани»-style label) when a short message is just her city, else None.
    «хочу переехать в Москву» or a long message mentioning a city does not count."""
    t = str(text or "").lower().replace("ё", "е").strip()
    if not t or len(t) > 40:
        return None
    for rx, tz, label in _CITY_RES:
        m = rx.search(t)
        if not m:
            continue
        # every word that the city match does not touch must be filler
        for w in re.finditer(r"[\w-]+", t):
            if w.end() <= m.start() or w.start() >= m.end():
                if not _FILLER.fullmatch(w.group(0)):
                    return None
        return tz, label
    return None


def zone_label(tz: str) -> str:
    """«Москве»-style phrase for the completion message («по Москве»)."""
    return {
        MOSCOW: "по Москве",
    }.get(tz, "")
