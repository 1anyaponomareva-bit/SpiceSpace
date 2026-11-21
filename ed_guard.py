"""Eating-disorder signs (Anya, 21.11.2026; approved in база-знаний-еда-тренировки.md).

Most new people come from threads about food and weight loss. Clear signs are caught in
code before any model reply, like the crisis check: fasting for days, vomiting,
laxatives or diuretics for weight, «burning off» food, self-hatred after eating, binges.
A weight goal is checked when height / weight / target are named: target BMI under
18.5 (WHO underweight) or losing more than ~1% of body weight a week over 12 weeks.
Sources: NICE NG69 (2017/2020), DSM-5 / DSM-5-TR, SCOFF (BMJ 1999), Russian clinical
guidelines «Ожирение» (2024: 5-10% of weight in 3-6 months). Pure functions.
"""

from __future__ import annotations

import re
import time

_NUM = r"(\d+|два|две|три|четыре|пять|шесть|семь|несколько)"

_ED_RU = [
    r"(вызыва\w*|вызвала|вызову|вызвать)\s+(у\s+себя\s+)?рвот",
    r"два\s+пальца\s+в\s+рот",
    r"(блюю|блевать|вырвать|вырываю|стошнить)\b.{0,25}(после|еду|съеден|чтобы\s+не\s+поправ|похуд)",
    r"(слабительн|мочегонн)\w*.{0,40}(похуд|вес|сбросить|не\s+поправ|скинуть|съеден)",
    r"(похуд|вес|сбросить|не\s+поправ|скинуть|съеден)\w*.{0,40}(слабительн|мочегонн)",
    r"(таблетк|препарат)\w*\s+(для|от)\s+(похуд|веса|аппетит)",
    r"не\s+(ем|ела|кушаю|ела\s+уже|ем\s+уже)\s+(уже\s+)?" + _NUM + r"\s+(дн|дня|дней|сут)",
    r"(голода\w*|не\s+ем)\s+(уже\s+)?" + _NUM + r"\s+(дн|дня|дней|сут)",
    r"не\s+(ем|буду\s+есть)\s+вообще|вообще\s+не\s+(ем|ела|буду\s+есть)",
    r"(пью|сижу)\s+только\s+(воду|на\s+воде)|только\s+на\s+воде",
    r"(отработ|сжечь|сжига|сожгу)\w*.{0,30}(съеден|что\s+съела|что\s+сожрала|переела|переедани|торт|калории\s+за)",
    r"наказ\w*\s+себя.{0,30}(трениров|зал|бег|голод)",
    r"(ненавижу|презираю)\s+себя.{0,40}(ед[аыуе]|съела|переела|жрала|сожрала|сорвалась)",
    r"(съела|переела|жрала|сожрала|сорвалась)\w*.{0,40}(ненавижу\s+себя|мерзко|отвратительн|противно\s+от\s+себя)",
    r"(после|за)\s+(еды|того,?\s+что\s+съела).{0,25}(ненавижу|отвращени|вина\s+съедает|противно)",
    r"(теряю|потеряла|теряется)\s+контроль.{0,20}(над\s+)?(ед|едой)",
]
_ED_EN = [
    r"make\s+myself\s+(throw\s+up|vomit|sick)",
    r"\bpurg(e|ing)\b",
    r"(laxative|diuretic)s?.{0,40}(weight|lose|slim)",
    r"(weight|lose|slim).{0,40}(laxative|diuretic)",
    r"(haven'?t|have\s+not|not)\s+(eaten|been\s+eating)\s+(in|for)\s+(\d+|two|three|four|five|several)\s+days",
    r"burn\s+off\s+(what|everything)\s+i\s+ate",
    r"hate\s+myself\s+(after|for)\s+eating",
]
_ACUTE = [
    r"не\s+(ем|ела)\s+(уже\s+)?(недел|\d{2}\s+дн)",
    r"(обморок|теряю\s+сознание|темнеет\s+в\s+глазах|чуть\s+не\s+упала|потеряла\s+сознание)",
    r"кровь.{0,20}рвот|рвот.{0,20}кров",
    r"(faint|passed\s+out|blood).{0,40}",
]
_ED_RE = re.compile("|".join(_ED_RU + _ED_EN))
_ACUTE_RE = re.compile("|".join(_ACUTE))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е"))


