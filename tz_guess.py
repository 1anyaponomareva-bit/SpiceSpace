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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
    (r"екатеринбург|екб\b|ебург|челябинск|тюмен|перм(?:ь|и|ью)?\b|уф[аеуы]\b|оренбург|курган|сургут|магнитогорск|нижневартовск|ханты|новый уренгой|ноябрьск", "Asia/Yekaterinburg", "Екатеринбурга"),
    (r"омск", "Asia/Omsk", "Омска"),
    (r"томск", "Asia/Tomsk", "Томска"),
    (r"новосибирск|новосиб|нск\b|барнаул", "Asia/Novosibirsk", "Новосибирска"),
    (r"красноярск|кемеров|новокузнецк|абакан|норильск", "Asia/Krasnoyarsk", "Красноярска"),
    (r"иркутск|улан[- ]?удэ|братск", "Asia/Irkutsk", "Иркутска"),
    (r"якутск|чит[аеу]\b", "Asia/Yakutsk", "Якутска"),
    (r"владивосток|хабаровск|благовещенск", "Asia/Vladivostok", "Владивостока"),
    (r"магадан|сахалин|южно[- ]?сахалинск", "Asia/Magadan", "Магадана"),
    (r"камчатк|петропавловск[- ]камчат", "Asia/Kamchatka", "Камчатки"),
    (r"минск|беларус|белорус|гомел|брест|гродн|витебск|могил[её]в", "Europe/Minsk", "Минска"),
    (r"киев|київ|харьков|харків|одесс|днепр|дніпр|львов|львів|запорож|украин|україн", "Europe/Kiev", "Киева"),
    (r"кишин[её]в|молдов", "Europe/Chisinau", "Кишинёва"),
    (r"алмат|астан(?!а\w)|казахстан|шымкент|караганд|талдык|павлодар|костанай|актобе|атырау|актау", "Asia/Almaty", "Алматы"),
    (r"ташкент|узбекистан|самарканд", "Asia/Tashkent", "Ташкента"),
    (r"бишкек|киргиз|кыргыз", "Asia/Bishkek", "Бишкека"),
    (r"тбилиси|грузи|батуми", "Asia/Tbilisi", "Тбилиси"),
    (r"ереван|армени", "Asia/Yerevan", "Еревана"),
    (r"баку|азербайджан", "Asia/Baku", "Баку"),
    (r"стамбул|турци|анталь|istanbul", "Europe/Istanbul", "Стамбула"),
    (r"дуба|оаэ|эмират|dubai", "Asia/Dubai", "Дубая"),
    (r"израил|тель[- ]?авив|иерусалим|хайф", "Asia/Jerusalem", "Израиля"),
    (r"кипр|лимассол|никоси|ларнак", "Asia/Nicosia", "Кипра"),
    (r"берлин|герман|мюнхен|штутгарт|гамбург|кельн|франкфурт|дюссельдорф|варшав|польш|прага|чехи|вен[аеу]\b|австри|париж|франци|рим\b|итали|мадрид|барселон|испани|амстердам|нидерланд|сербия|белград|черногор|будва", "Europe/Berlin", "Европы (Берлин)"),
    (r"лондон|англи|британи|london", "Europe/London", "Лондона"),
    (r"таиланд|тайланд|пхукет|бангкок|паттай", "Asia/Bangkok", "Таиланда"),
    (r"вьетнам|нячанг|хошимин|ханой|дананг|фукуок", "Asia/Ho_Chi_Minh", "Вьетнама"),
    (r"бали|индонези", "Asia/Makassar", "Бали"),
    (r"нью[- ]?йорк|майами|флорид", "America/New_York", "Нью-Йорка"),
    (r"лос[- ]?анджелес|калифорни|сиэтл|сан[- ]?франциско|портленд", "America/Los_Angeles", "Лос-Анджелеса"),
    # English names (22.11.2026, English Threads test): US first, then big cities elsewhere
    (r"new york|nyc\b|ny\b|brooklyn|manhattan|boston|massachusetts|philadelphia|pennsylvania|"
     r"miami|orlando|tampa|florida|atlanta|washington,? ?d\.?c|\bdc\b|baltimore|maryland|"
     r"charlotte|north carolina|south carolina|virginia|new jersey|nj\b|connecticut|detroit|michigan|"
     r"ohio|columbus|cleveland|pittsburgh|indiana|indianapolis|toronto|montreal|ottawa|ontario|quebec|"
     r"east coast|eastern|est\b|edt\b", "America/New_York", "Нью-Йорка"),
    (r"chicago|illinois|texas|houston|dallas|austin|san antonio|minnesota|minneapolis|wisconsin|"
     r"milwaukee|missouri|st\.? louis|kansas|tennessee|nashville|memphis|new orleans|louisiana|"
     r"oklahoma|iowa|alabama|mississippi|arkansas|nebraska|winnipeg|mexico city|"
     r"central|cst\b|cdt\b", "America/Chicago", "Чикаго"),
    (r"phoenix|arizona", "America/Phoenix", "Финикса"),
    (r"denver|colorado|utah|salt lake|new mexico|albuquerque|montana|wyoming|idaho|calgary|edmonton|"
     r"mountain|mst\b|mdt\b", "America/Denver", "Денвера"),
    (r"los angeles|california|san francisco|san diego|san jose|bay area|silicon valley|seattle|"
     r"washington state|portland|oregon|nevada|las vegas|vancouver|west coast|"
     r"pacific|pst\b|pdt\b", "America/Los_Angeles", "Лос-Анджелеса"),
    (r"alaska|anchorage", "America/Anchorage", "Аляски"),
    (r"hawaii|honolulu", "Pacific/Honolulu", "Гавайев"),
    (r"dublin|ireland", "Europe/Dublin", "Дублина"),
    (r"england|britain|\buk\b|scotland|manchester|gmt\b|bst\b", "Europe/London", "Лондона"),
    (r"lisbon|portugal", "Europe/Lisbon", "Лиссабона"),
    (r"berlin|germany|munich|paris|france|madrid|spain|barcelona|rome|italy|milan|amsterdam|"
     r"netherlands|brussels|belgium|vienna|austria|zurich|switzerland|warsaw|poland|prague|"
     r"stockholm|sweden|oslo|norway|copenhagen|denmark|cet\b|cest\b", "Europe/Berlin", "Европы (Берлин)"),
    (r"athens|greece|helsinki|finland|bucharest|romania|sofia|bulgaria", "Europe/Athens", "Афин"),
    (r"india|delhi|mumbai|bangalore|bengaluru|ist\b", "Asia/Kolkata", "Индии"),
    (r"singapore|malaysia|kuala lumpur", "Asia/Singapore", "Сингапура"),
    (r"philippines|manila", "Asia/Manila", "Манилы"),
    (r"hong kong|china|beijing|shanghai|taiwan|taipei", "Asia/Shanghai", "Китая"),
    (r"japan|tokyo", "Asia/Tokyo", "Токио"),
    (r"korea|seoul", "Asia/Seoul", "Сеула"),
    (r"sydney|melbourne|canberra", "Australia/Sydney", "Сиднея"),
    (r"brisbane|queensland", "Australia/Brisbane", "Брисбена"),
    (r"perth", "Australia/Perth", "Перта"),
    (r"auckland|new zealand", "Pacific/Auckland", "Окленда"),
    (r"sao paulo|são paulo|rio|brazil", "America/Sao_Paulo", "Сан-Паулу"),
    (r"buenos aires|argentina", "America/Argentina/Buenos_Aires", "Буэнос-Айреса"),
    (r"bangkok|thailand|phuket", "Asia/Bangkok", "Таиланда"),
    (r"bali|indonesia|jakarta", "Asia/Makassar", "Бали"),
    (r"cyprus|limassol", "Asia/Nicosia", "Кипра"),
    (r"israel|tel aviv", "Asia/Jerusalem", "Израиля"),
    (r"tbilisi|yerevan|armenia|baku", "Asia/Tbilisi", "Тбилиси"),
)
_CITY_RES = tuple((re.compile(r"(?<!\w)(?:" + p + ")"), tz, label) for p, tz, label in _CITIES)

