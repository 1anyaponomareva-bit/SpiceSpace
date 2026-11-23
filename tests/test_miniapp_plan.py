"""Mini App: the plan tapped on the card goes straight to checkout (Anya, 23.11.2026)."""

import asyncio
import unittest
from unittest import mock

import main


class Subscribe(unittest.TestCase):
    def call(self, plan, available=("1m", "3m")):
        chosen, started = [], []

        async def choose(bot, cid, prof, p, method=""):
            chosen.append(p)

        async def start(bot, cid, prof):
            started.append(cid)

        async def load():
            return None

        body = main.SubscribePayload(plan=plan) if plan else None
        with mock.patch.object(main, "_auth_telegram_id", lambda r, t: "8850"), \
                mock.patch.object(main.lava_pay, "enabled", return_value=True), \
                mock.patch.object(main.lava_pay, "available_plans", return_value=list(available)), \
                mock.patch.object(main.lava_pay, "load_offers", load), \
                mock.patch.object(main, "telegram_app", mock.Mock()), \
                mock.patch.object(main, "_resolve_user_profile", lambda t: {}), \
                mock.patch.object(main, "_pay_mark", lambda *a: None), \
                mock.patch.object(main, "_lava_choose", choose), \
                mock.patch.object(main, "_lava_send_start", start):
            out = asyncio.run(main.subscribe_endpoint(mock.Mock(), None, body))
        self.assertTrue(out["ok"])
        return chosen, started

    def test_plan_goes_to_checkout(self):
        self.assertEqual(self.call("1m"), (["1m"], []))
        self.assertEqual(self.call("3m"), (["3m"], []))

    def test_old_page_or_missing_offer_asks_in_chat(self):
        self.assertEqual(self.call(None), ([], [8850]))
        self.assertEqual(self.call("4weeks"), ([], [8850]))
        self.assertEqual(self.call("1m", available=("3m",)), ([], [8850]))


if __name__ == "__main__":
    unittest.main()
