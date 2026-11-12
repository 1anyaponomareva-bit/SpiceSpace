"""lava.top checkout: invoice body, webhook parsing/auth, access dates, payment store."""

from __future__ import annotations

import asyncio
import base64
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import lava_pay  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402

ENV = {
    "LAVA_API_KEY": "k",
    "LAVA_WEBHOOK_KEY": "hook-secret",
    "LAVA_OFFER_1M": "11111111-1111-1111-1111-111111111111",
    "LAVA_OFFER_3M": "33333333-3333-3333-3333-333333333333",
}

FIRST = {
    "eventType": "payment.success",
    "product": {"id": "p", "title": "Спейс"},
    "buyer": {"email": "Anna@Mail.ru"},
    "contractId": "c5a0cacc-3453-44b0-9532-aa492f1ba191",
    "amount": 490,
    "currency": "RUB",
    "timestamp": "2026-11-12T08:44:32.42176Z",
    "status": "subscription-active",
    "errorMessage": "",
}
RENEW = {
    "eventType": "subscription.recurring.payment.success",
    "buyer": {"email": "anna@mail.ru"},
    "contractId": "d41db415-ad71-4f2a-8d8c-27eefee91e66",
    "parentContractId": "c5a0cacc-3453-44b0-9532-aa492f1ba191",
    "amount": 490,
    "currency": "RUB",
    "timestamp": "2026-12-12T08:44:49Z",
    "status": "subscription-active",
}
CANCEL = {
    "eventType": "subscription.cancelled",
    "contractId": "d41db415-ad71-4f2a-8d8c-27eefee91e66",
    "buyer": {"email": "anna@mail.ru"},
    "cancelledAt": "2026-12-20T08:44:49Z",
    "willExpireAt": "2027-01-12T08:44:49Z",
}


