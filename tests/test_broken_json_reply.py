"""A chat reply with unescaped quotes inside "reply" must not reach the user as raw JSON."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_budget import parse_structured_reply  # noqa: E402

BROKEN = '''```json
{
  "reply": "You want to replace "Launch my habit-tracker app" with learning Spanish?\\n\\nWhat happened with the app?",
  "state_updates": {
    "task_completed": null,
    "new_task": null,
    "new_goal": null,
    "weekly_goal_update": null,
    "important_fact": null,
    "person": null
  }
}
```'''


class BrokenJsonReplyTests(unittest.TestCase):
    def test_reply_salvaged(self) -> None:
        out = parse_structured_reply(BROKEN)
        self.assertNotIn('"state_updates"', out["reply"])
        self.assertNotIn("```", out["reply"])
        self.assertTrue(out["reply"].startswith('You want to replace "Launch my habit-tracker app"'))
        self.assertIn("\n\nWhat happened with the app?", out["reply"])
        self.assertIsNone(out["state_updates"]["new_goal"])

    def test_valid_json_unchanged(self) -> None:
        out = parse_structured_reply('{"reply": "Привет", "state_updates": {"new_task": "X"}}')
        self.assertEqual(out["reply"], "Привет")
        self.assertEqual(out["state_updates"]["new_task"], "X")

    def test_plain_text(self) -> None:
        self.assertEqual(parse_structured_reply("Просто текст")["reply"], "Просто текст")


if __name__ == "__main__":
    unittest.main()
