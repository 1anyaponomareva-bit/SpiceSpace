"""Crisis detection, vetted replies, pause question and the 18+ step."""

import asyncio
import unittest
from unittest import mock

import onboarding_flow as ob
import safety
from prompts import build_chat_system

CRISIS = [
    "Я не хочу жить",
    "иногда думаю покончить с собой",
    "Лучше бы меня не было",
    "хочу умереть",
    "я режу себя",
    "жить не хочется уже",
    "не вижу смысла жить дальше",
    "I want to die",
    "thinking about suicide",
    "I hope I never wake up",
]
ORDINARY = [
    "убила весь день на ерунду",
    "умираю как хочу спать",
    "сдохнуть со смеху",
    "порезала себе палец ножом",
    "хочу жить у моря",
    "хочу исчезнуть из соцсетей на неделю",
    "I did not wake up on time",
    "I hurt myself at the gym",
    "Хочу запустить бота и получить первые оплаты",
]


class DetectTests(unittest.TestCase):
    def test_crisis_phrases_detected(self):
        self.assertEqual([t for t in CRISIS if not safety.detect_crisis(t)], [])

    def test_ordinary_phrases_not_detected(self):
        self.assertEqual([t for t in ORDINARY if safety.detect_crisis(t)], [])

    def test_yo_and_case(self):
        self.assertTrue(safety.detect_crisis("НЕ ХОЧУ   ЖИТЬ"))


class ReplyTests(unittest.TestCase):
    def test_reply_has_help_and_no_goals_talk(self):
        for lang in ("ru", "en"):
            for repeat in (False, True):
                text = safety.crisis_reply(lang, repeat=repeat)
                self.assertIn("112", text)
                self.assertIn("findahelpline.com", text)
                self.assertIn("988", text)
                self.assertIn("?", text)  # asks whether she is safe

    def test_repeat_window(self):
        safety._last_crisis_at.pop(1, None)
        self.assertFalse(safety.note_crisis(1, now=1000.0))
        self.assertTrue(safety.note_crisis(1, now=2000.0))
        self.assertFalse(safety.note_crisis(1, now=2000.0 + safety.REPEAT_WINDOW_SECONDS + 1))

    def test_pause_question_mentions_subscription_only_for_premium(self):
        self.assertIn("Подписка", safety.pause_question("ru", premium=True))
        self.assertNotIn("Подписка", safety.pause_question("ru", premium=False))
        data = [b.callback_data for row in safety.pause_keyboard("ru").inline_keyboard for b in row]
        self.assertEqual(data, ["safety:pause", "safety:keep"])


class PromptTests(unittest.TestCase):
    def test_chat_system_has_safety_rule(self):
        for lang, marker in (("ru", "БЕЗОПАСНОСТЬ"), ("en", "SAFETY")):
            system = build_chat_system({"language_code": lang, "name": "Аня"}, None)
            self.assertIn(marker, system)
            self.assertIn("findahelpline.com", system)

    def test_onboarding_dialogs_have_safety_rule(self):
        for lang in ("ru", "en"):
            self.assertIn("SAFETY", ob._system_with_lang("X", lang))

    def test_core_prompt_no_longer_claims_not_a_bot(self):
        from prompts import SPICESPACE_CORE_SYSTEM, SPICESPACE_CORE_SYSTEM_RU
        self.assertNotIn("Не бот", SPICESPACE_CORE_SYSTEM_RU)
        self.assertNotIn("Not a bot", SPICESPACE_CORE_SYSTEM)


class AgeStepTests(unittest.TestCase):
    def test_new_onboarding_waits_for_age(self):
        flows: dict = {}
        ob.start_new_onboarding(flows, 7, "ru")
        self.assertTrue(ob.age_pending(flows[7]))
        self.assertFalse(ob.age_pending({"step": ob.OB_NAME}))  # older states keep going
        self.assertIn("18", ob.get_greeting_new("ru"))
        self.assertIn("не психолог", ob.get_greeting_new("ru"))
        data = [b.callback_data for row in ob.age_keyboard("ru").inline_keyboard for b in row]
        self.assertEqual(data, ["ob_age:yes", "ob_age:no"])

    def test_text_before_age_button_reasks(self):
        flows: dict = {}
        ob.start_new_onboarding(flows, 8, "ru")
        sent = []

        class Msg:
            async def reply_text(self, text, reply_markup=None):
                sent.append((text, reply_markup))

        update = mock.Mock()
        update.effective_chat.id = 8
        update.effective_user.language_code = "ru"
        update.message = Msg()
        context = mock.Mock()
        context.bot_data = {}
        asyncio.run(ob.handle_onboarding_turn(update, context, "Аня", flows, {}, {}, set()))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], ob.age_reply_text("press", "ru"))
        self.assertIsNotNone(sent[0][1])
        self.assertNotIn("name", flows[8])


if __name__ == "__main__":
    unittest.main()


class CrisisHandlerTests(unittest.TestCase):
    def _run(self, prof, cid):
        import main
        sent = []

        class Msg:
            async def reply_text(self, text, reply_markup=None):
                sent.append((text, reply_markup))

        update = mock.Mock()
        update.message = Msg()
        update.effective_user.language_code = "ru"
        with mock.patch.object(main, "_append_history_turn"), \
             mock.patch.object(main.asyncio, "sleep", new=mock.AsyncMock()):
            asyncio.run(main._handle_crisis_message(update, cid, "не хочу жить", prof))
        return sent

    def test_first_time_reply_then_pause_question_then_short(self):
        safety._last_crisis_at.pop(501, None)
        prof = {"language_code": "ru", "main_goal": "x", "weekly_goal": "y", "name": "Аня",
                "onboarding_complete": True, "daily_enabled": True}
        with mock.patch("onboarding_flow.profile_onboarding_complete", return_value=True):
            first = self._run(prof, 501)
            second = self._run(prof, 501)
        self.assertEqual(first[0][0], safety.CRISIS_REPLY_RU)
        self.assertEqual(len(first), 2)
        self.assertIsNotNone(first[1][1])
        self.assertEqual(second, [(safety.CRISIS_REPLY_SHORT_RU, None)])

    def test_mid_onboarding_no_pause_question(self):
        safety._last_crisis_at.pop(502, None)
        sent = self._run({}, 502)
        self.assertEqual(len(sent), 1)
