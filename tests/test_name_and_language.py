"""21.11.2026: English greeting after /reset full, "No name found" taken as a name,
and English kept while she writes in Russian."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402


class ExtractName(unittest.TestCase):
    def _extract(self, model_says: str, raw: str) -> str:
        with mock.patch.object(ob, "claude_generate", return_value=model_says):
            return asyncio.run(ob._extract_name(raw, ["m"], lang="ru"))

    def test_no_name_answers_are_empty(self):
        for said in ("No name found", "NONE", "Имя не указано", "нет имени"):
            self.assertEqual(self._extract(said, "Что?"), "", said)

    def test_real_name_kept(self):
        self.assertEqual(self._extract("Аня", "меня зовут аня"), "Аня")

    def test_fallback_without_model_skips_questions(self):
        with mock.patch.object(ob, "claude_generate", side_effect=RuntimeError("down")):
            self.assertEqual(asyncio.run(ob._extract_name("Что?", ["m"], lang="ru")), "")
            self.assertEqual(asyncio.run(ob._extract_name("я Маша", ["m"], lang="ru")), "Маша")


class NameStep(unittest.TestCase):
    def _turn(self, st, raw, extracted):
        sent = []

        class Msg:
            chat_id = 77

            async def reply_text(self, text, reply_markup=None):
                sent.append(text)

        update = mock.Mock()
        update.effective_chat.id = 77
        update.effective_user.language_code = "en"
        update.message = Msg()
        context = mock.Mock()
        context.bot_data = {}
        flows = {77: st}
        with mock.patch.object(ob, "_extract_name", mock.AsyncMock(return_value=extracted)), \
             mock.patch.object(ob, "typing_while") as tw:
            tw.return_value.__aenter__ = mock.AsyncMock()
            tw.return_value.__aexit__ = mock.AsyncMock(return_value=False)
            asyncio.run(ob.handle_onboarding_turn(update, context, raw, flows, {}, {}, set()))
        return sent, flows[77]

    def test_question_instead_of_name_asks_again_in_russian(self):
        st = {"step": ob.OB_NAME, "lang": "en", "language_code": "en", "age_ok": True}
        sent, st = self._turn(st, "Что?", "")
        self.assertEqual(sent, [ob.NAME_AGAIN_RU])  # switched to Russian, asked again
        self.assertEqual(st["lang"], "ru")
        self.assertEqual(st["step"], ob.OB_NAME)


class FunnelNoProfile(unittest.TestCase):
    def test_no_flag_write_without_profile(self):
        main.user_profiles.pop("555", None)
        with mock.patch.object(main.db_store, "mark_cycle_flag") as mark:
            main._mark_funnel(555, {"step": ob.OB_NAME, "age_ok": True})
        mark.assert_not_called()


if __name__ == "__main__":
    unittest.main()
