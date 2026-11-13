"""Morning: Spice's own unconfirmed suggestion must not be presented as her task."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_lang_paths import _main_functions  # noqa: E402


class MorningAgreedTaskRule(unittest.TestCase):
    def setUp(self):
        self.rule = _main_functions({"_morning_only_agreed_task_rule"}, {})[
            "_morning_only_agreed_task_rule"
        ]

    def test_ru_rule(self):
        text = self.rule(True)
        self.assertIn("явно подтвердил", text)
        self.assertIn("не договорённость", text)

    def test_en_rule(self):
        text = self.rule(False)
        self.assertIn("clearly confirmed", text)
        self.assertIn("not a plan", text)

    def test_rule_used_in_both_morning_prompts(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("_morning_only_agreed_task_rule(True)", src)
        self.assertIn("_morning_only_agreed_task_rule(False)", src)


if __name__ == "__main__":
    unittest.main()