# what may surround the city in a short answer: «я в Казани», «живу в Екб», «у меня Минск»
_FILLER = re.compile(
    r"\b(я|мы|сейчас|живу|живем|живём|нахожусь|находимся|у|меня|нас|в|во|из|город|г|"
    r"время|по|времени|часовой|пояс|это|тут|здесь|а|ну|да|нет|сама|сам|i|live|in|am|im|from|time|"
    r"m|s|it|its|my|the|zone|timezone|near|area|state|city|standard|daylight|us|usa|based|living|"
    r"around|currently|now|we|our|hi|hey|ok|okay|yes)\b"
)


_OFFSET_RE = re.compile(r"(?<!\w)(?:gmt|utc)\s*([+-])\s*(\d{1,2})(?::00)?(?!\d)")


def _only_filler(rest: str) -> bool:
    return all(_FILLER.fullmatch(w) for w in re.findall(r"[\w]+", rest))


def offset_timezone(text: str) -> str:
    """«GMT+3», «UTC -5» -> an Etc/GMT zone (sign is inverted there), else ''."""
    t = str(text or "").lower()
    m = _OFFSET_RE.search(t)
    if not m or not _only_filler(t[: m.start()] + " " + t[m.end():]):
        return ""
    hours = int(m.group(2))
    if hours > 14:
        return ""
    if hours == 0:
        return "Etc/GMT"
    return f"Etc/GMT{'-' if m.group(1) == '+' else '+'}{hours}"