def detect(text: str) -> str:
    """'acute' / 'signs' / '' for one message."""
    t = _norm(text)
    if not t or not _ED_RE.search(t):
        return ""
    return "acute" if _ACUTE_RE.search(t) else "signs"


# ---------------------------------------------------------------- weight goal
def parse_body(text: str) -> dict:
    """Height (cm), weight, target weight and planned loss (kg) named in a message."""
    t = _norm(text).replace(",", ".")
    out: dict = {}
    m = re.search(r"рост\w*\s*(?:у\s+меня\s+)?(1[4-9]\d)|(1[4-9]\d)\s*см|\b1\.([4-9]\d)\s*м\b|height\s*(1[4-9]\d)|(1[4-9]\d)\s*cm", t)
    if m:
        g = next(x for x in m.groups() if x)
        out["height"] = int(g) if len(g) == 3 else 100 + int(g)
    m = re.search(r"\bс\s+(\d{2,3})\s*(?:кг\s*)?до\s+(\d{2,3})", t)
    if m:
        out["weight"], out["target"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(вешу|вес\s*(?:сейчас|у\s+меня)?|weigh)\s*(?:сейчас\s+)?(\d{2,3})", t)
    if m:
        out["weight"] = int(m.group(2))
    m = re.search(r"(весить|похудеть\s+до|сбросить\s+до|до)\s+(\d{2,3})\s*(кг|kg|килограмм)", t)
    if m:
        out["target"] = int(m.group(2))
    m = re.search(r"(минус|сбросить|скинуть|похудеть\s+на|лишних|lose)\s+(\d{1,2})\s*(кг|kg|кило|килограмм)", t)
    if m:
        out["loss"] = int(m.group(2))
    return {k: v for k, v in out.items() if v}


def weight_goal_concern(body: dict, weeks: int = 12) -> str:
    """'bmi' (target BMI < 18.5), 'fast' (> ~1% of weight a week) or ''."""
    h, w = body.get("height"), body.get("weight")
    target = body.get("target")
    if not target and w and body.get("loss"):
        target = w - body["loss"]
    if h and target and 25 <= target <= 200:
        if target / ((h / 100) ** 2) < 18.5:
            return "bmi"
    if w and target and w > target:
        if (w - target) / w > 0.01 * weeks:
            return "fast"
    return ""


# ---------------------------------------------------------------- replies
REPLY_RU = {
    "signs": (
        "Спасибо, что рассказала мне, я понимаю, что это непросто 🤍\n\n"
        "С этим лучше не оставаться одной: когда еда и вес так давят, это сильно бьёт и по телу, и по "
        "настроению, и из этого круга сложно выйти самой. Здесь помогает врач: можно начать с терапевта или "
        "психотерапевта, они направят к специалисту по пищевому поведению.\n\n"
        "Если захочется поговорить с кем-то прямо сейчас: в России круглосуточно и бесплатно "
        "+7 495 989-50-50 (психологическая помощь МЧС), в других странах - findahelpline.com.\n\n"
        "Советы по похудению я тут давать не буду, но я рядом. Расскажешь, как ты сейчас?"
    ),
    "acute": (
        "Мне за тебя тревожно. Головокружение, обмороки и долгие дни без еды - это сигнал, что телу "
        "нужна помощь прямо сейчас.\n\n"
        "Если станет хуже, темнеет в глазах или болит в груди - вызови скорую: 103 или 112. Если можешь, "
        "съешь сейчас что-то небольшое и попей, и скажи кому-нибудь рядом, что тебе плохо.\n\n"
        "А дальше обязательно к врачу. Я здесь, напиши, как ты."
    ),
    "bmi": (
        "Слышу, что хочется сильно и быстро 🤍 Но тут я честно скажу: такой вес будет ниже здоровой "
        "нормы для твоего роста, и это опасно для сердца и гормонов.\n\n"
        "С такой целью по весу я не пойду, но могу быть рядом с другой. Например, чувствовать себя "
        "сильнее и бодрее: сон, движение, которое нравится, еда без пропусков. Что тебе ближе?\n\n"
        "И если мысли про вес и еду занимают много места, с этим очень помогает врач или специалист "
        "по пищевому поведению. Это нормально - обратиться."
    ),
    "fast": (
        "Слышу, что хочется побыстрее 🤍 Но минус {loss} кг за 12 недель - слишком быстрый темп: "
        "так чаще уходят мышцы, а вес потом возвращается.\n\n"
        "Бережно и реально - минус 5-10% веса за 3 месяца, у тебя это примерно {lo}-{hi} кг. "
        "Давай поставим такую цель или выберем другую, например про сон или движение. Что тебе ближе?"
    ),
}
REPLY_EN = {
    "signs": (
        "Thank you for telling me, I know it isn't easy 🤍\n\n"
        "This is something better not to carry alone: when food and weight press this hard, it hits both "
        "body and mood, and it's hard to get out of that loop by yourself. A doctor can really help: you "
        "can start with a GP or a therapist, they'll refer you to an eating-disorder specialist.\n\n"
        "If you want to talk to someone right now: findahelpline.com has free helplines in your "
        "country (in the US, call or text 988).\n\n"
        "I won't give weight-loss tips here, but I'm with you. How are you right now?"
    ),
    "acute": (
        "I'm worried about you. Dizziness, fainting and days without food mean your body needs help "
        "right now.\n\n"
        "If it gets worse, your vision goes dark or your chest hurts, call emergency services: 112 "
        "(911 in the US). If you can, eat something small and drink now, and tell someone near you "
        "that you feel unwell.\n\n"
        "And then please see a doctor. I'm here, tell me how you are."
    ),
    "bmi": (
        "I hear how much you want it, and fast 🤍 But honestly: that weight would be below a healthy "
        "range for your height, and that's risky for your heart and hormones.\n\n"
        "I won't go toward that weight goal, but I can be with you on another one. For example, "
        "feeling stronger and more energetic: sleep, movement you enjoy, regular meals. What feels "
        "closer?\n\n"
        "And if thoughts about weight and food take up a lot of space, a doctor or an eating-disorder "
        "specialist really helps. It's okay to reach out."
    ),
    "fast": (
        "I hear you want it fast 🤍 But minus {loss} kg in 12 weeks is too fast: that way you lose "
        "more muscle and the weight tends to come back.\n\n"
        "Gentle and realistic is minus 5-10% of your weight in 3 months, about {lo}-{hi} kg for you. "
        "Shall we set that goal, or pick another one, like sleep or movement? What feels closer?"
    ),
}


def reply(kind: str, lang: str, body: dict | None = None) -> str:
    table = REPLY_RU if str(lang or "").lower().startswith(("ru", "uk", "be")) else REPLY_EN
    text = table[kind]
    if kind == "fast":
        b = body or {}
        w = b.get("weight") or 0
        target = b.get("target") or (w - (b.get("loss") or 0))
        text = text.format(loss=max(w - target, 0), lo=max(round(w * 0.05), 1), hi=max(round(w * 0.10), 1))
    return text


# One fixed reply per day; after that the chat model (with the ED rule) carries on.
_last_reply: dict[int, float] = {}
REPEAT_WINDOW_SECONDS = 24 * 3600


def should_reply(cid: int, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    prev = _last_reply.get(cid)
    if prev is not None and now - prev < REPEAT_WINDOW_SECONDS:
        return False
    _last_reply[cid] = now
    return True


# Prompt rule (English: it goes into English and Russian prompts alike).
ED_RULE_EN = (
    "EATING-DISORDER SAFETY (any goal, outranks food and fitness advice): if she plans or "
    "describes fasting for a day or more, vomiting, laxatives, diuretics or pills to lose weight, "
    "exercising to «burn off» food or as punishment, strong fear, guilt or self-hatred after "
    "eating, eating she cannot stop, or a weight goal that would put her below a healthy weight "
    "for her height or lose more than about 1% of body weight a week: no shame, no diagnosis "
    "words, no weight-loss steps, no calorie counting, never praise not eating. Gently suggest a "
    "doctor or an eating-disorder specialist (a GP or therapist can refer), and offer to stay "
    "with a different goal: sleep, regular meals, movement she enjoys, mood. With fainting, chest "
    "pain or days without food, give the emergency number (103 or 112 in Russia, 911 in the US). "
    "A realistic weight goal is about 5-10% of body weight in 3-6 months."
)
