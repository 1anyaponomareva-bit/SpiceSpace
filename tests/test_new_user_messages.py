"""A new user gets no morning/evening before onboarding, and no double greeting."""

import unittest
from datetime import datetime, timedelta

import onboarding_flow as ob


class JustGreetedTests(unittest.TestCase):
    def test_fresh_greeting_is_not_repeated(self):
        states: dict = {}
        ob.start_new_onboarding(states, 1, "ru")
        self.assertTrue(ob.just_greeted(states[1]))

    def test_old_or_advanced_state_gets_greeting(self):
        self.assertFalse(ob.just_greeted(None))
        old = {"step": ob.OB_NAME, "last_activity_at": datetime.now() - timedelta(minutes=10)}
        self.assertFalse(ob.just_greeted(old))
        later = {"step": ob.OB_VISION_DIALOG, "last_activity_at": datetime.now()}
        self.assertFalse(ob.just_greeted(later))


class DailyCheckGateTests(unittest.TestCase):
    def test_fresh_profile_is_not_onboarded(self):
        # What /start + name leaves in the DB: no goal, no weekly goal yet.
        self.assertFalse(ob.profile_onboarding_complete({"name": "Аня", "evening_time": "21:00"}))

    def test_daily_check_skips_incomplete_profiles(self):
        import inspect
        import main
        src = inspect.getsource(main)
        i = src.index("async def daily_check_job")
        self.assertIn("profile_onboarding_complete(profile)", src[i:i + 3000])


if __name__ == "__main__":
    unittest.main()


class ResumeOnStartTests(unittest.TestCase):
    def test_mid_onboarding_start_repeats_last_question(self):
        st = {
            "step": ob.OB_WEEKLY_TACTICS,
            "name": "Аня",
            "vision_turns": [{"role": "assistant", "content": "Что хочешь поменять?"}],
            "weekly_turns": [{"role": "assistant", "content": "Что успеешь на этой неделе?"}],
        }
        msg = ob.resume_onboarding_message(st, "ru")
        self.assertIn("Продолжим", msg)
        self.assertTrue(msg.endswith("Что успеешь на этой неделе?"))

    def test_no_resume_at_name_step_or_without_state(self):
        self.assertIsNone(ob.resume_onboarding_message(None, "ru"))
        self.assertIsNone(ob.resume_onboarding_message({"step": ob.OB_NAME}, "ru"))

    def test_flow_saved_after_every_update(self):
        import main
        import inspect
        self.assertIn("TypeHandler(Update, _persist_flow_after_update), group=99",
                      inspect.getsource(main))


class PersistRestoreRoundTripTests(unittest.TestCase):
    def test_in_place_step_change_survives_restart(self):
        import asyncio
        from types import SimpleNamespace
        from unittest import mock
        import main

        saved = {}
        cid = 777001
        with mock.patch.object(main.db_store, "save_flow_blob", side_effect=lambda u, b: saved.update({u: b})), \
             mock.patch.object(main.db_store, "delete_flow_blob"), \
             mock.patch.object(main.db_store, "load_flow_blob", side_effect=lambda u: saved.get(u)):
            main.onboarding[cid] = {"step": ob.OB_NAME}
            st = main.onboarding[cid]
            st["step"] = ob.OB_WEEKLY_TACTICS  # in-place, the dict hook doesn't see it
            st["name"] = "Аня"
            st["weekly_turns"] = [{"role": "assistant", "content": "Что успеешь?"}]
            update = SimpleNamespace(effective_chat=SimpleNamespace(id=cid))
            asyncio.run(main._persist_flow_after_update(update, None))

            main._flow_save_muted = True
            dict.pop(main.onboarding, cid)  # simulate restart: memory empty
            main._flow_save_muted = False
            main._restore_cid_flow(cid)
            restored = main.onboarding.get(cid)
            self.assertEqual(restored["step"], ob.OB_WEEKLY_TACTICS)
            self.assertIn("Что успеешь?", ob.resume_onboarding_message(restored, "ru"))
            dict.pop(main.onboarding, cid, None)
