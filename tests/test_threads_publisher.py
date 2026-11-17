"""Auto-publishing of the threads Anya picked: pools, timing, reply chain."""

import unittest
from datetime import date, datetime, timedelta, timezone

import threads_publisher as tp

tp.time.sleep = lambda s: None  # no real waiting in tests


class Store(dict):
    def get_(self, k):
        return self.get(k)

    def set_(self, k, v):
        self[k] = v
        return True


class FakeThreads:
    def __init__(self, replies=0):
        self.posts = []
        self.replies = replies
        self.n = 0

    def post(self, url, params):
        assert params.get("access_token") == "TOK"
        if url.endswith("/me/threads"):
            self.n += 1
            self.posts.append(dict(params))
            return {"id": f"c{self.n}"}
        if url.endswith("/me/threads_publish"):
            return {"id": "p" + params["creation_id"][1:]}
        raise AssertionError(url)

    def get(self, url, params):
        if "status" in str(params.get("fields")):
            return {"status": "FINISHED"}
        return {"data": [{"name": "replies", "values": [{"value": self.replies}]}]}


def items(n, **extra):
    return [{"tid": f"t{i:02d}", "text": f"тред {i}", **extra} for i in range(1, n + 1)]


DAY = date(2026, 11, 18)
EARLY = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)  # 6:00 MSK


class ScheduleTests(unittest.TestCase):
    def test_thirty_picks_fill_three_pools_msk(self):
        out = tp.schedule(DAY, items(30), now=EARLY)
        pools = [x["pool"] for x in out]
        self.assertEqual(pools.count("morning"), 10)
        self.assertEqual(pools.count("day"), 10)
        self.assertEqual(pools.count("evening"), 10)
        first = {p: min(x["at"] for x in out if x["pool"] == p) for p in tp.POOL_ORDER}
        self.assertEqual(first["morning"], "2026-11-18T06:00:00+00:00")  # 9:00 MSK
        self.assertEqual(first["day"], "2026-11-18T10:00:00+00:00")      # 13:00 MSK
        self.assertEqual(first["evening"], "2026-11-18T16:00:00+00:00")  # 19:00 MSK
        last_morning = max(x["at"] for x in out if x["pool"] == "morning")
        self.assertEqual(last_morning, "2026-11-18T07:48:00+00:00")  # 10:48 MSK, 12 min apart

    def test_explicit_pool_is_kept(self):
        out = tp.schedule(DAY, [{"tid": "a", "text": "x", "pool": "evening"}] + items(2), now=EARLY)
        self.assertEqual({x["tid"]: x["pool"] for x in out}, {"a": "evening", "t01": "morning", "t02": "morning"})

    def test_late_picks_start_now(self):
        late = datetime(2026, 11, 18, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK, morning already started
        out = tp.schedule(DAY, items(2), now=late)
        self.assertEqual(out[0]["at"], "2026-11-18T07:02:00+00:00")


class RunTests(unittest.TestCase):
    def test_publishes_one_due_thread_per_tick(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(3), now=EARLY)
        fake = FakeThreads()
        at = datetime(2026, 11, 18, 6, 30, tzinfo=timezone.utc)  # 3 morning threads due
        tp.run_once(s.get_, s.set_, "TOK", now=at, post=fake.post, get=fake.get)
        q = tp.load(s.get_)
        self.assertEqual([x["status"] for x in q], ["posted", "queued", "queued"])
        self.assertEqual(fake.posts[0]["text"], "тред 1")
        self.assertNotIn("reply_to_id", fake.posts[0])

    def test_nothing_before_time(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY)
        fake = FakeThreads()
        tp.run_once(s.get_, s.set_, "TOK", now=EARLY, post=fake.post, get=fake.get)
        self.assertEqual(fake.posts, [])

    def test_chain_after_enough_comments(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1, reply="а у меня…", reply2="вот ссылка"), now=EARLY)
        fake = FakeThreads(replies=3)
        t0 = datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)
        tp.run_once(s.get_, s.set_, "TOK", now=t0, post=fake.post, get=fake.get)
        tp.run_once(s.get_, s.set_, "TOK", now=t0 + timedelta(minutes=30), post=fake.post, get=fake.get)
        self.assertEqual(len(fake.posts), 1)  # 3 comments, 30 min: wait
        fake.replies = 16
        tp.run_once(s.get_, s.set_, "TOK", now=t0 + timedelta(minutes=45), post=fake.post, get=fake.get)
        self.assertEqual(len(fake.posts), 3)
        thread_id = tp.load(s.get_)[0]["post_id"]
        self.assertEqual(fake.posts[1]["reply_to_id"], thread_id)
        self.assertEqual(fake.posts[2]["reply_to_id"], tp.load(s.get_)[0]["reply_id"])
        self.assertEqual(fake.posts[2]["text"], "вот ссылка")

    def test_chain_after_two_hours_anyway(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1, reply="r", reply2="r2"), now=EARLY)
        fake = FakeThreads(replies=0)
        t0 = datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)
        tp.run_once(s.get_, s.set_, "TOK", now=t0, post=fake.post, get=fake.get)
        tp.run_once(s.get_, s.set_, "TOK", now=t0 + timedelta(hours=2, minutes=1), post=fake.post, get=fake.get)
        self.assertEqual(tp.load(s.get_)[0]["chain"], "posted")

    def test_resend_does_not_post_twice(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(2), now=EARLY)
        fake = FakeThreads()
        tp.run_once(s.get_, s.set_, "TOK", now=datetime(2026, 11, 18, 6, 1, tzinfo=timezone.utc),
                    post=fake.post, get=fake.get)
        tp.put_day(s.get_, s.set_, DAY, items(2), now=EARLY)
        q = tp.load(s.get_)
        self.assertEqual(sorted(x["tid"] for x in q), ["t01", "t02"])
        self.assertEqual([x["status"] for x in q if x["tid"] == "t01"], ["posted"])

    def test_failure_retries_then_reports(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY)

        def boom(url, params):
            raise RuntimeError("HTTP 400: nope")

        t = datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)
        notes = []
        for i in range(3):
            notes += tp.run_once(s.get_, s.set_, "TOK", now=t + timedelta(minutes=6 * i), post=boom)
        self.assertEqual(tp.load(s.get_)[0]["status"], "error")
        self.assertEqual(len(notes), 1)

    def test_summary_has_no_token(self):
        s = Store()
        tp.put_day(s.get_, s.set_, DAY, items(1), now=EARLY)
        self.assertNotIn("TOK", repr(tp.summary(tp.load(s.get_))))


if __name__ == "__main__":
    unittest.main()