def city_timezone(text: str) -> tuple[str, str] | None:
    """(zone, «Казани»-style label) when a short message is just her city, else None.
    «хочу переехать в Москву» or a long message mentioning a city does not count."""
    t = str(text or "").lower().replace("ё", "е").replace("’", "'").strip(" .!🙂")
    if not t or len(t) > 40:
        return None
    off = offset_timezone(t)
    if off:
        m = _OFFSET_RE.search(t)
        return off, f"GMT{m.group(1)}{int(m.group(2))}"
    for rx, tz, label in _CITY_RES:
        spans = [(m.start(), m.end()) for m in rx.finditer(t)]
        if not spans:
            continue
        # every word that no city name of this zone touches must be filler («Denver, Colorado»)
        for w in re.finditer(r"[\w-]+", t):
            touched = any(w.start() < e and w.end() > s for s, e in spans)
            if not touched and not _FILLER.fullmatch(w.group(0)):
                return None
        return tz, label
    return None


# ---- the zone from an ordinary phrase (23.11.2026): «я живу в Томске, у меня 11:08» used to
# get an apology and nothing was saved
_PLACE_CUE = re.compile(
    r"(?<!\w)(?:жив\w+|диву|нахожусь|я\s+(?:сейчас\s+)?в(?:о)?|мы\s+(?:сейчас\s+)?в(?:о)?|(?:мой\s+)?город|"
    r"другое время|часовой пояс|время по|по времени|i live in|i'?m in|i am in|we live in)(?!\w)"
)
_NOT_HOME = re.compile(
    r"(?<!\w)(?:хочу|хотела|хотел|мечт\w*|переех\w*|переезж\w*|поед\w*|еду|едем|лечу|летим|отпуск\w*|был|была|были|"
    r"раньше|жила|жил|летом|скоро|если|мама|муж|подруга|сестра|работала|училась|не живу|not|used to|want|"
    r"moving|was|lived|my mom|visit\w*)(?!\w)"
)
_MSK_DIFF = re.compile(
    r"(?:разниц\w*|плюс|минус|больше|меньше|впереди|позади)\D{0,25}(?:москв\w*|мск)\D{0,12}?"
    r"(?P<a>\d{1,2}|один|час|два|три|четыре|пять|шесть|семь)(?!\w)|"
    r"(?:москв\w*|мск)\s*(?P<sign>[+\-])\s*(?P<b>\d{1,2})"
)
_NUM = {"один": 1, "час": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7}
_CLOCK = re.compile(
    r"(?:у\s+(?:меня|нас)\s+(?:сейчас\s+)?(?:уже\s+)?(?:время\s+)?|"
    r"(?:здесь|тут)\s+(?:сейчас|уже)\s+|"
    r"^(?:сейчас|время)\s+(?:сейчас\s+)?(?:у\s+(?:меня|нас)\s+)?|"
    r"(?:сейчас|время)\s+у\s+(?:меня|нас)\s+)"
    r"(?:(?P<h>\d{1,2})[:.](?P<m>\d{2})(?=\s*(?:$|[.,!?;)]))|без\s+четверти\s+(?P<q>\d{1,2})(?!\d)|"
    r"(?P<e>\d{1,2})\s+(?P<part>утра|вечера|дня|ночи)(?=\s*(?:$|[.,!?;)])))"
)
_ZONE_BY_HOURS = {
    2: "Europe/Kaliningrad", 3: MOSCOW, 4: "Europe/Samara", 5: "Asia/Yekaterinburg", 6: "Asia/Omsk",
    7: "Asia/Novosibirsk", 8: "Asia/Irkutsk", 9: "Asia/Yakutsk", 10: "Asia/Vladivostok",
    11: "Asia/Magadan", 12: "Asia/Kamchatka",
}


