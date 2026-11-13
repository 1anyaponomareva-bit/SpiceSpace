"""Subscription payments through lava.top (API v3).

Chat flow: period -> payment method -> email (asked once) -> summary with a
"pay" link (+ optional promo code). Access is granted by lava webhooks, never by
the chat flow itself.

Configuration (Railway variables, never in code):
  LAVA_API_KEY       API key from the lava.top cabinet
  LAVA_WEBHOOK_KEY   secret lava sends in the X-Api-Key header of webhooks
  LAVA_OFFER_1M      offer id of the 1-month subscription
  LAVA_OFFER_3M      offer id of the 3-month subscription
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

log = logging.getLogger(__name__)

LAVA_BASE_URL = os.getenv("LAVA_BASE_URL", "https://gate.lava.top").rstrip("/")
GRACE_DAYS = 2  # renewal may land a little after the period ends

PLANS: dict[str, dict] = {
    "1m": {
        "env": "LAVA_OFFER_1M",
        "periodicity": "MONTHLY",
        "days": 30,
        "rub": 590,
        "label_ru": "1 месяц",
        "label_en": "1 month",
    },
    "3m": {
        "env": "LAVA_OFFER_3M",
        "periodicity": "PERIOD_90_DAYS",
        "days": 90,
        "rub": 1490,
        "label_ru": "3 месяца",
        "label_en": "3 months",
    },
}

METHODS: dict[str, dict] = {
    "ru": {"currency": "RUB", "label_ru": "карта российского банка", "label_en": "Russian bank card"},
    "intl": {"currency": "USD", "label_ru": "иностранная карта", "label_en": "international card"},
}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_PROMO_RE = re.compile(r"^[A-Za-z0-9_\-]{3,36}$")

# cid -> {"plan", "method", "step": "email" | "promo" | "", "promo", "email"}
checkout: dict[int, dict] = {}


def api_key() -> str:
    return os.getenv("LAVA_API_KEY", "").strip()


# Offers found in the lava account by period: plan -> {"id", "rub", "usd"}.
_found: dict[str, dict] = {}


def offer_id(plan: str) -> str:
    spec = PLANS.get(plan)
    if not spec:
        return ""
    return os.getenv(spec["env"], "").strip() or str((_found.get(plan) or {}).get("id") or "")


def plan_rub(plan: str) -> object:
    """RUB price shown on the button: the one set in lava when known."""
    return (_found.get(plan) or {}).get("rub") or PLANS[plan]["rub"]


def plan_has_usd(plan: str) -> bool:
    info = _found.get(plan)
    return True if info is None else bool(info.get("usd"))


def enabled() -> bool:
    """Checkout is on once the API key is set; offers are looked up in the lava account."""
    return bool(api_key())


def available_plans() -> list[str]:
    return [p for p in PLANS if offer_id(p)]


_PLAN_BY_PERIOD = {spec["periodicity"]: plan for plan, spec in PLANS.items()}


def pick_offers(products: list[dict]) -> dict[str, dict]:
    """Choose one offer per plan from /api/v2/products items, by subscription period.

    When several offers share a period, the one whose RUB price equals ours wins.
    """
    found: dict[str, dict] = {}
    for item in products or []:
        data = item.get("data") if isinstance(item, dict) and "data" in item else item
        if not isinstance(data, dict):
            continue
        for offer in data.get("offers") or []:
            if not isinstance(offer, dict) or not offer.get("id"):
                continue
            prices = [p for p in (offer.get("prices") or []) if isinstance(p, dict)]
            for price in prices:
                plan = _PLAN_BY_PERIOD.get(str(price.get("periodicity") or ""))
                if not plan or price.get("currency") != "RUB":
                    continue
                usd = any(
                    p.get("currency") in ("USD", "EUR") and p.get("periodicity") == price.get("periodicity")
                    for p in prices
                )
                cand = {
                    "id": str(offer["id"]),
                    "rub": price.get("amount"),
                    "usd": usd,
                    "title": str(data.get("title") or ""),
                    "name": str(offer.get("name") or ""),
                }
                prev = found.get(plan)
                exact = price.get("amount") == PLANS[plan]["rub"]
                if prev is None or (exact and prev.get("rub") != PLANS[plan]["rub"]):
                    found[plan] = cand
    return found


async def load_offers(force: bool = False) -> dict[str, dict]:
    """Fetch subscription offers from the lava account once (or again with force)."""
    global _found
    if _found and not force:
        return _found
    key = api_key()
    if not key:
        return {}
    items: list[dict] = []
    url = f"{LAVA_BASE_URL}/api/v2/products"
    params: dict | None = {"productTypes": "SUBSCRIPTION", "showAllSubscriptionPeriods": "true"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(10):
            r = await client.get(url, params=params, headers={"X-Api-Key": key, "Accept": "application/json"})
            if r.status_code >= 400:
                raise LavaError(r.status_code, r.text[:300])
            data = r.json()
            items.extend(data.get("items") or [])
            nxt = data.get("nextPage")
            if not nxt:
                break
            url, params = str(nxt), None
    _found = pick_offers(items)
    log.info("lava offers: %s", {p: (v["id"], v["rub"], v["usd"]) for p, v in _found.items()})
    return _found


async def get_contract(contract_id: str) -> dict | None:
    """The contract as lava sees it (used to check a webhook is real). None if not found."""
    key = api_key()
    if not key or not re.fullmatch(r"[0-9a-fA-F\-]{8,64}", contract_id or ""):
        return None
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{LAVA_BASE_URL}/api/v2/invoices/{contract_id}",
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise LavaError(r.status_code, r.text[:300])
    data = r.json()
    return data if isinstance(data, dict) else None


def webhook_secret_set() -> bool:
    return bool(os.getenv("LAVA_WEBHOOK_KEY", "").strip())


def contract_confirms(event: dict, contract: dict | None) -> bool:
    """A webhook without the shared secret is trusted only if lava's own record agrees."""
    if not contract:
        return False
    if event["kind"] in ("paid", "renewed"):
        return str(contract.get("status") or "").upper() == "COMPLETED"
    return True


