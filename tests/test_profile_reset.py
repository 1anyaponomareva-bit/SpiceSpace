"""Full reset deletes every per-user table before the profile and checks the row is gone."""

import unittest
from unittest import mock

import db


class ProfileResetTests(unittest.TestCase):
    def _run(self, profile_left: list):
        calls: list[tuple[str, str]] = []

        def fake_request(method, path, **kw):
            calls.append((method, path))
            if method == "GET" and path.startswith("user_profiles"):
                return profile_left
            return []

        with mock.patch.object(db, "_use_supabase", True), \
             mock.patch.object(db, "_request", side_effect=fake_request), \
             mock.patch.object(db, "_load_json", return_value={}), \
             mock.patch.object(db, "_save_json"), \
             mock.patch.object(db, "_read_flow_file", return_value={}):
            ok = db.delete_profile(42)
        return ok, calls

    def test_children_first_profile_last_then_verified(self):
        ok, calls = self._run(profile_left=[])
        self.assertTrue(ok)
        deletes = [p.split("?")[0] for m, p in calls if m == "DELETE"]
        self.assertEqual(deletes[-1], "user_profiles")
        for table in ("weekly_summaries", "daily_summaries", "user_facts",
                      "user_personality", "conversation_history", "tasks"):
            self.assertIn(table, deletes[:-1])
        self.assertNotIn("claude_usage", deletes)  # cost history is kept
        self.assertEqual(calls[-1][0], "GET")  # read back after deleting

    def test_reports_failure_when_profile_survives(self):
        ok, _ = self._run(profile_left=[{"user_id": 42}])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
