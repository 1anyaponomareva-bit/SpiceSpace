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


def offer_id(plan: str) -> str:
    spec = PLANS.get(plan)
    return os.getenv(spec["env"], "").strip() if spec else ""


def enabled() -> bool:
    """Checkout is shown only when the key and at least one offer are configured."""
    return bool(api_key()) and any(offer_id(p) for p in PLANS)


def available_plans() -> list[str]:
    return [p for p in PLANS if offer_id(p)]


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
        "error": "Не получилось создать оплату. Попробуй ещё раз чуть позже 🙏",
        "unavailable": "Оплата пока недоступна. Скоро включим 💚",
        "paid": (
            "Оплата прошла 💚 Подписка на Спейс активна до {end}. "
            "Продлевается автоматически, отменить можно в любой момент."
        ),
        "renewed": "Подписка продлена до {end} 💚",
        "failed": "Оплата не прошла. Если хочешь, попробуй ещё раз 👇",
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
        "error": "Couldn't create the payment. Please try again a bit later 🙏",
        "unavailable": "Payments aren't available yet. Coming soon 💚",
        "paid": (
            "Payment received 💚 Your Space subscription is active until {end}. "
            "It renews automatically; you can cancel anytime."
        ),
        "renewed": "Subscription renewed until {end} 💚",
        "failed": "The payment didn't go through. You can try again 👇",
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