def valid_email(text: str) -> bool:
    return bool(_EMAIL_RE.match((text or "").strip()))


def normalize_promo(text: str) -> str:
    code = (text or "").strip().upper()
    return code if _PROMO_RE.match(code) else ""


# ---------------------------------------------------------------- texts

_T = {
    "ru": {
        "choose_plan": "Выбери период подписки на Спейс 💚",
        "plan_btn": "{label} — {rub} ₽",
        "choose_method": "Как будешь платить?",
        "method_ru_btn": "🇷🇺 Карта российского банка",
        "method_intl_btn": "🌍 Иностранная карта",
        "ask_email": (
            "Напиши свой email — на него придёт чек об оплате. "
            "Спрошу только один раз."
        ),
        "bad_email": "Похоже, в адресе опечатка. Напиши email ещё раз, например name@mail.ru",
        "ask_promo": "Напиши промокод одним словом.",
        "bad_promo": "Такой промокод не нашёлся. Проверь и напиши ещё раз — или оплати без него.",
        "no_promo_btn": "Без промокода",
        "summary": (
            "Подписка на Спейс\n\n"
            "Период: {plan}\n"
            "Оплата: {method}\n"
            "Сумма: {amount}\n"
            "{promo_line}"
            "\nПодписка продлевается автоматически по той же цене. "
            "Отменить можно в любой момент — доступ останется до конца оплаченного срока."
        ),
        "promo_line": "Промокод: {promo}\n",
        "pay_btn": "💳 Перейти к оплате",
        "promo_btn": "🎟 Ввести промокод",
        "change_btn": "↩️ Изменить",
        "error": "Не получилось создать оплату. Попробуй ещё раз чуть позже 🙏 Если не выйдет, напиши Ане: @panomareva",
        "unavailable": "Оплата пока недоступна. Скоро включим 💚",
        "paid": (
            "Оплата прошла 💚 Подписка на Спейс активна до {end}. "
            "Продлевается автоматически, отменить можно в любой момент."
        ),
        "renewed": "Подписка продлена до {end} 💚",
        "failed": "Оплата не прошла. Если хочешь, попробуй ещё раз 👇 Если что-то не так, напиши Ане: @panomareva",
        "retry_btn": "💳 Оформить подписку",
        "renew_failed": (
            "Не получилось списать оплату за продление подписки. "
            "Доступ к Спейс сохранится до {end}."
        ),
        "cancelled": "Автопродление подписки отключено. Доступ к Спейс сохранится до {end}.",
    },
    "en": {
        "choose_plan": "Choose your Space subscription 💚",
        "plan_btn": "{label} — {rub} ₽",
        "choose_method": "How would you like to pay?",
        "method_ru_btn": "🇷🇺 Russian bank card",
        "method_intl_btn": "🌍 International card",
        "ask_email": "Send me your email — the receipt goes there. I'll only ask once.",
        "bad_email": "Looks like a typo. Please send your email again, e.g. name@gmail.com",
        "ask_promo": "Send your promo code as one word.",
        "bad_promo": "That promo code wasn't found. Check it and send again — or pay without it.",
        "no_promo_btn": "No promo code",
        "summary": (
            "Space subscription\n\n"
            "Period: {plan}\n"
            "Payment: {method}\n"
            "Amount: {amount}\n"
            "{promo_line}"
            "\nThe subscription renews automatically at the same price. "
            "You can cancel anytime — access stays until the paid period ends."
        ),
        "promo_line": "Promo code: {promo}\n",
        "pay_btn": "💳 Go to payment",
        "promo_btn": "🎟 Enter promo code",
        "change_btn": "↩️ Change",
        "error": "Couldn't create the payment. Please try again a bit later 🙏 If it still fails, message Anya: @panomareva",
        "unavailable": "Payments aren't available yet. Coming soon 💚",
        "paid": (
            "Payment received 💚 Your Space subscription is active until {end}. "
            "It renews automatically; you can cancel anytime."
        ),
        "renewed": "Subscription renewed until {end} 💚",
        "failed": "The payment didn't go through. You can try again 👇 If something's wrong, message Anya: @panomareva",
        "retry_btn": "💳 Subscribe",
        "renew_failed": (
            "We couldn't charge the subscription renewal. "
            "You keep access to Space until {end}."
        ),
        "cancelled": "Auto-renewal is off. You keep access to Space until {end}.",
    },
}


