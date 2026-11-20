"""Language: Russian when she writes Russian, switch when she asks."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lang_switch  # noqa: E402


class Detect(unittest.TestCase):
    def test_requests(self):
        for t, want in (
            ("пиши по-английски", "en"), ("Давай на английском", "en"), ("can you speak English?", "en"),
            ("говори по-русски пожалуйста", "ru"), ("переключи на русский", "ru"), ("switch to Russian", "ru"),
        ):
            self.assertEqual(lang_switch.requested(t), want, t)
        for t in ("хочу выучить английский", "I love Russian food", "привет", "Аня"):
            self.assertEqual(lang_switch.requested(t), "", t)

    def test_implied(self):
        self.assertEqual(lang_switch.implied("Привет, как дела", "en"), "ru")
        self.assertEqual(lang_switch.implied("Привет", "ru"), "")
        self.assertEqual(lang_switch.implied("hello there", "en"), "")
        self.assertEqual(lang_switch.implied("ok", "en"), "")


class Handler(unittest.TestCase):
    def _run(self, raw, prof):
        import main

        sent = []

        async def reply(msg, text, **kw):
            sent.append(text)

        update = mock.Mock()
        with mock.patch.object(main, "_bot_reply", reply), \
             mock.patch.object(main.db_store, "update_profile") as up:
            used = asyncio.run(main._handle_language_switch(update, 4242, raw, prof))
        main.user_profiles.pop("4242", None)
        return used, sent, up

    def test_request_switches_and_confirms(self):
        prof = {"language_code": "ru", "name": "A"}
        used, sent, up = self._run("пиши по-английски", prof)
        self.assertTrue(used)
        self.assertEqual(prof["language_code"], "en")
        self.assertEqual(sent, [lang_switch.CONFIRM["en"]])
        up.assert_called_once()

    def test_russian_text_switches_quietly(self):
        prof = {"language_code": "en", "name": "A"}
        used, sent, _ = self._run("Привет, хочу похудеть", prof)
        self.assertFalse(used)
        self.assertEqual(prof["language_code"], "ru")
        self.assertEqual(sent, [])

    def test_no_profile_nothing(self):
        used, sent, up = self._run("пиши по-английски", None)
        self.assertFalse(used)
        up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
