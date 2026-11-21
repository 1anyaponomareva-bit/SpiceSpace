"""Cheaper Claude calls without changing replies (Anya, 22.11.2026): onboarding rules read
from cache, the goal's topic block in the cached system prompt, name extraction on the
cheap model."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_client  # noqa: E402
import onboarding_flow as ob  # noqa: E402
from claude_budget import DYNAMIC_MARKER  # noqa: E402
from prompts import specialty_instructions  # noqa: E402


class OnboardingSystem(unittest.TestCase):
    def test_rules_first_then_step(self):
        for lang in ("ru", "en"):
            system = ob._system_with_lang("STEP PROMPT", lang)
            rules, step = system.split(DYNAMIC_MARKER)
            self.assertIn("SAFETY", rules)
            self.assertIn(ob.PRICE_RULE_EN, rules)
            self.assertEqual(step, "STEP PROMPT")
        self.assertTrue(ob._system_with_lang("x", "ru").startswith("IMPORTANT: User speaks Russian"))

    def test_rules_are_the_same_for_every_step(self):
        a = ob._system_with_lang("one", "ru").split(DYNAMIC_MARKER)[0]
        b = ob._system_with_lang("two", "ru").split(DYNAMIC_MARKER)[0]
        self.assertEqual(a, b)

    def test_blocks(self):
        system = ob._system_with_lang("STEP", "ru")
        blocks = claude_client._system_blocks(system, cache_core=True)
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", blocks[1])
        both = claude_client._system_blocks(system, cache_core=True, cache_dynamic=True)
        self.assertIn("cache_control", both[1])

    def test_marker_never_reaches_the_model(self):
        plain = claude_client._system_blocks("a" + DYNAMIC_MARKER + "b", cache_core=False)
        self.assertNotIn("DYNAMIC", plain)

    def test_json_reply_passes_cache_flag(self):
        seen = {}

        def fake(model, messages, **kw):
            seen.update(kw)
            return '{"message": "hi"}'

        with mock.patch.object(ob, "claude_generate", side_effect=fake):
            ob.generate_json_reply("claude-sonnet-5-5", [{"role": "user", "content": "x"}],
                                   system="s", cache_dynamic=True)
        self.assertTrue(seen["cache_core"])
        self.assertTrue(seen["cache_dynamic"])


class NameOnCheapModel(unittest.TestCase):
    def test_haiku_first(self):
        models = []

        def fake(model, messages, **kw):
            models.append(model)
            return "Аня"

        with mock.patch.object(ob, "claude_generate", side_effect=fake):
            name = asyncio.run(ob._extract_name("я аня", ["claude-sonnet-5-5"], lang="ru"))
        self.assertEqual(models[0], "claude-haiku-5-5")
        self.assertEqual(name, "Аня")


class GoalTopic(unittest.TestCase):
    def test_goal_block_without_message(self):
        self.assertTrue(specialty_instructions("", lang="ru", goal_text="Похудеть на 5 кг"))
        self.assertFalse(specialty_instructions("", lang="ru", goal_text="Запустить блог"))

    def test_message_block_minus_goal_block_is_empty(self):
        goal = specialty_instructions("", lang="ru", goal_text="Похудеть")
        msg = specialty_instructions("что съесть на ужин, чтобы похудеть", lang="ru")
        self.assertEqual(msg.replace(goal, "").strip(), "")


if __name__ == "__main__":
    unittest.main()
