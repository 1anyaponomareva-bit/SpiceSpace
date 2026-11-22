"""Daily numbers for the owner's content dashboard, split by the ?start= label.

Pure functions: main.py gathers profiles, message times and lava payments, this
module only counts. Days are the owner's local days (OWNER_TZ, UTC+7 by default).
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OWNER_TZ = os.getenv("OWNER_TZ", "Asia/Saigon").strip() or "Asia/Saigon"
COHORT_DAYS = 14
NO_LABEL = "none"
SRC_PREFIX = "src_"
_LABEL_RE = re.compile(r"[^a-z0-9_]")
# start args used inside the app, not traffic sources
_INTERNAL_ARGS = {
    "change_weekly", "change-weekly", "weekly_goal",
    "change_12w", "change-12w", "12w_goal", "main_goal",
    "subscribe", "status", "reset",
}


def clean_label(arg: str | None) -> str:
    """'th_1311_a' -> 'th_1311_a'; junk -> ''. Lowercase, [a-z0-9_], max 40."""
    raw = str(arg or "").strip().lower()
    if not raw or raw in _INTERNAL_ARGS:
        return ""
    label = _LABEL_RE.sub("", raw.replace("-", "_"))[:40]
    return label


def profile_label(profile: dict) -> str:
    flags = profile.get("cycle_flags") if isinstance(profile, dict) else None
    keys: list[str] = []
    if isinstance(flags, dict):
        keys += [k for k, v in flags.items() if v and str(k).startswith(SRC_PREFIX)]
    if isinstance(profile, dict):
        keys += [k for k, v in profile.items() if v and str(k).startswith(SRC_PREFIX)]
    for k in sorted(set(keys)):
        label = str(k)[len(SRC_PREFIX):]
        if label:
            return label
    return NO_LABEL


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(OWNER_TZ)
    except Exception:
        return ZoneInfo("Asia/Saigon")


def local_day(ts: object) -> date | None:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz()).date()


def owner_yesterday(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(_tz()).date() - timedelta(days=1)


def _onboarded(p: dict) -> bool:
    return bool(str(p.get("name") or "").strip() and str(p.get("main_goal") or "").strip())


# onboarding funnel, in order. The bot marks each step in cycle_flags as "ob_<step>"
# (from 21.11.2026); older profiles are read from the fields the step leaves behind.
FUNNEL = ("start", "age", "name", "goal_talk", "main_goal", "weekly", "time")


# payment steps (from 22.11.2026): the bot keeps cycle_flags["pay_<step>"] = time of the
# first time she reached it. Payment itself is counted from lava_payments (paid_1m/3m).
PAY_STEPS = ("day3", "day4", "pause", "open", "plan", "email", "link")


def _pay_time(p: dict, step: str) -> object:
    flags = p.get("cycle_flags")
    return flags.get("pay_" + step) if isinstance(flags, dict) else None


def _flag(p: dict, key: str) -> bool:
    flags = p.get("cycle_flags")
    return bool((isinstance(flags, dict) and flags.get(key)) or p.get(key))


def funnel_reached(p: dict) -> str:
    """The last onboarding step this person got through ("start" .. "time")."""
    has = {
        "age": _flag(p, "ob_age"),
        "name": _flag(p, "ob_name"),
        "goal_talk": _flag(p, "ob_goal_talk"),
        "main_goal": _flag(p, "ob_main_goal") or bool(str(p.get("main_goal") or "").strip()),
        "weekly": _flag(p, "ob_weekly") or bool(str(p.get("weekly_goal") or "").strip()),
        "time": _flag(p, "ob_time") or bool(str(p.get("cycle_start_date") or "").strip()),
    }
    last = "start"
    for step in FUNNEL[1:]:
        if has[step]:
            last = step  # a later step implies the earlier ones
    return last


def _add_funnel(b: dict, p: dict) -> None:
    last = funnel_reached(p)
    idx = FUNNEL.index(last)
    for step in FUNNEL[: idx + 1]:
        b["f_" + step] += 1
    if last != "time":
        b["stuck_" + last] += 1
    if _flag(p, "ob_age_no"):
        b["age_under"] += 1
    if str(p.get("language_code") or "").lower().startswith("ru"):
        b["lang_ru"] += 1
    else:
        b["lang_other"] += 1


def _plan_of(row: dict) -> str:
    plan = str(row.get("plan") or "").strip().lower()
    if plan in ("1m", "3m"):
        return plan
    try:
        amount = float(row.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    cur = str(row.get("currency") or "").upper()
    if cur == "RUB":
        return "3m" if amount >= 1000 else "1m"
    return "3m" if amount >= 15 else "1m"


def _is_renewal(row: dict) -> bool:
    parent = str(row.get("parent_contract_id") or "").strip()
    return bool(parent) and parent != str(row.get("contract_id") or "").strip()


def _money(row: dict) -> tuple[str, float]:
    try:
        amount = float(row.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return str(row.get("currency") or "RUB").upper(), amount


def _paid_status(row: dict) -> bool:
    """Rows are written at invoice creation ('new') and updated by webhooks; paid_until
    is only ever written by a successful payment, so it survives a later cancel."""
    status = str(row.get("status") or "").lower()
    return bool(str(row.get("paid_until") or "").strip()) or "success" in status


def _paid_day(row: dict) -> date | None:
    return local_day(row.get("updated_at") or row.get("created_at"))


def _empty_counts() -> dict:
    return {
        "starts": 0,
        "onboarded": 0,
        "trial_started": 0,
        "active_d2": 0,
        "active_d4": 0,
        "paid_1m": 0,
        "paid_3m": 0,
        "renewals": 0,
        "revenue_rub": 0.0,
        "revenue_usd": 0.0,
        **{"f_" + s: 0 for s in FUNNEL},
        **{"stuck_" + s: 0 for s in FUNNEL[:-1]},
        "age_under": 0,
        "lang_ru": 0,
        "lang_other": 0,
        **{"pay_" + s: 0 for s in PAY_STEPS},
    }


def _add_money(bucket: dict, row: dict) -> None:
    cur, amount = _money(row)
    if cur == "USD":
        bucket["revenue_usd"] = round(bucket["revenue_usd"] + amount, 2)
    else:
        bucket["revenue_rub"] = round(bucket["revenue_rub"] + amount, 2)


def build_spice_stats(
    profiles: dict,
    message_days: dict[str, set[date]],
    payments: list[dict] | None,
    report_day: date,
) -> dict:
    """payments=None means the lava_payments history is not available."""
    labels: dict[str, str] = {}
    start_days: dict[str, date | None] = {}
    for uid, p in (profiles or {}).items():
        if not isinstance(p, dict):
            continue
        labels[str(uid)] = profile_label(p)
        start_days[str(uid)] = local_day(p.get("created_at"))

    # ---- what happened on report_day, by label
    day: dict[str, dict] = {}

    def bucket(store: dict, label: str) -> dict:
        return store.setdefault(label, _empty_counts())

    for uid, p in profiles.items():
        if not isinstance(p, dict):
            continue
        uid = str(uid)
        if start_days.get(uid) != report_day:
            continue
        b = bucket(day, labels[uid])
        b["starts"] += 1
        if _onboarded(p):
            b["onboarded"] += 1
            b["trial_started"] += 1
        _add_funnel(b, p)

    # payment steps reached on report_day, by label (anyone, whatever day she started)
    for uid, p in profiles.items():
        if not isinstance(p, dict):
            continue
        for step in PAY_STEPS:
            t = _pay_time(p, step)
            if t and t is not True and local_day(t) == report_day:
                bucket(day, labels.get(str(uid), NO_LABEL))["pay_" + step] += 1

    for uid, days in (message_days or {}).items():
        uid = str(uid)
        start = start_days.get(uid)
        if start is None:
            continue
        b = None
        if start + timedelta(days=1) == report_day and report_day in days:
            b = bucket(day, labels.get(uid, NO_LABEL))
            b["active_d2"] += 1
        if start + timedelta(days=3) == report_day and report_day in days:
            b = b or bucket(day, labels.get(uid, NO_LABEL))
            b["active_d4"] += 1

    for row in payments or []:
        if not isinstance(row, dict) or not _paid_status(row):
            continue
        if _paid_day(row) != report_day:
            continue
        uid = str(row.get("telegram_id") or "")
        b = bucket(day, labels.get(uid, NO_LABEL))
        if _is_renewal(row):
            b["renewals"] += 1
        else:
            b["paid_" + _plan_of(row)] += 1
        _add_money(b, row)

    # ---- cohorts: people who started on each of the last COHORT_DAYS days
    cohorts: dict[tuple[str, str], dict] = {}
    first_day = report_day - timedelta(days=COHORT_DAYS - 1)
    for uid, p in profiles.items():
        if not isinstance(p, dict):
            continue
        uid = str(uid)
        start = start_days.get(uid)
        if start is None or not (first_day <= start <= report_day):
            continue
        label = labels[uid]
        c = cohorts.setdefault((start.isoformat(), label), _empty_counts())
        c["starts"] += 1
        if _onboarded(p):
            c["onboarded"] += 1
            c["trial_started"] += 1
        _add_funnel(c, p)
        for step in PAY_STEPS:
            t = _pay_time(p, step)
            if t and (t is True or (local_day(t) or report_day) <= report_day):
                c["pay_" + step] += 1
        days = message_days.get(uid) or set()
        if start + timedelta(days=1) in days:
            c["active_d2"] += 1
        if start + timedelta(days=3) in days:
            c["active_d4"] += 1
    for row in payments or []:
        if not isinstance(row, dict) or not _paid_status(row):
            continue
        paid_day = _paid_day(row)
        if paid_day is None or paid_day > report_day:
            continue
        uid = str(row.get("telegram_id") or "")
        start = start_days.get(uid)
        if start is None or not (first_day <= start <= report_day):
            continue
        c = cohorts.setdefault((start.isoformat(), labels.get(uid, NO_LABEL)), _empty_counts())
        if _is_renewal(row):
            c["renewals"] += 1
        else:
            c["paid_" + _plan_of(row)] += 1
        _add_money(c, row)

    cohort_rows = []
    for (start_iso, label), c in sorted(cohorts.items(), reverse=True):
        start = date.fromisoformat(start_iso)
        row = {"start_day": start_iso, "label": label, **c}
        # not known yet: the day has not finished
        if start + timedelta(days=1) > report_day:
            row["active_d2"] = None
        if start + timedelta(days=3) > report_day:
            row["active_d4"] = None
        cohort_rows.append(row)

    # ---- paying right now, by label (works without payment history)
    paying_now: dict[str, dict] = {}
    for uid, p in profiles.items():
        if not isinstance(p, dict):
            continue
        tag = str(p.get("plan") or "")
        end = str(p.get("subscription_end") or "")[:10]
        if not tag.startswith("lava_") or not end or end < report_day.isoformat():
            continue
        plan = tag.split("|", 1)[0][len("lava_"):] or "?"
        lb = paying_now.setdefault(labels.get(str(uid), NO_LABEL), {"1m": 0, "3m": 0})
        lb[plan] = lb.get(plan, 0) + 1

    totals = _empty_counts()
    for b in day.values():
        for k, v in b.items():
            totals[k] = round(totals[k] + v, 2) if isinstance(v, float) else totals[k] + v

    return {
        "report_day": report_day.isoformat(),
        "timezone": OWNER_TZ,
        "payments_history": payments is not None,
        "day_by_label": day,
        "day_totals": totals,
        "cohorts": cohort_rows,
        "paying_now_by_label": paying_now,
        "users_total": sum(1 for p in profiles.values() if isinstance(p, dict)),
        "notes": {
            "day_by_label": "события за report_day: старты, оплаты и продления в этот день; "
            "active_d2/active_d4 — люди, пришедшие 1 и 3 дня назад и написавшие в этот день",
            "cohorts": "люди, пришедшие в start_day, и что с ними стало к сегодня; "
            "null — день ещё не наступил",
            "trial_started": "пробный период начинается по окончании знакомства, поэтому равен onboarded",
            "funnel": "f_start..f_time - сколько людей дошли до шага знакомства: старт, «мне есть 18», "
            "имя, начала разговор о цели, главная цель, цель недели, время утра (знакомство закончено). "
            "stuck_<шаг> - на этом шаге остановились и дальше не пошли. Шаги «18+», имя и разговор о цели "
            "бот отмечает с 21.11.2026, у пришедших раньше они видны, только если человек прошёл дальше. "
            "onboarded - старое поле (имя и главная цель), f_time - полностью закончили знакомство",
            "age_under": "нажали «мне нет 18» или написали, что им меньше 18",
            "f_age_from_21_11": "с 21.11.2026 кнопки «Мне есть 18» нет: бот сразу спрашивает имя, "
            "поэтому у новых людей f_age = f_start (а про 18+ говорит после знакомства)",
            "lang_ru / lang_other": "язык телеграма русский / любой другой или неизвестен (знакомство тогда идёт на английском)",
            "label_none": "люди без метки ?start= (в том числе все, кто пришёл до 13.11.2026)",
            "pay_steps": "шаги оплаты, бот отмечает с 22.11.2026: pay_day3 / pay_day4 - получили сообщение "
            "3-го и 4-го дня пробного периода, pay_pause - получили утреннее сообщение паузы (дни 5-7), "
            "pay_open - нажали оформить или кнопку тарифа, pay_plan - выбрали тариф, pay_email - ввели почту, "
            "pay_link - получили ссылку lava, оплатили - paid_1m / paid_3m. В day_by_label - кто дошёл до шага "
            "в этот день (считается первый раз), в cohorts - сколько из пришедших в start_day дошли до шага",
        },
    }
