"""Cheaper prompts (Anya, 23.11.2026): common parts cached for an hour, the food block and the
onboarding rules shared by everyone, right prices in the log."""

import unittest
from unittest import mock

import claude_budget
import claude_client
import onboarding_flow as ob
import prompts
from claude_budget import DYNAMIC_MARKER, SHARED_MARKER

HOUR = {"type": "ephemeral", "ttl": "1h"}
FIVE = {"type": "ephemeral"}


def prof(name, goal):
    return {"name": name, "language_code": "ru", "main_goal": goal, "timezone": "Europe/Moscow"}


def chat_blocks(p):
    food = prompts.specialty_instructions("", lang="ru", goal_text=p["main_goal"])
    system = prompts.build_chat_system(p, None, None, extra="", coach_style="стиль", shared=food)
    return claude_client._system_blocks(system, cache_core=True, cache_dynamic=True, hour=True)


class Chat(unittest.TestCase):
    def test_food_block_is_one_copy(self):
        a = chat_blocks(prof("Анна", "похудеть на 5 кг"))
        b = chat_blocks(prof("Ольга", "наладить питание без срывов"))
        self.assertEqual([x.get("cache_control") for x in a], [HOUR, HOUR, FIVE])
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])
        self.assertIn("ПИТАНИЕ", a[1]["text"])
        self.assertNotEqual(a[2], b[2])
        self.assertIn("Анна", a[2]["text"])

    def test_other_goal_has_no_food(self):
        a = chat_blocks(prof("Ира", "выучить английский"))
        self.assertEqual(len(a), 2)
        self.assertNotIn("ПИТАНИЕ", a[0]["text"] + a[1]["text"])

    def test_at_most_four_breakpoints(self):
        blocks = chat_blocks(prof("Анна", "похудеть"))
        msgs = claude_client.with_history_cache([
            {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ])
        n = sum("cache_control" in x for x in blocks) + str(msgs).count("cache_control")
        self.assertEqual(n, 4)

    def test_rare_calls_keep_five_minutes(self):
        self.assertTrue(claude_client.long_cache("chat"))
        self.assertTrue(claude_client.long_cache("onb_why_dig"))
        for f in ("morning", "evening", "weekly_recap", "unspecified"):
            self.assertFalse(claude_client.long_cache(f), f)
        blocks = claude_client._system_blocks("a" + DYNAMIC_MARKER + "b", cache_core=True)
        self.assertEqual(blocks[0]["cache_control"], FIVE)


class Onboarding(unittest.TestCase):
    def blocks(self, system):
        return claude_client._system_blocks(system, cache_core=True, hour=True)

    def test_weekly_rules_shared(self):
        def one(goal):
            return self.blocks(ob._system_with_lang(prompts.WEEKLY_TACTICS_DIALOG_SYSTEM.format(
                main_goal=goal, user_message="u", dialog_history="d", confirm_question="Верно?"), "ru"))
        a, b = one("цель А"), one("цель Б")
        self.assertEqual(len(a), 3)
        self.assertEqual(a[:2], b[:2])
        self.assertIn("цель А", a[2]["text"])
        self.assertNotIn("cache_control", a[2])
        self.assertIn("Return JSON", a[1]["text"])

    def test_goal_rules_shared(self):
        def one(v):
            return self.blocks(ob._system_with_lang(prompts.GOAL_DIALOG_SYSTEM.format(
                vision=v, dialog_history="d", prior_dialog="p", final_question="Верно?"), "ru"))
        a, b = one("мечта А"), one("мечта Б")
        self.assertEqual(a[:2], b[:2])
        self.assertIn("мечта А", a[2]["text"])

    def test_why_dig_whole_step(self):
        a = self.blocks(ob._system_with_lang("STEP", "ru") + SHARED_MARKER + "\n\nAnswer JSON")
        self.assertEqual([x.get("cache_control") for x in a], [HOUR, HOUR, None])
        self.assertEqual(a[1]["text"], "STEP")

    def test_marker_never_reaches_the_model(self):
        for kw in ({"cache_core": False}, {"cache_core": True}):
            for s in ("a" + SHARED_MARKER + "b", "a" + SHARED_MARKER + "b" + DYNAMIC_MARKER + "c"):
                self.assertNotIn("SHARED", str(claude_client._system_blocks(s, **kw)))


class Prices(unittest.TestCase):
    def test_sonnet_55(self):
        m = "claude-sonnet-5-5"
        self.assertAlmostEqual(claude_budget.usage_cost_usd(m, cache_read_input_tokens=1_000_000), 0.10)
        self.assertAlmostEqual(claude_budget.usage_cost_usd(m, cache_creation_input_tokens=1_000_000), 2.50)
        self.assertAlmostEqual(claude_budget.usage_cost_usd(
            m, cache_creation_input_tokens=1_000_000, cache_creation_1h_tokens=1_000_000), 4.00)
        self.assertAlmostEqual(claude_budget.usage_cost_usd(
            m, cache_creation_input_tokens=1_000_000, cache_creation_1h_tokens=400_000), 3.10)

    def test_generate_counts_hour_writes(self):
        saved = []
        usage = mock.Mock(cache_read_input_tokens=0, cache_creation_input_tokens=1000, input_tokens=0,
                          output_tokens=0, cache_creation=mock.Mock(ephemeral_1h_input_tokens=1000))
        resp = mock.Mock(usage=usage, model="claude-sonnet-5-5",
                         content=[mock.Mock(type="text", text="ок")])
        client = mock.Mock()
        client.messages.create.return_value = resp
        with mock.patch.object(claude_client, "get_client", return_value=client), \
                mock.patch.object(claude_client, "persist_usage", saved.append):
            claude_client.generate("claude-sonnet-5-5", [{"role": "user", "content": "x"}],
                                   system="a" + DYNAMIC_MARKER + "b", feature="chat")
        self.assertAlmostEqual(saved[0]["cost_usd"], 0.004)
        self.assertEqual(client.messages.create.call_args.kwargs["system"][0]["cache_control"], HOUR)


if __name__ == "__main__":
    unittest.main()
