"""Onboarding with fewer typed answers (Anya, 22.11.2026): buttons under the goal card,
morning and evening in one tap, a nudge for those who never gave a name."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402
from prompts import GOAL_DIALOG_SYSTEM  # noqa: E402


class CardButtons(unittest.TestCase):
    def test_goal_card(self):
        kb = ob.card_keyboard("К 12-й неделе ты гуляешь каждый день.\n\nПодходит такая цель?", "ru")
        self.assertEqual([b.callback_data for b in kb.inline_keyboard[0]], ["ob_card:yes", "ob_card:fix"])
        self.assertEqual(kb.inline_keyboard[0][0].text, ob.CARD_YES_RU)

    def test_confirm_and_weekly(self):
        self.assertIsNotNone(ob.card_keyboard("Записала ✨ Гулять 3 раза. Верно?", "ru"))
        self.assertIsNotNone(ob.card_keyboard("Три прогулки на этой неделе. Подходит?", "ru"))
        self.assertIsNotNone(ob.card_keyboard("By week 12 you walk daily.\n\nDoes this goal work for you?", "en"))

    def test_not_on_other_questions(self):
        self.assertIsNone(ob.card_keyboard("Что для тебя сейчас важнее всего?", "ru"))
        self.assertIsNone(ob.card_keyboard("", "ru"))

    def test_no_body_questions(self):
        self.assertIn("Never ask her weight, height", ob.WHY_DIG_SYSTEM)
        self.assertIn("Never ask her weight, height", GOAL_DIALOG_SYSTEM)


class TimeButtons(unittest.TestCase):
    def test_presets(self):
        kb = ob.time_pick_keyboard("ru")
        self.assertEqual([r[0].text for r in kb.inline_keyboard],
                         ["8:00 и 20:00", "9:00 и 21:00", "10:00 и 22:00", "Другое время"])
        self.assertEqual(ob.time_preset("9-21"), ("09:00", "21:00"))
        self.assertIsNone(ob.time_preset("other"))

    def _tap(self, data, st):
        cid = 777001
        main.onboarding[cid] = st
        msg = mock.Mock(chat_id=cid, text="Последний вопрос")
        query = mock.Mock(data=data, message=msg, from_user=SimpleNamespace(language_code="ru"))
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        update = SimpleNamespace(callback_query=query, effective_chat=None, effective_user=None)
        context = SimpleNamespace(bot=mock.Mock(send_message=mock.AsyncMock()),
                                  bot_data={"claude_model_names": ["m"]})
        with mock.patch.object(main, "_restore_cid_flow"), \
             mock.patch.object(main, "_touch_user_message_date"), \
             mock.patch.object(main.ob, "_complete_onboarding", new=mock.AsyncMock()) as done:
            asyncio.run(main.handle_time_callback(update, context))
        main.onboarding.pop(cid, None)
        return done, context

    def test_one_tap_finishes(self):
        st = {"step": ob.OB_MORNING_TIME, "lang": "ru"}
        done, _ = self._tap("ob_time:9-21", st)
        done.assert_awaited_once()
        self.assertEqual((st["morning_time"], st["evening_time"]), ("09:00", "21:00"))

    def test_other_asks_typed_question(self):
        st = {"step": ob.OB_MORNING_TIME, "lang": "ru"}
        done, context = self._tap("ob_time:other", st)
        done.assert_not_awaited()
        context.bot.send_message.assert_awaited_once()
        self.assertNotIn("morning_time", st)

    def test_stale_button(self):
        st = {"step": ob.OB_DONE, "lang": "ru"}
        done, context = self._tap("ob_time:8-20", st)
        done.assert_not_awaited()


class NoNameNudge(unittest.TestCase):
    def test_text(self):
        self.assertIn("как тебя зовут", ob.s("onboarding_reminder_noname", "ru"))
        self.assertIn("name", ob.s("onboarding_reminder_noname", "en"))


if __name__ == "__main__":
    unittest.main()
