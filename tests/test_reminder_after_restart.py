"""After a restart the onboarding reminder must still see people who stalled before it."""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


class RestoreRecentFlows(unittest.TestCase):
    def tearDown(self):
        for cid in (7001, 7002, 7003):
            dict.pop(main.onboarding, cid, None)
            dict.pop(main.pending_morning, cid, None)

    def test_loads_stalled_onboarding_without_writing_back(self):
        blobs = {
            7001: {"onboarding": {"step": 6, "name": "A", "last_activity_at": "2026-11-20T22:32:49"}},
            7002: {"pending_morning": {"task": "x"}},
        }
        dict.__setitem__(main.onboarding, 7003, {"step": 3, "name": "live"})
        blobs[7003] = {"onboarding": {"step": 1}}
        with mock.patch.object(main.db_store, "load_flow_blobs_since", return_value=blobs) as load, \
             mock.patch.object(main.db_store, "save_flow_blob") as save:
            n = main._restore_recent_flows(datetime(2026, 11, 21, 7, 40))
        self.assertEqual(n, 1)
        self.assertEqual(main.onboarding[7001]["step"], 6)
        self.assertIn(7002, main.pending_morning)
        self.assertEqual(main.onboarding[7003]["name"], "live")  # memory wins over the DB
        save.assert_not_called()
        self.assertEqual(load.call_args.args[0], "2026-11-20T01:40:00")

    def test_db_error_is_quiet(self):
        with mock.patch.object(main.db_store, "load_flow_blobs_since", side_effect=RuntimeError("down")):
            self.assertEqual(main._restore_recent_flows(datetime(2026, 11, 21, 7, 40)), 0)


if __name__ == "__main__":
    unittest.main()