class LavaPayTests(unittest.TestCase):
    def setUp(self) -> None:
        lava_pay._found = {}

    def tearDown(self) -> None:
        lava_pay._found = {}

    def test_enabled_needs_key(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(lava_pay.enabled())
        with mock.patch.dict("os.environ", {"LAVA_API_KEY": "k"}, clear=True):
            self.assertTrue(lava_pay.enabled())
            self.assertEqual(lava_pay.available_plans(), [])
        with mock.patch.dict("os.environ", {"LAVA_API_KEY": "k", "LAVA_OFFER_3M": "x"}, clear=True):
            self.assertEqual(lava_pay.available_plans(), ["3m"])

    def test_pick_offers_by_period(self) -> None:
        items = [
            {"type": "PRODUCT", "data": {"title": "Спейс 1 мес", "offers": [{
                "id": "aaa", "prices": [
                    {"amount": 590, "currency": "RUB", "periodicity": "MONTHLY"},
                    {"amount": 7, "currency": "USD", "periodicity": "MONTHLY"},
                ]}]}},
            {"type": "PRODUCT", "data": {"title": "Спейс 3 мес", "offers": [{
                "id": "bbb", "prices": [{"amount": 1490, "currency": "RUB", "periodicity": "PERIOD_90_DAYS"}]}]}},
            {"type": "PRODUCT", "data": {"title": "Старая", "offers": [{
                "id": "old", "prices": [{"amount": 990, "currency": "RUB", "periodicity": "MONTHLY"}]}]}},
        ]
        found = lava_pay.pick_offers(items)
        self.assertEqual(found["1m"]["id"], "aaa")  # 590 wins over 990
        self.assertTrue(found["1m"]["usd"])
        self.assertEqual(found["3m"]["id"], "bbb")
        self.assertFalse(found["3m"]["usd"])
        lava_pay._found = found
        with mock.patch.dict("os.environ", {"LAVA_API_KEY": "k"}, clear=True):
            self.assertEqual(lava_pay.available_plans(), ["1m", "3m"])
            self.assertEqual(lava_pay.offer_id("3m"), "bbb")
            self.assertEqual(lava_pay.plan_rub("3m"), 1490)
            self.assertFalse(lava_pay.plan_has_usd("3m"))

    def test_contract_confirms(self) -> None:
        paid = lava_pay.parse_event(FIRST)
        self.assertTrue(lava_pay.contract_confirms(paid, {"status": "COMPLETED"}))
        self.assertFalse(lava_pay.contract_confirms(paid, {"status": "NEW"}))
        self.assertFalse(lava_pay.contract_confirms(paid, None))
        self.assertTrue(lava_pay.contract_confirms(lava_pay.parse_event(CANCEL), {"status": "COMPLETED"}))

    def test_invoice_body(self) -> None:
        with mock.patch.dict("os.environ", ENV, clear=True):
            b = lava_pay.invoice_body(cid=42, plan="3m", method="intl", email="a@b.co", lang="ru", promo="FIRST30")
        self.assertEqual(b["offerId"], ENV["LAVA_OFFER_3M"])
        self.assertEqual(b["currency"], "USD")
        self.assertEqual(b["periodicity"], "PERIOD_90_DAYS")
        self.assertEqual(b["buyerLanguage"], "RU")
        self.assertEqual(b["promoCode"], "FIRST30")
        self.assertEqual(b["clientUtm"]["utm_content"], "42")
        with mock.patch.dict("os.environ", ENV, clear=True):
            b2 = lava_pay.invoice_body(cid=1, plan="1m", method="ru", email="a@b.co", lang="en")
        self.assertEqual((b2["currency"], b2["periodicity"]), ("RUB", "MONTHLY"))
        self.assertNotIn("promoCode", b2)

    def test_email_and_promo(self) -> None:
        self.assertTrue(lava_pay.valid_email("anna@mail.ru"))
        self.assertFalse(lava_pay.valid_email("anna@mail"))
        self.assertFalse(lava_pay.valid_email("привет"))
        self.assertEqual(lava_pay.normalize_promo(" first30 "), "FIRST30")
        self.assertEqual(lava_pay.normalize_promo("ab"), "")
        self.assertEqual(lava_pay.normalize_promo("скидка"), "")

    def test_format_amount(self) -> None:
        self.assertEqual(lava_pay.format_amount(590, "RUB"), "590 ₽")
        self.assertEqual(lava_pay.format_amount(5.5, "USD"), "$5.50")

    def test_parse_events(self) -> None:
        ev = lava_pay.parse_event(FIRST)
        self.assertEqual(ev["kind"], "paid")
        self.assertEqual(ev["email"], "anna@mail.ru")
        self.assertEqual(lava_pay.parse_event(RENEW)["kind"], "renewed")
        self.assertEqual(lava_pay.parse_event(RENEW)["parent_id"], FIRST["contractId"])
        c = lava_pay.parse_event(CANCEL)
        self.assertEqual(c["kind"], "cancelled")
        self.assertTrue(c["will_expire"].startswith("2027-01-12"))
        failed = dict(FIRST, eventType="payment.failed", status="subscription-failed")
        self.assertEqual(lava_pay.parse_event(failed)["kind"], "failed")
        odd = dict(FIRST, status="in-progress")
        self.assertEqual(lava_pay.parse_event(odd)["kind"], "other")
        refund = {"event_type": "refund.success", "data": {}}
        self.assertEqual(lava_pay.parse_event(refund)["kind"], "other")
        utm = dict(FIRST, clientUtm={"utm_content": "802927717"})
        self.assertEqual(lava_pay.parse_event(utm)["telegram_id"], "802927717")

    def test_paid_until(self) -> None:
        today = date(2026, 11, 12)
        self.assertEqual(lava_pay.paid_until("1m", FIRST["timestamp"], "", today), "2026-12-14")
        # renewal a month later: from the payment date, grace not stacked
        self.assertEqual(lava_pay.paid_until("1m", RENEW["timestamp"], "2026-12-14", today), "2027-01-13")
        # never shortens access
        self.assertEqual(lava_pay.paid_until("1m", FIRST["timestamp"], "2027-05-01", today), "2027-05-01")
        self.assertEqual(lava_pay.paid_until("3m", "", "", today), "2027-02-12")

    def test_webhook_auth(self) -> None:
        with mock.patch.dict("os.environ", ENV, clear=True):
            self.assertTrue(lava_pay.webhook_authorized({"X-Api-Key": "hook-secret"}))
            self.assertFalse(lava_pay.webhook_authorized({"X-Api-Key": "wrong"}))
            self.assertFalse(lava_pay.webhook_authorized({}))
            basic = base64.b64encode(b"lava:hook-secret").decode()
            self.assertTrue(lava_pay.webhook_authorized({"Authorization": f"Basic {basic}"}))
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(lava_pay.webhook_authorized({"X-Api-Key": ""}))


class LavaInvoiceHttpTests(unittest.TestCase):
    def _run(self, handler):
        import httpx

        real = httpx.AsyncClient

        def fake(*a, **k):
            k["transport"] = httpx.MockTransport(handler)
            return real(*a, **k)

        with mock.patch.dict("os.environ", ENV, clear=True), mock.patch.object(
            lava_pay.httpx, "AsyncClient", fake
        ):
            body = lava_pay.invoice_body(cid=1, plan="1m", method="ru", email="a@b.co", lang="ru")
            return asyncio.run(lava_pay.create_invoice(body))

    def test_ok(self) -> None:
        import httpx

        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["url"] = str(req.url)
            seen["key"] = req.headers.get("X-Api-Key")
            return httpx.Response(201, json={
                "id": "abc-123", "status": "new", "paymentUrl": "https://pay.lava.top/x",
                "amountTotal": {"currency": "RUB", "amount": 490},
            })

        inv = self._run(handler)
        self.assertEqual(seen["url"], "https://gate.lava.top/api/v3/invoice")
        self.assertEqual(seen["key"], "k")
        self.assertEqual((inv.contract_id, inv.payment_url, inv.amount), ("abc-123", "https://pay.lava.top/x", 490))

    def test_error(self) -> None:
        import httpx

        def handler(req):
            return httpx.Response(404, json={"error": "Product with offer id not found"})

        with self.assertRaises(lava_pay.LavaError) as ctx:
            self._run(handler)
        self.assertEqual(ctx.exception.status, 404)


class LavaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.p1 = mock.patch.object(db, "LAVA_PAYMENTS_PATH", Path(self.tmp.name) / "lava.json")
        self.p2 = mock.patch.object(db, "_use_supabase", False)
        self.p1.start()
        self.p2.start()

    def tearDown(self) -> None:
        self.p1.stop()
        self.p2.stop()
        self.tmp.cleanup()

    def test_save_and_find(self) -> None:
        db.lava_save_payment({"contract_id": FIRST["contractId"], "telegram_id": "77",
                              "email": "anna@mail.ru", "plan": "1m", "status": "new"})
        db.lava_save_payment({"contract_id": FIRST["contractId"], "status": "payment.success",
                              "paid_until": "2026-12-14"})
        row = db.lava_find_contract(FIRST["contractId"])
        self.assertEqual(row["telegram_id"], 77)
        self.assertEqual(row["plan"], "1m")
        self.assertEqual(row["paid_until"], "2026-12-14")
        self.assertEqual(db.lava_latest_for_user(77)["email"], "anna@mail.ru")
        self.assertEqual(db.lava_latest_for_email("ANNA@mail.ru")["telegram_id"], 77)
        self.assertIsNone(db.lava_find_contract("x'; drop"))


class _Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)


