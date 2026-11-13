"""Owner dashboard: start labels, daily numbers by label, Threads/Instagram collection."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import meta_stats  # noqa: E402
import spice_stats  # noqa: E402

D = date(2026, 11, 20)


def ts(day: date, hour: int = 5) -> str:
    # 05:00 UTC = 12:00 in UTC+7, same local day
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).isoformat()


class Labels(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(spice_stats.clean_label("th_2011_a"), "th_2011_a")
        self.assertEqual(spice_stats.clean_label("TT-Reel 1!"), "tt_reel1")
        self.assertEqual(spice_stats.clean_label("change_weekly"), "")
        self.assertEqual(spice_stats.clean_label(""), "")
        self.assertEqual(len(spice_stats.clean_label("x" * 99)), 40)

    def test_profile_label(self):
        self.assertEqual(spice_stats.profile_label({"cycle_flags": {"src_tiktok": True}}), "tiktok")
        self.assertEqual(spice_stats.profile_label({"src_rl": True}), "rl")
        self.assertEqual(spice_stats.profile_label({}), "none")

    def test_local_day_uses_utc_plus_7(self):
        # 20:00 UTC on the 19th is already the 20th in UTC+7
        self.assertEqual(spice_stats.local_day("2026-11-19T20:00:00+00:00"), D)


class DailyNumbers(unittest.TestCase):
    def setUp(self):
        self.profiles = {
            "1": {"created_at": ts(D), "cycle_flags": {"src_th_2011_a": True}, "name": "A", "main_goal": "g"},
            "2": {"created_at": ts(D), "cycle_flags": {"src_th_2011_a": True}},
            "3": {"created_at": ts(D - timedelta(days=1)), "cycle_flags": {"src_tt": True},
                  "name": "C", "main_goal": "g"},
            "4": {"created_at": ts(D - timedelta(days=3)), "name": "D", "main_goal": "g",
                  "plan": "lava_3m|c4|e", "subscription_end": "2027-02-20"},
        }
        self.msgs = {
            "3": {D - timedelta(days=1), D},
            "4": {D - timedelta(days=3), D},
        }
        self.payments = [
            {"contract_id": "c4", "telegram_id": 4, "plan": "3m", "amount": 1490, "currency": "RUB",
             "status": "payment.success", "paid_until": "2027-02-20", "updated_at": ts(D)},
            {"contract_id": "c9", "telegram_id": 3, "plan": "1m", "amount": 590, "currency": "RUB",
             "status": "new", "updated_at": ts(D)},
            {"contract_id": "r1", "parent_contract_id": "c0", "telegram_id": 1, "plan": "1m",
             "amount": 590, "currency": "RUB", "status": "subscription.recurring.payment.success",
             "paid_until": "2026-12-20", "updated_at": ts(D)},
        ]

    def test_day_by_label(self):
        s = spice_stats.build_spice_stats(self.profiles, self.msgs, self.payments, D)
        th = s["day_by_label"]["th_2011_a"]
        self.assertEqual(th["starts"], 2)
        self.assertEqual(th["onboarded"], 1)
        self.assertEqual(th["renewals"], 1)
        self.assertEqual(s["day_by_label"]["tt"]["active_d2"], 1)
        none = s["day_by_label"]["none"]
        self.assertEqual(none["active_d4"], 1)
        self.assertEqual(none["paid_3m"], 1)
        self.assertEqual(none["revenue_rub"], 1490)
        # unpaid invoice ('new') is not counted
        self.assertEqual(s["day_totals"]["paid_1m"], 0)
        self.assertEqual(s["day_totals"]["revenue_rub"], 2080)
        self.assertTrue(s["payments_history"])

    def test_cohorts_and_unknown_days(self):
        s = spice_stats.build_spice_stats(self.profiles, self.msgs, self.payments, D)
        rows = {(r["start_day"], r["label"]): r for r in s["cohorts"]}
        today = rows[(D.isoformat(), "th_2011_a")]
        self.assertIsNone(today["active_d2"])
        self.assertEqual(rows[((D - timedelta(days=3)).isoformat(), "none")]["active_d4"], 1)
        self.assertEqual(s["paying_now_by_label"], {"none": {"1m": 0, "3m": 1}})

    def test_no_payment_history(self):
        s = spice_stats.build_spice_stats(self.profiles, self.msgs, None, D)
        self.assertFalse(s["payments_history"])
        self.assertEqual(s["day_totals"]["starts"], 2)


class FakeMeta:
    def __init__(self):
        self.calls = []
        now = datetime.now(timezone.utc)
        self.recent = now.strftime("%Y-%m-%dT%H:%M:%S+0000")
        self.old = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if "refresh_access_token" in url:
            return {"access_token": "NEW_" + params["access_token"], "expires_in": 5184000}
        if url.endswith("/me/threads"):
            return {"data": [{"id": "t1", "text": "hi", "timestamp": self.recent},
                             {"id": "t0", "text": "old", "timestamp": self.old}]}
        if url.endswith("/t1/insights"):
            return {"data": [{"name": "views", "values": [{"value": 120}]},
                             {"name": "likes", "values": [{"value": 7}]}]}
        if url.endswith("/me/threads_insights"):
            if params["metric"] == "followers_count":
                return {"data": [{"name": "followers_count", "total_value": {"value": 55}}]}
            return {"data": [{"name": "views", "values": [{"value": 10}, {"value": 20}]}]}
        if url.endswith("/me/accounts"):
            return {"data": [{"name": "Page"}, {"name": "Spice", "instagram_business_account": {"id": "1784"}}]}
        if url.endswith("/1784"):
            assert "user_id" not in params["fields"]
            return {"username": "anya", "followers_count": 301, "media_count": 4}
        if url.endswith("/1784/insights"):
            return {"data": [{"name": params["metric"], "total_value": {"value": 9}}]}
        if url.endswith("/1784/media"):
            return self.__call__(url.replace("/1784/media", "/me/media"), params)
        if url.endswith("/me"):
            return {"username": "anya", "followers_count": 300, "media_count": 4}
        if url.endswith("/me/insights"):
            return {"data": [{"name": params["metric"], "total_value": {"value": 9}}]}
        if url.endswith("/me/media"):
            return {"data": [
                {"id": "r1", "caption": "reel", "media_product_type": "REELS", "timestamp": self.recent,
                 "like_count": 3, "comments_count": 1},
                {"id": "p1", "caption": "photo", "media_product_type": "FEED", "timestamp": self.recent},
                {"id": "r0", "caption": "old", "media_product_type": "REELS", "timestamp": self.old},
            ]}
        if url.endswith("/r1/insights"):
            return {"data": [{"name": "views", "values": [{"value": 900}]},
                             {"name": "reach", "values": [{"value": 800}]}]}
        raise AssertionError(url)


class MetaCollection(unittest.TestCase):
    def test_collect_all_refreshes_and_hides_tokens(self):
        store: dict = {}
        fake = FakeMeta()
        env = {"THREADS_ACCESS_TOKEN": "TH_SECRET", "INSTAGRAM_ACCESS_TOKEN": "IG_SECRET"}
        with mock.patch.dict(os.environ, env):
            out = meta_stats.collect_all(store.get, lambda k, v: store.__setitem__(k, v) or True, fake)
        th = out["threads"]
        self.assertEqual([p["id"] for p in th["posts"]], ["t1"])
        self.assertEqual(th["posts"][0]["views"], 120)
        self.assertEqual(th["account"]["followers"], 55)
        self.assertEqual(th["account"]["views_last_24h"], 30)
        ig = out["instagram"]
        self.assertEqual([r["id"] for r in ig["reels"]], ["r1"])
        self.assertEqual(ig["reels"][0]["views"], 900)
        self.assertEqual(ig["account"]["followers"], 300)
        self.assertNotIn("SECRET", repr(out))
        self.assertEqual(store["meta_token_threads"]["token"], "NEW_TH_SECRET")
        grants = [p["grant_type"] for u, p in fake.calls if "refresh" in u]
        self.assertEqual(grants, ["th_refresh_token", "ig_refresh_token"])

    def test_refreshed_token_used_until_env_changes(self):
        store: dict = {}
        setter = lambda k, v: store.__setitem__(k, v) or True  # noqa: E731
        with mock.patch.dict(os.environ, {"THREADS_ACCESS_TOKEN": "A"}):
            meta_stats.current_token("threads", "THREADS_ACCESS_TOKEN", "u/refresh_access_token",
                                     "th_refresh_token", store.get, setter, FakeMeta())
            fake = FakeMeta()
            tok, info = meta_stats.current_token("threads", "THREADS_ACCESS_TOKEN", "u/refresh_access_token",
                                                 "th_refresh_token", store.get, setter, fake)
            self.assertEqual(tok, "NEW_A")
            self.assertEqual(fake.calls, [])  # refreshed less than a week ago
        with mock.patch.dict(os.environ, {"THREADS_ACCESS_TOKEN": "B"}):
            tok, info = meta_stats.current_token("threads", "THREADS_ACCESS_TOKEN", "u/refresh_access_token",
                                                 "th_refresh_token", store.get, setter, FakeMeta())
            self.assertEqual(tok, "NEW_B")

    def test_instagram_via_facebook_login(self):
        fake = FakeMeta()
        env = {"THREADS_ACCESS_TOKEN": "", "INSTAGRAM_ACCESS_TOKEN": "SUAT_SECRET",
               "INSTAGRAM_API": "facebook", "INSTAGRAM_BUSINESS_ACCOUNT_ID": ""}
        with mock.patch.dict(os.environ, env):
            out = meta_stats.collect_all(lambda k: None, lambda k, v: True, fake)
        ig = out["instagram"]
        self.assertEqual(ig["token"]["api"], "facebook")
        self.assertEqual(ig["token"]["account_id"], "1784")
        self.assertEqual(ig["account"]["followers"], 301)
        self.assertEqual([r["id"] for r in ig["reels"]], ["r1"])
        self.assertTrue(all("graph.facebook.com" in u for u, _ in fake.calls))
        self.assertFalse(any("refresh" in u for u, _ in fake.calls))
        self.assertNotIn("SECRET", repr(out))

    def test_missing_tokens(self):
        with mock.patch.dict(os.environ, {"THREADS_ACCESS_TOKEN": "", "INSTAGRAM_ACCESS_TOKEN": ""}):
            out = meta_stats.collect_all(lambda k: None, lambda k, v: True, FakeMeta())
        self.assertIn("error", out["threads"])
        self.assertIn("error", out["instagram"])


class Wiring(unittest.TestCase):
    def test_main_saves_label_and_serves_stats(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("spice_stats.SRC_PREFIX + label", src)
        self.assertIn('@app.get("/api/stats/daily")', src)
        self.assertIn("STATS_EXPORT_KEY", src)


if __name__ == "__main__":
    unittest.main()
