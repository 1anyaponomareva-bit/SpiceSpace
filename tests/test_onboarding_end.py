"""End of onboarding (Anya, 21.11.2026): 3 messages, no «доброе утро» right after,
greeting by the hour, no long dashes anywhere."""

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402
from text_case import short_dashes  # noqa: E402


class Dashes(unittest.TestCase):
    def test_short(self):
        self.assertEqual(short_dashes("И ещё — в какое время?"), "И ещё - в какое время?")
        self.assertEqual(short_dashes("— Привет\n— Пока"), "- Привет\n- Пока")
        self.assertEqual(short_dashes("2—3 раза"), "2-3 раза")
        self.assertEqual(short_dashes("без тире"), "без тире")

    def test_paced_bot_sends_short(self):
        import bot_pacing

        kwargs = {"text": "привет — как ты"}
        bot_pacing._cap_text((), kwargs, 1)
        self.assertEqual(kwargs["text"], "Привет - как ты")


class Greeting(unittest.TestCase):
    def test_by_hour(self):
        self.assertEqual(main.greeting_for_hour("Аня, доброе утро 🌅", 9), "Аня, доброе утро 🌅")
        self.assertEqual(main.greeting_for_hour("Аня, доброе утро 🌅", 13), "Аня, привет 🌅")
        self.assertEqual(main.greeting_for_hour("Доброе утро, Аня", 22), "Добрый вечер, Аня")
        self.assertEqual(main.greeting_for_hour("Anya, good morning", 15), "Anya, hi")


class QuietSlots(unittest.TestCase):
    def test_saved_with_the_profile(self):
        st = {"name": "Аня", "main_goal": "g", "weekly_goal": "w", "morning_time": "13:00",
              "evening_time": "23:00", "timezone": "Europe/Moscow", "language_code": "ru"}
        saved = {}
        with mock.patch.object(ob.db, "get_profile", side_effect=[{}, None]), \
             mock.patch.object(ob.db, "upsert_profile", side_effect=lambda cid, p: saved.update(p)), \
             mock.patch.object(ob.db, "save_subscriber"), \
             mock.patch.object(ob, "save_onboarding_summary"), \
             mock.patch.object(ob, "datetime") as dt:
            dt.now.return_value = datetime(2026, 11, 21, 18, 25, tzinfo=ob.ZoneInfo("Europe/Moscow"))
            ob.persist_profile(1, st, [])
        self.assertEqual(saved.get("last_morning_sent_date"), "2026-11-21")  # 13:00 already passed


class Sequence(unittest.TestCase):
    def test_three_messages_one_button(self):
        sent = []

        class Msg:
            async def reply_text(self, text, reply_markup=None, **kw):
                sent.append((text, reply_markup))

        prof = {"name": "Аня", "main_goal": "Питаться по системе", "weekly_goal": "Продумывать рацион с белком",
                "morning_time": "13:00", "evening_time": "23:00", "timezone": "Europe/Moscow",
                "language_code": "ru", "trial_start_date": "2026-11-21"}
        update = SimpleNamespace(message=Msg())
        context = SimpleNamespace(bot_data={}, bot=mock.Mock())
        st = {"age_note": True, "lang": "ru", "language_code": "ru"}

        async def live(*a, **k):
            return "Сейчас уже вечер, так что давай просто наметим завтрашний завтрак 🙂 Что у тебя есть дома?"

        with mock.patch.object(ob, "persist_profile", return_value=dict(prof)), \
             mock.patch.object(ob.db, "update_profile"), \
             mock.patch.object(ob.db, "save_history_turn"), \
             mock.patch.object(ob, "next_step_message", side_effect=live), \
             mock.patch.object(ob.asyncio, "sleep", new=mock.AsyncMock()) as slept:
            asyncio.run(ob._complete_onboarding(update, context, 5, st, {}, {}, {}, set(), []))
        texts = [t for t, _ in sent]
        self.assertEqual(len(sent), 3, texts)
        self.assertTrue(texts[0].startswith("Всё запомнила"))
        self.assertIn("Первое сообщение - в 13:00", texts[0])
        self.assertTrue(texts[1].startswith("И ещё коротко"))
        self.assertIn("завтрашний завтрак", texts[2])
        # Anya, 22.11.2026: the Mini App button under the summary («Погнали»), only there
        self.assertIsNotNone(sent[0][1])
        self.assertIsNone(sent[1][1])
        self.assertIsNone(sent[2][1])
        context.bot.send_message.assert_not_called()  # no separate «Смотри как выглядит прогресс»
        pauses = [c.args[0] for c in slept.await_args_list]
        self.assertEqual(pauses, [ob.SUMMARY_TO_NOTE_S, ob.NOTE_TO_QUESTION_S])

    def test_fallback_by_hour(self):
        async def run(hour):
            with mock.patch.object(ob, "_claude_plain_text", side_effect=lambda *a, **k: k["fallback"]):
                return await ob.next_step_message({"weekly_goal": "w"}, "ru", datetime(2026, 11, 21, hour, 0), [])

        self.assertIn("уже сегодня", asyncio.run(run(10)))
        self.assertIn("завтра", asyncio.run(run(22)))


if __name__ == "__main__":
    unittest.main()
