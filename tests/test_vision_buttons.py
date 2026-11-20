"""Buttons under the goal question: a tap counts as her typed answer."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402


class Keyboard(unittest.TestCase):
    def test_labels_and_choice(self):
        data = [b.callback_data for row in ob.vision_keyboard("ru").inline_keyboard for b in row]
        self.assertEqual(data[0], "ob_vis:lose")
        self.assertIn("ob_vis:other", data)
        labels = [b.text for row in ob.vision_keyboard("ru").inline_keyboard for b in row]
        self.assertEqual(labels[0], "Похудеть")
        self.assertEqual(ob.vision_choice_text("lose", "ru"), "Похудеть")
        self.assertEqual(ob.vision_choice_text("food", "en"), "Eat healthier")
        self.assertEqual(ob.vision_choice_text("other", "ru"), "")
        self.assertEqual(ob.vision_choice_text("x", "ru"), "")


class Callback(unittest.TestCase):
    def _run(self, data, st):
        cid = 9001
        main.onboarding[cid] = st
        query = mock.Mock()
        query.data = data
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        query.edit_message_reply_markup = mock.AsyncMock()
        query.message.chat_id = cid
        query.message.text = "Расскажи, что тебе хочется?"
        query.from_user.language_code = "ru"
        update = mock.Mock()
        update.callback_query = query
        context = mock.Mock()
        context.bot.send_message = mock.AsyncMock()
        turn = mock.AsyncMock()
        with mock.patch.object(main, "_restore_cid_flow"), \
             mock.patch.object(main, "_touch_user_message_date"), \
             mock.patch.object(main.ob, "handle_onboarding_turn", turn):
            asyncio.run(main.handle_vision_callback(update, context))
        main.onboarding.pop(cid, None)
        return query, context, turn

    def test_tap_goes_on_as_her_answer(self):
        st = {"step": ob.OB_VISION_DIALOG, "lang": "ru", "vision_turns": [{"role": "assistant", "content": "q"}]}
        query, context, turn = self._run("ob_vis:lose", st)
        self.assertEqual(turn.call_args.args[2], "Похудеть")
        query.edit_message_text.assert_awaited_once()
        self.assertIn("👉 Похудеть", query.edit_message_text.call_args.args[0])

    def test_other_asks_to_write(self):
        st = {"step": ob.OB_VISION_DIALOG, "lang": "ru", "vision_turns": []}
        _, context, turn = self._run("ob_vis:other", st)
        turn.assert_not_called()
        self.assertIn("своими словами", context.bot.send_message.call_args.kwargs["text"])

    def test_stale_or_already_answered(self):
        _, _, turn = self._run("ob_vis:lose", {"step": ob.OB_GOAL_DIALOG, "lang": "ru"})
        turn.assert_not_called()
        st = {"step": ob.OB_VISION_DIALOG, "lang": "ru",
              "vision_turns": [{"role": "assistant", "content": "q"}, {"role": "user", "content": "бегать"}]}
        _, _, turn = self._run("ob_vis:lose", st)
        turn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
