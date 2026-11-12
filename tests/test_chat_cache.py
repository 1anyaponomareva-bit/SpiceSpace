"""Chat caching: stable history window, cache breakpoints, no per-minute time in system."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_budget import DYNAMIC_MARKER, recent_history_stable  # noqa: E402
from claude_client import _system_blocks, with_history_cache  # noqa: E402
from lang_context import TIME_LINE_RE, time_label  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402


class ChatCacheTests(unittest.TestCase):
    def test_window_start_moves_in_jumps(self) -> None:
        starts = []
        for n in range(12, 40):
            hist = [{"i": i} for i in range(n)]
            win = recent_history_stable(hist, limit=12, step=8)
            self.assertGreaterEqual(len(win), 12)
            self.assertLess(len(win), 20)
            self.assertEqual(win[-1]["i"], n - 1)
            starts.append(win[0]["i"])
        # the start stays the same for 8 messages in a row
        self.assertEqual(starts[:8], [0] * 8)
        self.assertEqual(starts[8:16], [8] * 8)

    def test_short_history_whole(self) -> None:
        self.assertEqual(len(recent_history_stable([{}] * 5)), 5)

    def test_history_breakpoint(self) -> None:
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "new"},
        ]
        out = with_history_cache(msgs)
        self.assertEqual(out[1]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(out[2]["content"], "new")
        self.assertEqual(msgs[1]["content"], "b")  # original untouched
        self.assertEqual(with_history_cache([{"role": "user", "content": "x"}]), [{"role": "user", "content": "x"}])

    def test_dynamic_block_cached_only_for_chat(self) -> None:
        system = "core" + DYNAMIC_MARKER + "profile"
        chat = _system_blocks(system, cache_core=True, cache_dynamic=True)
        self.assertIn("cache_control", chat[1])
        other = _system_blocks(system, cache_core=True)
        self.assertNotIn("cache_control", other[1])

    def test_system_time_line_is_stable(self) -> None:
        ns = _main_functions({"stable_time_in_system", "_with_turn_rules"},
                             {"TIME_LINE_RE": TIME_LINE_RE, "time_label": time_label})
        sys_a = f"x\n{time_label('ru')}: 21:03, четверг 12 ноября\ny"
        sys_b = f"x\n{time_label('ru')}: 21:04, четверг 12 ноября\ny"
        a = ns["stable_time_in_system"]({"language_code": "ru"}, sys_a)
        b = ns["stable_time_in_system"]({"language_code": "ru"}, sys_b)
        self.assertEqual(a, b)
        self.assertNotIn("21:03", a)
        msg = ns["_with_turn_rules"]("[время]\n\nпривет", "Правило", "ru")
        self.assertTrue(msg.startswith("[время]\n\nпривет"))
        self.assertIn("Правило", msg)
        self.assertEqual(ns["_with_turn_rules"]("x", "", "ru"), "x")


if __name__ == "__main__":
    unittest.main()
