"""Onboarding goal flow: stage hand-off and reply shape."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import onboarding_flow as ob  # noqa: E402


class DigToGoalHandOffTests(unittest.TestCase):
    def test_goal_turns_start_with_the_draft(self) -> None:
        draft = (
            "За 12 недель запустить телеграм-бота и получить 10 платных пользователей, "
            "работая по 3 часа в день. Берём?"
        )
        vision_turns = [
            {"role": "user", "content": "Хочу запустить телеграм-бота и 10 платных"},
            {"role": "assistant", "content": draft},
        ]
        st = {"step": ob.OB_VISION_DIALOG, "dont_know_streak": 2}
        ob.start_goal_dialog_after_dig(st, vision_turns)
        self.assertEqual(st["step"], ob.OB_GOAL_DIALOG)
        self.assertEqual(st["goal_turns"], [{"role": "assistant", "content": draft}])
        self.assertEqual(st["dont_know_streak"], 0)
        self.assertIn("телеграм-бота", st["vision"])
        self.assertNotIn("Берём", st["vision"])

    def test_no_draft_means_empty_goal_turns(self) -> None:
        st: dict = {}
        ob.start_goal_dialog_after_dig(st, [{"role": "user", "content": "хочу худеть"}])
        self.assertEqual(st["goal_turns"], [])


class KeepOneQuestionTests(unittest.TestCase):
    def test_two_questions_become_one_ru(self) -> None:
        text = (
            "Миллион за 12 недель — это цель. А что ты будешь делать, чтобы их заработать? "
            "У тебя есть бизнес, проект или идея?"
        )
        self.assertEqual(
            ob.keep_one_question(text),
            "Миллион за 12 недель — это цель. А что ты будешь делать, чтобы их заработать?",
        )

    def test_two_questions_become_one_en(self) -> None:
        text = "What would count for you? Like, what would actually be different?"
        self.assertEqual(ob.keep_one_question(text), "What would count for you?")

    def test_one_question_is_unchanged(self) -> None:
        for text in (
            "Три серьёзные вещи за три месяца — это много. Что из этого самое важное?",
            "A million in 12 weeks is a huge jump. What do you do now?",
            "Окей, записала.",
        ):
            self.assertEqual(ob.keep_one_question(text), text)

    def test_question_inside_brackets_is_not_cut(self) -> None:
        one = "Что именно ты делаешь? Чем именно ты занимаешься как дизайнер (сайты, интерфейсы, брендинг?)"
        self.assertEqual(ob.keep_one_question(one), "Что именно ты делаешь?")
        whole = (
            "Хорошо. Чем именно ты занимаешься как дизайнер (сайты, интерфейсы, брендинг?) "
            "и кто твои клиенты сейчас? А сколько берёшь за проект?"
        )
        self.assertEqual(
            ob.keep_one_question(whole),
            "Хорошо. Чем именно ты занимаешься как дизайнер (сайты, интерфейсы, брендинг?) "
            "и кто твои клиенты сейчас?",
        )
        unclosed = "Чем именно ты занимаешься как дизайнер (сайты, интерфейсы, брендинг? Кто клиенты?"
        self.assertEqual(
            ob.keep_one_question(unclosed),
            "Чем именно ты занимаешься как дизайнер (сайты, интерфейсы, брендинг?)",
        )

    def test_question_inside_quotes_is_not_cut(self) -> None:
        text = "Ты сказала «а зачем?», и это честно. Что для тебя важнее? Деньги или время?"
        self.assertEqual(
            ob.keep_one_question(text),
            "Ты сказала «а зачем?», и это честно. Что для тебя важнее?",
        )
        text_en = 'You said "why bother?" and that is fair. What matters more? Money or time?'
        self.assertEqual(
            ob.keep_one_question(text_en),
            'You said "why bother?" and that is fair. What matters more?',
        )

    def test_card_keeps_its_final_question(self) -> None:
        for lang, card in (
            ("ru", "1. Запустить бота и 10 платных\n2. Видишь оплаты\n3. 3 часа в день\n"),
            ("en", "1. Launch the bot and 10 paying users\n2. You see payments\n3. 3 hours a day\n"),
        ):
            final = ob.s("goal_final_question", lang)
            self.assertEqual(ob.keep_one_question(card + final, final), card + final)
            extra = card.replace("3 часа в день", "сколько часов?").replace(
                "3 hours a day", "how many hours?"
            )
            kept = ob.keep_one_question(extra + final, final)
            self.assertTrue(kept.endswith(final))


class PlainTextReplyTests(unittest.TestCase):
    RAW_10EN = "Got it - I'll just talk with you normally. What's on your mind?"

    def test_plain_text_is_used(self) -> None:
        self.assertEqual(ob.plain_text_reply(self.RAW_10EN, "ready_for_goal"), self.RAW_10EN)

    def test_broken_or_empty_output_is_rejected(self) -> None:
        for raw in (
            "",
            "   ",
            '{"message": "Привет", "ready_for_goal": tr',
            '```json\n{"message": "hi"',
            'Sure. "message": "hi", "ready_for_goal": false',
        ):
            self.assertEqual(ob.plain_text_reply(raw, "ready_for_goal"), "", raw)

    def test_dig_stage_uses_plain_text_instead_of_canned_reply(self) -> None:
        import asyncio

        calls: list[str] = []

        def fake_generate(model, messages, **kwargs):
            calls.append(model)
            return self.RAW_10EN

        original = ob.claude_generate
        ob.claude_generate = fake_generate
        try:
            result = asyncio.run(
                ob._claude_vision_dialog(
                    [{"role": "user", "content": "These questions are annoying"}],
                    ["m1", "m2"],
                    lang="en",
                )
            )
        finally:
            ob.claude_generate = original
        self.assertEqual(result, {"message": self.RAW_10EN, "ready_for_goal": False})
        self.assertEqual(calls, ["m1"])

    def test_canned_dig_reply_has_no_why_question(self) -> None:
        turns = [
            {"role": "user", "content": "хочу запустить проект"},
            {"role": "user", "content": "свой бизнес на маркетплейсе"},
        ]
        self.assertEqual(
            ob._fallback_vision_reply(turns, "ru")["message"],
            "Расскажи подробнее, что ты имеешь в виду?",
        )
        self.assertEqual(
            ob._fallback_vision_reply(turns, "en")["message"],
            "Tell me a bit more about what you mean.",
        )


class _BadRequest(Exception):
    status_code = 400


class _Overloaded(Exception):
    status_code = 529


class JsonPrefillTests(unittest.TestCase):
    TURNS = [{"role": "user", "content": "Хочу запустить бота"}]

    def _run(self, replies: list) -> tuple[dict, list[list[dict]]]:
        import asyncio

        sent: list[list[dict]] = []
        queue = list(replies)

        def fake_generate(model, messages, **kwargs):
            sent.append(list(messages))
            reply = queue.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        original = ob.claude_generate
        ob.claude_generate = fake_generate
        try:
            result = asyncio.run(ob._claude_vision_dialog(self.TURNS, ["m1", "m2"], lang="ru"))
        finally:
            ob.claude_generate = original
        return result, sent

    def test_prefilled_reply_is_parsed(self) -> None:
        result, sent = self._run(['"message": "Поняла. Что за бот?", "ready_for_goal": false}'])
        self.assertEqual(result, {"message": "Поняла. Что за бот?", "ready_for_goal": False})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][-1], {"role": "assistant", "content": "{"})

    def test_full_json_despite_prefill_is_not_doubled(self) -> None:
        result, sent = self._run(['{"message": "Поняла.", "ready_for_goal": false}'])
        self.assertEqual(result["message"], "Поняла.")
        self.assertEqual(len(sent), 1)

    def test_400_on_prefill_retries_without_it(self) -> None:
        result, sent = self._run(
            [_BadRequest("prefill not supported"), '{"message": "Поняла.", "ready_for_goal": true}']
        )
        self.assertEqual(result, {"message": "Поняла.", "ready_for_goal": True})
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0][-1], {"role": "assistant", "content": "{"})
        self.assertEqual(sent[1][-1]["role"], "user")

    def test_other_errors_are_not_retried_without_prefill(self) -> None:
        result, sent = self._run(
            [_Overloaded("overloaded"), '"message": "Поняла.", "ready_for_goal": false}']
        )
        self.assertEqual(result["message"], "Поняла.")
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1][-1], {"role": "assistant", "content": "{"})


if __name__ == "__main__":
    unittest.main()