class _Store:
    class ProfileReadError(Exception):
        pass

    def __init__(self, profile: dict, rows: dict) -> None:
        self.profile = profile
        self.rows = rows
        self.saved: list[dict] = []

    def lava_find_contract(self, cid):
        return self.rows.get(cid)

    def lava_latest_for_email(self, email):
        return None

    def get_profile(self, cid):
        return dict(self.profile)

    def update_profile(self, cid, fields):
        self.profile.update(fields)
        return dict(self.profile)

    def save_subscriber(self, cid, on):
        pass

    def lava_save_payment(self, row):
        self.saved.append(row)


def _apply_ns(store: _Store) -> dict:
    from fastapi import HTTPException

    ns = {
        "db_store": store,
        "lava_pay": lava_pay,
        "user_profiles": {},
        "subscribers": set(),
        "date": date,
        "HTTPException": HTTPException,
        "ui_lang": lambda p: "ru",
        "_user_lang": lambda p: "ru",
        "_profile_local_date": lambda p: date(2026, 11, 12),
        "_profile_truthy": lambda v: v is True,
        "_webapp_keyboard": lambda cid, lang: None,
        "InlineKeyboardMarkup": lambda rows: rows,
        "InlineKeyboardButton": lambda *a, **k: (a, k),
        "log": __import__("logging").getLogger("t"),
    }
    return _main_functions({"_lava_apply_event", "_lava_end_text", "_lava_user_from_profiles"}, ns)


