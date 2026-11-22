"""Second Threads account (Anya, 22.11.2026): "en" (@anyapnm) on US Eastern pools, queued,
replaced, cancelled and posted apart from "ru"; stats per account."""

import unittest
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import meta_stats
import threads_publisher as tp
from tests.test_threads_publisher import FakeThreads, Store, items

tp.time.sleep = lambda s: None

DAY = date(2026, 12, 2)
EARLY = datetime(2026, 12, 2, 4, 0, tzinfo=timezone.utc)  # 7:00 MSK, 23:00 ET the day before
ET = ZoneInfo("America/New_York")


class EnSchedule(unittest.TestCase):
    def test_pools_in_us_eastern(self):
        q = tp.schedule(DAY, items(15), now=EARLY, account="en")
        self.assertTrue(all(x["account"] == "en" for x in q))
        first = {}
        for x in q:
            first.setdefault(x["pool"], datetime.fromisoformat(x["at"]).astimezone(ET))
        self.assertEqual([(first[p].hour, first[p].minute) for p in tp.POOL_ORDER], [(8, 0), (12, 0), (19, 0)])
        last_evening = max(datetime.fromisoformat(x["at"]).astimezone(ET) for x in q if x["pool"] == "evening")
        self.assertLessEqual(last_evening.hour, 21)

    def test_ru_unchanged(self):
        q = tp.schedule(DAY, items(1), now=EARLY)
        self.assertEqual(q[0]["account"], "ru")
        self.assertEqual(datetime.fromisoformat(q[0]["at"]).astimezone(tp.MSK).hour, 9)

    def test_norm_account(self):
        self.assertEqual(tp.norm_account("EN"), "en")
        self.assertEqual(tp.norm_account(None), "ru")
        self.assertEqual(tp.norm_account("de"), "ru")
        self.assertEqual(tp.account_of({}), "ru")  # items queued before 22.11


class Separate(unittest.TestCase):
    def test_put_and_cancel_do_not_touch_the_other(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(3), now=EARLY)
        tp.put_day(s.get_, s.set_, DAY, items(2), now=EARLY, account="en")
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY, account="en")  # resend en only
        q = tp.load(s.get_)
        self.assertEqual(sum(tp.account_of(x) == "ru" for x in q), 3)
        self.assertEqual(sum(tp.account_of(x) == "en" for x in q), 1)
        self.assertEqual(tp.cancel_day(s.get_, s.set_, DAY, "en"), 1)
        q = tp.load(s.get_)
        self.assertTrue(all(x["status"] == "queued" for x in q if tp.account_of(x) == "ru"))

    def test_old_items_without_account_stay_ru(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(2), now=EARLY)
        data = s[tp.QUEUE_KEY]
        for x in data["items"]:
            x.pop("account")
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY, account="en")
        self.assertEqual(tp.cancel_day(s.get_, s.set_, DAY), 2)

    def test_run_posts_only_its_account(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY)
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY, account="en")
        fake = FakeThreads()
        at = datetime(2026, 12, 2, 13, 30, tzinfo=timezone.utc)  # both mornings due
        tp.run_once(s.get_, s.set_, "TOK", now=at, post=fake.post, get=fake.get, account="en")
        q = {tp.account_of(x): x["status"] for x in tp.load(s.get_)}
        self.assertEqual(q, {"ru": "queued", "en": "posted"})
        self.assertEqual(len(fake.posts), 1)

    def test_summary_has_account(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY, account="en")
        self.assertEqual(tp.summary(tp.load(s.get_))[0]["account"], "en")


class Stats(unittest.TestCase):
    def test_en_numbers_apart(self):
        def fake_token(kind, env, *a, **k):
            return ("TOK_" + kind, {"source": "railway"})

        with mock.patch.object(meta_stats, "current_token", side_effect=fake_token), \
             mock.patch.object(meta_stats, "collect_threads", side_effect=lambda tok, get: {"who": tok}), \
             mock.patch.object(meta_stats, "search_threads_niche", return_value={}), \
             mock.patch.object(meta_stats, "_instagram_block", return_value={}):
            out = meta_stats.collect_all(lambda k: None, lambda k, v: True)
        self.assertEqual(out["threads"]["who"], "TOK_threads")
        self.assertEqual(out["threads_en"]["who"], "TOK_threads_en")


if __name__ == "__main__":
    unittest.main()
