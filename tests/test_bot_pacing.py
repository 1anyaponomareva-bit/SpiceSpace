"""Messages sent back to back to one chat are spaced out; other chats are not delayed."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot_pacing  # noqa: E402


class PacingTests(unittest.TestCase):
    def test_gap_between_messages_same_chat(self) -> None:
        async def run():
            bot_pacing._next_slot.clear()
            bot_pacing._locks.clear()
            t0 = time.monotonic()
            stamps = []
            for _ in range(3):
                await bot_pacing.wait_turn(1, gap=0.2)
                stamps.append(time.monotonic() - t0)
            await bot_pacing.wait_turn(2, gap=0.2)
            other = time.monotonic() - t0
            return stamps, other

        stamps, other = asyncio.run(run())
        self.assertLess(stamps[0], 0.05)
        self.assertGreaterEqual(stamps[1], 0.19)
        self.assertGreaterEqual(stamps[2], 0.39)
        self.assertLess(other - stamps[2], 0.05)


if __name__ == "__main__":
    unittest.main()
