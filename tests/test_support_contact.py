"""Support: users can reach the owner in Telegram DMs for problems and ideas."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_lang_paths import _main_functions  # noqa: E402


class SupportContact(unittest.TestCase):
    def setUp(self):
        ns = _main_functions({"support_text"}, {"SUPPORT_USERNAME": "panomareva"})
        self.text = ns["support_text"]

    def test_ru_mentions_contact_and_ideas(self):
        t = self.text("ru")
        self.assertIn("@panomareva", t)
        self.assertIn("не работает", t)
        self.assertIn("Предложения", t)

    def test_en(self):
        t = self.text("en")
        self.assertIn("@panomareva", t)
        self.assertIn("idea", t)

    def test_wired_everywhere(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('CommandHandler(["support", "help"], cmd_support)', main)
        self.assertIn("@panomareva", (ROOT / "prompts.py").read_text(encoding="utf-8"))
        self.assertIn("btn-support", (ROOT / "webapp" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("t.me/panomareva", (ROOT / "webapp" / "app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
