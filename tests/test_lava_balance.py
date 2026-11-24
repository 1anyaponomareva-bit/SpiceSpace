"""Admin panel: money on lava.top (Anya, 24.11.2026)."""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import lava_balance as lb
import lava_pay
import main

TZ = ZoneInfo("Asia/Saigon")
NOW = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)


def inv(amount, fee, cur="RUB", at="2026-11-24T13:53:23Z", status="COMPLETED"):
    return {"status": status, "datetime": at, "receipt": {"amount": amount, "currency": cur, "fee": fee},
            "buyer": {"email": "x@example.com"}}


ALL = [inv(1490, 119.2), inv(590, 0, status="FAILED"), inv(7.08, 0.57, "USD", "2026-11-15T18:34:02Z")]


class Summary(unittest.TestCase):
    def test_net_by_currency(self):
        out = lb.summarize(ALL, None, NOW, TZ)["currencies"]
        self.assertEqual([c["currency"] for c in out], ["RUB", "USD"])
        self.assertEqual((out[0]["balance"], out[0]["count"], out[0]["net_today"]), (1370.8, 1, 1370.8))
        self.assertEqual((out[1]["balance"], out[1]["net_today"], out[1]["net_month"]), (6.51, 0, 6.51))
        self.assertNotIn("example", str(out))  # no buyers

    def test_after_withdrawal(self):
        stored = {"RUB": {"value": 100, "at": "2026-11-24T14:00:00+00:00"}}
        out = lb.summarize(ALL + [inv(590, 47.2, at="2026-11-24T15:00:00Z")], stored, NOW, TZ)["currencies"]
        self.assertEqual(out[0]["balance"], 642.8)
        self.assertEqual(out[1]["balance"], 6.51)

    def test_empty(self):
        self.assertEqual(lb.summarize([], None, NOW, TZ), {"currencies": []})


class Api(unittest.TestCase):
    def test_not_for_others(self):
        with mock.patch.object(main, "_is_admin_request", return_value=False):
            for f in (main.admin_lava_balance, main.admin_lava_balance_set):
                with self.assertRaises(main.HTTPException) as e:
                    asyncio.run(f(mock.Mock()))
                self.assertEqual(e.exception.status_code, 403)

    def test_pages(self):
        seen = []

        class R:
            status_code = 200

            def __init__(self, p):
                self.p = p

            def json(self):
                return {"items": [{"n": self.p}], "pages": 2}

        class C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, params, headers):
                seen.append(dict(params)["page"])
                return R(dict(params)["page"])

        with mock.patch.object(lava_pay, "api_key", return_value="k"), \
                mock.patch.object(lava_pay.httpx, "AsyncClient", lambda **k: C()):
            self.assertEqual(len(asyncio.run(lava_pay.list_invoices())), 2)
        self.assertEqual(seen, [1, 2])


if __name__ == "__main__":
    unittest.main()
