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
