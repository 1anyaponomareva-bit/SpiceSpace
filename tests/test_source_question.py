"""«Откуда ты узнала?» for new users who came without a ?start= label."""

import asyncio
import unittest
from unittest import mock

import onboarding_flow as ob
import spice_stats


class SourceQuestionTests(unittest.TestCase):
    def test_keyboard_codes_and_labels(self):
        data = [b.callback_data for row in ob.source_keyboard("ru").inline_keyboard for b in row]
        self.assertEqual(
            data,
            ["ob_src:tt", "ob_src:rl", "ob_src:th", "ob_src:tg", "ob_src:fr", "ob_src:other", "ob_src:skip"],
        )
        self.assertEqual(ob.source_label("tt"), "tt_ask")
        self.assertEqual(ob.source_label("other"), "other_ask")
        self.assertEqual(ob.source_label("skip"), "")
        # the label survives the same cleaning as a start label
        for code in ob.SOURCE_CODES:
            label = ob.source_label(code)
            self.assertEqual(spice_stats.clean_label(label), label)

    def test_typed_answer_mapping(self):
        cases = {
            "из тиктока": "tt",
            "TikTok": "tt",
            "в инсте увидела": "rl",
            "reels": "rl",
            "тредс": "th",
            "в телеге канал": "tg",
            "подруга скинула": "fr",
            "от друга": "fr",
            "другое": "other",
            "Аня": "other",
        }
        for text, code in cases.items():
            self.assertEqual(ob.source_code_from_text(text), code, text)

    def test_save_only_when_no_label_yet(self):
        with mock.patch.object(ob.db, "get_profile", return_value={"cycle_flags": {}}), \
                mock.patch.object(ob.db, "mark_cycle_flag") as mark:
            self.assertEqual(ob.save_source_answer(5, "tt"), "tt_ask")
            mark.assert_called_once_with(5, "src_tt_ask")
        with mock.patch.object(ob.db, "get_profile", return_value={"cycle_flags": {"src_th_1311": True}}), \
                mock.patch.object(ob.db, "mark_cycle_flag") as mark:
            self.assertEqual(ob.save_source_answer(5, "tt"), "")
            mark.assert_not_called()
        with mock.patch.object(ob.db, "mark_cycle_flag") as mark:
            self.assertEqual(ob.save_source_answer(5, "skip"), "")
            mark.assert_not_called()

    def test_profile_label_reads_answer(self):
        prof = {"cycle_flags": {"src_tt_ask": True}}
        self.assertEqual(spice_stats.profile_label(prof), "tt_ask")

    def test_typed_answer_then_name_question(self):
        flows: dict = {}
        ob.start_new_onboarding(flows, 9, "ru")
        flows[9]["age_ok"] = True
        flows[9]["src_ask"] = True
        sent = []

        class Msg:
            async def reply_text(self, text, reply_markup=None):
                sent.append(text)

        update = mock.Mock()
        update.effective_chat.id = 9
        update.effective_user.language_code = "ru"
        update.message = Msg()
        context = mock.Mock()
        context.bot_data = {}
        with mock.patch.object(ob.db, "get_profile", return_value={}), \
                mock.patch.object(ob.db, "mark_cycle_flag") as mark:
            asyncio.run(ob.handle_onboarding_turn(update, context, "из тиктока", flows, {}, {}, set()))
            mark.assert_called_once_with(9, "src_tt_ask")
        self.assertEqual(sent, [ob.age_reply_text("ask_name", "ru")])
        self.assertFalse(ob.source_pending(flows[9]))
        self.assertNotIn("name", flows[9])


if __name__ == "__main__":
    unittest.main()
