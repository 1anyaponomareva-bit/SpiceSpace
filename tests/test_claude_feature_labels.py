"""Every Claude call in the bot carries feature=..., so claude_usage logs show where tokens go."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ("main.py", "onboarding_flow.py", "summaries.py", "goal_change.py")
FILES_WITH_CALLS = ("main.py", "onboarding_flow.py", "summaries.py")


def _claude_generate_calls(tree: ast.Module) -> list[ast.Call]:
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "claude_client":
            for alias in node.names:
                if alias.name == "generate":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "claude_client":
                    modules.add(alias.asname or alias.name)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in names:
            calls.append(node)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "generate"
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ):
            calls.append(node)
    return calls


class FeatureLabelTests(unittest.TestCase):
    def test_every_claude_call_has_feature(self):
        missing: list[str] = []
        for name in FILES:
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
            calls = _claude_generate_calls(tree)
            if name in FILES_WITH_CALLS:
                self.assertTrue(calls, f"{name}: no claude_client.generate calls found")
            for call in calls:
                if not any(kw.arg == "feature" for kw in call.keywords):
                    missing.append(f"{name}:{call.lineno}")
        self.assertEqual(missing, [], "Claude calls without feature=: " + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
