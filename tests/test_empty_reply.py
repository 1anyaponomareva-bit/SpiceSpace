"""Empty or refused model replies: strict callers get an exception, others a fallback phrase."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import claude_client  # noqa: E402
from claude_budget import is_billing_error  # noqa: E402
from claude_client import ClaudeBillingError, ClaudeEmptyReply, generate  # noqa: E402
from lang_context import empty_fallback, has_cyrillic, refusal_fallback  # noqa: E402


class _FakeMessages:
    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason

    def create(self, **_kwargs):
        return SimpleNamespace(content=[], stop_reason=self.stop_reason, usage=None)


class _FakeClient:
    def __init__(self, stop_reason: str) -> None:
        self.messages = _FakeMessages(stop_reason)


class EmptyReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_client = claude_client._client
        self._saved_stopped = claude_client._billing_stopped
        claude_client._billing_stopped = False

    def tearDown(self) -> None:
        claude_client._client = self._saved_client
        claude_client._billing_stopped = self._saved_stopped

    def _call(self, stop_reason: str, **kwargs) -> str:
        claude_client._client = _FakeClient(stop_reason)
        return generate("claude-haiku-4-5", [{"role": "user", "content": "hi"}], **kwargs)

    def test_strict_raises_on_empty_and_refusal(self) -> None:
        for stop in ("end_turn", "refusal"):
            with self.subTest(stop=stop):
                with self.assertRaises(ClaudeEmptyReply):
                    self._call(stop, lang="en", strict=True)

    def test_strict_empty_is_not_billing(self) -> None:
        with self.assertRaises(ClaudeEmptyReply) as ctx:
            self._call("refusal", strict=True)
        self.assertNotIsInstance(ctx.exception, ClaudeBillingError)
        self.assertFalse(is_billing_error(ctx.exception))
        self.assertFalse(claude_client._billing_stopped)
        self.assertEqual(self._call("end_turn"), empty_fallback("ru"))

    def test_non_strict_returns_fallback_in_user_language(self) -> None:
        self.assertEqual(self._call("end_turn", lang="en"), empty_fallback("en"))
        self.assertEqual(self._call("refusal", lang="en"), refusal_fallback("en"))
        self.assertFalse(has_cyrillic(self._call("end_turn", lang="en")))
        self.assertFalse(has_cyrillic(self._call("refusal", lang="en")))
        self.assertEqual(self._call("end_turn", lang="ru"), "Напиши ещё раз — я слушаю.")
        self.assertEqual(self._call("refusal"), refusal_fallback("ru"))


class ScheduledCallsAreStrictTests(unittest.TestCase):
    def test_morning_evening_reengagement_pass_strict(self) -> None:
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        strict_by_feature: dict[str, bool] = {}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "claude_generate"
            ):
                continue
            kw = {k.arg: k.value for k in node.keywords}
            feature = kw.get("feature")
            if isinstance(feature, ast.Constant):
                strict = kw.get("strict")
                strict_by_feature[feature.value] = (
                    isinstance(strict, ast.Constant) and strict.value is True
                )
        for feature in ("morning", "evening", "reengagement"):
            self.assertTrue(strict_by_feature.get(feature), feature)
        self.assertFalse(strict_by_feature.get("chat", False))


if __name__ == "__main__":
    unittest.main()
