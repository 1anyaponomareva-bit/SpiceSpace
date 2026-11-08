"""Claude 5 request options: no prefill, thinking off for short replies, effort set."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import claude_client
import onboarding_flow


class ModelOptionTests(unittest.TestCase):
    def test_defaults_are_claude_5(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_MODEL", None)
            self.assertEqual(claude_client.select_model_id(), "claude-sonnet-5-5")
        self.assertEqual(claude_client.SCHEDULE_MODEL, "claude-haiku-5-5")

    def test_request_options(self):
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": ""}):
            self.assertEqual(
                claude_client.model_request_options("claude-sonnet-5-5"),
                {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}},
            )
            self.assertEqual(
                claude_client.model_request_options("claude-haiku-5-5"),
                {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}},
            )
            self.assertEqual(claude_client.model_request_options("claude-sonnet-4-5"), {})

    def test_prefill_only_for_old_models(self):
        self.assertTrue(claude_client.supports_prefill("claude-sonnet-4-5"))
        self.assertFalse(claude_client.supports_prefill("claude-sonnet-5-5"))
        self.assertFalse(claude_client.supports_prefill("claude-haiku-5-5"))

    def test_json_reply_skips_prefill_on_claude_5(self):
        calls = []

        def fake_generate(model_id, messages, **kw):
            calls.append((messages, kw.get("feature")))
            return '{"reply": "ок"}'

        with mock.patch.object(onboarding_flow, "claude_generate", fake_generate):
            json_text, _ = onboarding_flow.generate_json_reply(
                "claude-sonnet-5-5",
                [{"role": "user", "content": "привет"}],
                system="s",
                feature="onb_goal_dialog",
            )
        self.assertEqual(len(calls), 1)  # one call, no 400-and-retry
        self.assertEqual(calls[0][0][-1]["role"], "user")
        self.assertEqual(calls[0][1], "onb_goal_dialog")
        self.assertEqual(json_text, '{"reply": "ок"}')

    def test_generate_sends_options(self):
        sent = {}

        def create(**kw):
            sent.update(kw)
            return SimpleNamespace(
                model="claude-sonnet-5-5",
                stop_reason="end_turn",
                content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="Привет")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
            )

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with mock.patch.object(claude_client, "get_client", return_value=client), \
             mock.patch.object(claude_client, "_billing_stopped", False):
            text = claude_client.generate("claude-sonnet-5-5", [{"role": "user", "content": "x"}], feature="chat")
        self.assertEqual(text, "Привет")
        self.assertEqual(sent["thinking"], {"type": "between_tools"})
        self.assertNotIn("temperature", sent)


if __name__ == "__main__":
    unittest.main()
