"""Goal card shown as plain text still moves onboarding to the goal step."""

import asyncio
import unittest
from unittest import mock

import onboarding_flow as ob


class PlainCardTests(unittest.TestCase):
    def test_plain_text_card_counts_as_ready(self):
        card = "К 12-й неделе у тебя будет первый оплаченный заказ.\nПодходит такая цель?"
        with mock.patch.object(ob, "generate_json_reply", return_value=(card, card)):
            r = asyncio.run(ob._claude_vision_dialog(
                [{"role": "user", "content": "хочу свои деньги"}], ["m"], lang="ru"))
        self.assertTrue(r["ready_for_goal"])

    def test_plain_question_is_not_a_card(self):
        q = "А что для тебя будет результатом через 12 недель?"
        with mock.patch.object(ob, "generate_json_reply", return_value=(q, q)):
            r = asyncio.run(ob._claude_vision_dialog(
                [{"role": "user", "content": "в деньгах"}], ["m"], lang="ru"))
        self.assertFalse(r["ready_for_goal"])


if __name__ == "__main__":
    unittest.main()