class LavaApplyTests(unittest.TestCase):
    def test_first_payment_then_renewal_then_cancel(self) -> None:
        store = _Store({"is_premium": False, "subscription_end": ""},
                       {FIRST["contractId"]: {"telegram_id": 77, "plan": "1m"}})
        ns = _apply_ns(store)
        bot = _Bot()
        ok = asyncio.run(ns["_lava_apply_event"](bot, lava_pay.parse_event(FIRST)))
        self.assertTrue(ok)
        self.assertTrue(store.profile["is_premium"])
        self.assertEqual(store.profile["subscription_end"], "2026-12-14")
        self.assertIn("14.12.2026", bot.sent[-1])
        self.assertIn(77, ns["subscribers"])
        # the same webhook delivered twice: no second message
        asyncio.run(ns["_lava_apply_event"](bot, lava_pay.parse_event(FIRST)))
        self.assertEqual(len(bot.sent), 1)
        # renewal found through the parent contract
        asyncio.run(ns["_lava_apply_event"](bot, lava_pay.parse_event(RENEW)))
        self.assertEqual(store.profile["subscription_end"], "2027-01-13")
        self.assertIn("продлена", bot.sent[-1])
        store.rows[RENEW["contractId"]] = {"telegram_id": 77, "plan": "1m"}
        asyncio.run(ns["_lava_apply_event"](bot, lava_pay.parse_event(CANCEL)))
        self.assertIn("12.01.2027", bot.sent[-1])
        self.assertEqual(store.profile["subscription_end"], "2027-01-13")  # access kept

    def test_unknown_user(self) -> None:
        store = _Store({}, {})
        ns = _apply_ns(store)
        bot = _Bot()
        self.assertFalse(asyncio.run(ns["_lava_apply_event"](bot, lava_pay.parse_event(FIRST))))
        self.assertEqual(bot.sent, [])


if __name__ == "__main__":
    unittest.main()


class LavaProfileTagTests(unittest.TestCase):
    def test_tag_roundtrip(self) -> None:
        tag = lava_pay.profile_tag("3m", "root-1", "a@b.co")
        self.assertEqual(lava_pay.parse_profile_tag(tag), {"plan": "3m", "root": "root-1", "email": "a@b.co"})
        self.assertIsNone(lava_pay.parse_profile_tag("subscription_4weeks"))

    def test_plan_from_amount(self) -> None:
        self.assertEqual(lava_pay.plan_from_amount(590, "RUB"), "1m")
        self.assertEqual(lava_pay.plan_from_amount(1490, "RUB"), "3m")

    def test_renewal_found_by_profile_tag_without_table(self) -> None:
        tag = lava_pay.profile_tag("3m", FIRST["contractId"], "anna@mail.ru")
        store = _Store({"is_premium": True, "subscription_end": "2026-12-14", "plan": tag}, {})
        ns = _apply_ns(store)
        ns["user_profiles"]["77"] = dict(store.profile)
        bot = _Bot()
        self.assertTrue(asyncio.run(ns["_lava_apply_event"](bot, lava_pay.parse_event(RENEW))))
        self.assertEqual(store.profile["subscription_end"], "2027-03-14")  # 3 months + grace
        self.assertTrue(store.profile["plan"].startswith("lava_3m|" + FIRST["contractId"]))
