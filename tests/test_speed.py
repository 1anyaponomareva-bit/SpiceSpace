"""Speed fixes (Anya, 21.11.2026): parallel chats, one chat in order, no catch-up after a
time zone change, profiles read in one request."""

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import update_order  # noqa: E402


def _upd(chat_id):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_user=None, effective_message=None)


class Processor(unittest.TestCase):
    def test_same_chat_in_order_other_chats_parallel(self):
        async def run():
            proc = update_order.ChatOrderedProcessor(max_parallel=8)
            events = []

            async def work(name, delay):
                events.append(("start", name))
                await asyncio.sleep(delay)
                events.append(("end", name))

            await asyncio.gather(
                proc.do_process_update(_upd(1), work("a1", 0.05)),
                proc.do_process_update(_upd(1), work("a2", 0.0)),
                proc.do_process_update(_upd(2), work("b1", 0.0)),
            )
            return events

        ev = asyncio.run(run())
        self.assertLess(ev.index(("end", "a1")), ev.index(("start", "a2")))  # one chat: in order
        self.assertLess(ev.index(("end", "b1")), ev.index(("end", "a1")))  # another chat: not waiting

    def test_limit(self):
        async def run():
            proc = update_order.ChatOrderedProcessor(max_parallel=2)
            live = []
            peak = []

            async def work():
                live.append(1)
                peak.append(len(live))
                await asyncio.sleep(0.02)
                live.pop()

            await asyncio.gather(*(proc.do_process_update(_upd(i), work()) for i in range(6)))
            return max(peak)

        self.assertEqual(asyncio.run(run()), 2)


class TimezoneChange(unittest.TestCase):
    def test_no_catch_up_after_zone_change(self):
        prof = {"timezone": "Europe/Moscow", "morning_time": "13:00", "evening_time": "23:00",
                "last_morning_sent_date": "2026-11-21"}
        now = datetime(2026, 11, 21, 23, 27, tzinfo=ZoneInfo("Asia/Saigon"))
        self.assertEqual(main._past_slots_today(prof, now), {"last_evening_sent_date": "2026-11-21"})

    def test_future_slot_stays(self):
        prof = {"morning_time": "09:00", "evening_time": "21:00"}
        now = datetime(2026, 11, 21, 15, 0, tzinfo=ZoneInfo("Asia/Saigon"))
        self.assertEqual(main._past_slots_today(prof, now), {"last_morning_sent_date": "2026-11-21"})

    def test_confirm_writes_slots(self):
        prof = {"timezone": "Europe/Moscow", "morning_time": "13:00", "evening_time": "23:00"}
        with mock.patch.object(main.db_store, "update_profile") as up, \
             mock.patch.object(main, "datetime") as dt:
            dt.now.return_value = datetime(2026, 11, 21, 23, 27, tzinfo=ZoneInfo("Asia/Saigon"))
            main._confirm_timezone(4321, prof, "Asia/Saigon")
        patch = up.call_args.args[1]
        self.assertEqual(patch["timezone"], "Asia/Saigon")
        self.assertEqual(patch["last_evening_sent_date"], "2026-11-21")
        main.user_profiles.pop("4321", None)


class BulkProfiles(unittest.TestCase):
    def test_one_request(self):
        calls = []

        def fake_request(method, path, **kw):
            calls.append(path)
            return [{"user_id": 1, "name": "A"}, {"user_id": 2, "name": "B"}]

        with mock.patch.object(main.db_store, "_use_supabase", True), \
             mock.patch.object(main.db_store, "_request", side_effect=fake_request), \
             mock.patch.object(main.db_store, "sync_profile_times", side_effect=lambda p: p), \
             mock.patch.object(main.db_store, "_row_to_profile", side_effect=lambda r: r):
            out = main.db_store.get_profiles([1, 2])
        self.assertEqual(len(calls), 1)
        self.assertIn("user_id=in.(1,2)", calls[0])
        self.assertEqual(set(out), {"1", "2"})

    def test_failure_raises(self):
        with mock.patch.object(main.db_store, "_use_supabase", True), \
             mock.patch.object(main.db_store, "_request", return_value=None):
            with self.assertRaises(main.db_store.ProfileReadError):
                main.db_store.get_profiles([1])


if __name__ == "__main__":
    unittest.main()