def t(lang: str, key: str, **kw) -> str:
    table = _T["ru"] if str(lang or "").lower().startswith("ru") else _T["en"]
    text = table[key]
    return text.format(**kw) if kw else text


def plan_label(plan: str, lang: str) -> str:
    spec = PLANS[plan]
    return spec["label_ru"] if str(lang).lower().startswith("ru") else spec["label_en"]


def method_label(method: str, lang: str) -> str:
    spec = METHODS[method]
    return spec["label_ru"] if str(lang).lower().startswith("ru") else spec["label_en"]


def format_amount(amount: object, currency: str) -> str:
    try:
        value = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    num = f"{value:.0f}" if value == int(value) else f"{value:.2f}"
    sign = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(currency, currency)
    return f"{num} ₽" if currency == "RUB" else f"{sign}{num}"


# ---------------------------------------------------------------- API


class LavaError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"lava {status}: {message}")
        self.status = status
        self.message = message


@dataclass
class Invoice:
    contract_id: str
    payment_url: str
    amount: object
    currency: str


def invoice_body(
    *, cid: int, plan: str, method: str, email: str, lang: str, promo: str = ""
) -> dict:
    spec = PLANS[plan]
    body: dict = {
        "email": email,
        "offerId": offer_id(plan),
        "currency": METHODS[method]["currency"],
        "periodicity": spec["periodicity"],
        "buyerLanguage": "RU" if str(lang).lower().startswith("ru") else "EN",
        "clientUtm": {"utm_source": "telegram", "utm_content": str(cid)},
    }
    if promo:
        body["promoCode"] = promo
    return body


async def create_invoice(body: dict) -> Invoice:
    key = api_key()
    if not key:
        raise LavaError(0, "LAVA_API_KEY is not set")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{LAVA_BASE_URL}/api/v3/invoice",
            json=body,
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
    if r.status_code >= 400:
        try:
            msg = str(r.json().get("error") or r.text)
        except Exception:
            msg = r.text
        raise LavaError(r.status_code, msg[:300])
    data = r.json()
    total = data.get("amountTotal") or {}
    url = str(data.get("paymentUrl") or "")
    if not url or not data.get("id"):
        raise LavaError(r.status_code, "no paymentUrl in response")
    return Invoice(
        contract_id=str(data["id"]),
        payment_url=url,
        amount=total.get("amount"),
        currency=str(total.get("currency") or body.get("currency") or ""),
    )


# ---------------------------------------------------------------- webhooks