def _zone_for_hours(hours: int) -> str:
    if hours in _ZONE_BY_HOURS:
        return _ZONE_BY_HOURS[hours]
    return f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}" if hours else "Etc/GMT"


def _offset_hours(tz_name: str, now_utc: datetime) -> float | None:
    try:
        return now_utc.astimezone(ZoneInfo(tz_name)).utcoffset().total_seconds() / 3600
    except Exception:
        return None


def _clock_hours(text: str, now_utc: datetime, current: float) -> int | None:
    """Whole-hour UTC offset from «у меня сейчас 11:08», the one closest to what we have now."""
    m = _CLOCK.search(text)
    if not m:
        return None
    minute: int | None
    if m.group("q"):
        hour, minute = int(m.group("q")) - 1, 45
    elif m.group("e"):
        hour, minute = int(m.group("e")), None
        if m.group("part") in ("вечера", "дня") and hour < 12:
            hour += 12
        if m.group("part") == "ночи" and hour == 12:
            hour = 0
    else:
        hour, minute = int(m.group("h")), int(m.group("m"))
    if not 0 <= hour <= 23 or (minute is not None and minute > 59):
        return None
    best: int | None = None
    ambiguous = bool(m.group("q")) and hour < 12
    for h in ((hour, hour + 12) if ambiguous else (hour,)):
        diff = h * 60 + (now_utc.minute if minute is None else minute) - (now_utc.hour * 60 + now_utc.minute)
        hours = round(diff / 60)
        if minute is not None and abs(diff - hours * 60) > 6:
            continue  # not a clock reading
        for x in (hours - 24, hours, hours + 24):
            if -11 <= x <= 13 and (best is None or abs(x - current) < abs(best - current)):
                best = x
    return best


def message_timezone(text: str, now_utc: datetime, current_tz: str) -> tuple[str, str] | None:
    """(zone, label or "") when she says where she lives or what time it is for her."""
    t = re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е").replace("’", "'")).strip()
    if not t or len(t) > 200:
        return None
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    cities: dict[str, str] = {}
    if not _NOT_HOME.search(t):
        for cue in _PLACE_CUE.finditer(t):
            near = re.split(r"[.!?;]", t[cue.end(): cue.end() + 30])[0]
            for rx, tz, label in _CITY_RES:
                m = rx.search(near)
                if m and m.start() <= 12 and len(m.group(0)) >= 4 and m.group(0) not in (
                    "central", "eastern", "mountain", "pacific",
                ):
                    cities.setdefault(tz, label)
                    break
    if len(cities) == 1:
        return next(iter(cities.items()))
    if cities:
        return None
    m = _MSK_DIFF.search(t)
    if m:
        raw = m.group("a") or m.group("b")
        n = int(raw) if raw.isdigit() else _NUM[raw]
        minus = m.group("sign") == "-" or bool(re.search(r"минус|меньше|позади", m.group(0)))
        if 0 < n <= 9:
            return _zone_for_hours(3 + (-n if minus else n)), ""
    current = _offset_hours(current_tz, now_utc)
    hours = _clock_hours(t, now_utc, 3 if current is None else current)
    if hours is None or hours == current:
        return None
    return _zone_for_hours(hours), ""


def zone_label(tz: str) -> str:
    """«Москве»-style phrase for the completion message («по Москве»)."""
    return {
        MOSCOW: "по Москве",
    }.get(tz, "")