def webhook_authorized(headers: dict) -> bool:
    """lava sends the secret set in its cabinet as X-Api-Key (or as Basic auth password)."""
    secret = os.getenv("LAVA_WEBHOOK_KEY", "").strip()
    if not secret:
        return False
    low = {str(k).lower(): str(v) for k, v in headers.items()}
    got = low.get("x-api-key", "").strip()
    if got and hmac.compare_digest(got, secret):
        return True
    auth = low.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
        except Exception:
            return False
        password = decoded.split(":", 1)[1] if ":" in decoded else decoded
        return hmac.compare_digest(password, secret)
    return False


_KIND_BY_EVENT = {
    "payment.success": "paid",
    "payment.failed": "failed",
    "subscription.recurring.payment.success": "renewed",
    "subscription.recurring.payment.failed": "renew_failed",
    "subscription.cancelled": "cancelled",
}


def parse_event(body: dict) -> dict:
    """Normalize a lava webhook body. kind: paid / renewed / failed / renew_failed / cancelled / other."""
    event = str(body.get("eventType") or body.get("event_type") or "")
    kind = _KIND_BY_EVENT.get(event, "other")
    if kind == "paid" and str(body.get("status") or "") not in ("subscription-active", "completed"):
        kind = "other"
    buyer = body.get("buyer") or {}
    utm = body.get("clientUtm") or {}
    tid = str(utm.get("utm_content") or "").strip()
    return {
        "event": event,
        "kind": kind,
        "contract_id": str(body.get("contractId") or ""),
        "parent_id": str(body.get("parentContractId") or ""),
        "email": str(buyer.get("email") or "").strip().lower(),
        "telegram_id": tid if tid.isdigit() else "",
        "timestamp": str(body.get("timestamp") or body.get("cancelledAt") or ""),
        "will_expire": str(body.get("willExpireAt") or ""),
        "amount": body.get("amount"),
        "currency": str(body.get("currency") or ""),
        "error": str(body.get("errorMessage") or ""),
    }


def _parse_day(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def paid_until(plan: str, paid_on: str, current_end: str, today: date) -> str:
    """New end date after a successful payment; never shortens access and never stacks grace."""
    days = PLANS.get(plan, PLANS["1m"])["days"]
    start = _parse_day(paid_on) or today
    new_end = start + timedelta(days=days + GRACE_DAYS)
    cur = _parse_day(current_end)
    if cur and cur > new_end:
        new_end = cur
    return new_end.isoformat()


# ---------------------------------------------------------------- profile tag
# The profile's `plan` column keeps "lava_<plan>|<first contract id>|<email>", so a
# renewal finds its user and the email is known even without the lava_payments table.


def profile_tag(plan: str, root_contract: str, email: str, cancelled: bool = False) -> str:
    tag = f"lava_{plan}|{root_contract}|{email}"
    return f"{tag}|cancelled" if cancelled else tag


def parse_profile_tag(value: object) -> dict | None:
    raw = str(value or "")
    if not raw.startswith("lava_"):
        return None
    parts = raw.split("|")
    plan = parts[0][len("lava_"):]
    return {
        "plan": plan if plan in PLANS else "1m",
        "root": parts[1] if len(parts) > 1 else "",
        "email": parts[2].lower() if len(parts) > 2 else "",
        "cancelled": len(parts) > 3 and parts[3] == "cancelled",
    }


async def cancel_subscription(root_contract: str, email: str) -> None:
    """Stop auto-renewal in lava. Raises LavaError when lava refuses."""
    key = api_key()
    if not key:
        raise LavaError(0, "LAVA_API_KEY is not set")
    if not re.fullmatch(r"[0-9a-fA-F\-]{8,64}", root_contract or "") or not valid_email(email):
        raise LavaError(0, "no subscription to cancel")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.delete(
            f"{LAVA_BASE_URL}/api/v1/subscriptions",
            params={"contractId": root_contract, "email": email},
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
    if r.status_code >= 400:
        raise LavaError(r.status_code, r.text[:300])


def plan_from_amount(amount: object, currency: str) -> str:
    """Best guess of the plan when no stored contract says it (3-month costs far more)."""
    try:
        value = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "1m"
    if currency == "RUB":
        one = float(plan_rub("1m") or PLANS["1m"]["rub"])
        return "3m" if value > one * 1.5 else "1m"
    return "1m"
