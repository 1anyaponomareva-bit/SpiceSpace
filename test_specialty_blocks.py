"""Run after wiring specialty_blocks_en into prompts.specialty_instructions."""

import re

import prompts
import specialty_blocks_en as sb

CYR = re.compile(r"[А-Яа-яЁё]")

EN_TRIGGERS = {
    "Can you write me a script for a 30-second video about procrastination?": "TRIGGER HOOK",
    "Give me a hook for a TikTok about burnout": "HOOK FORMULA",
    "Make me a meal plan for a busy week": "NUTRITION",
    "I need a workout for three days a week": "WORKOUTS",
}


def test_english_user_gets_english_blocks_only():
    for text, marker in EN_TRIGGERS.items():
        out = prompts.specialty_instructions(text, "en")
        assert marker in out, (text, out[:80])
        assert not CYR.search(out), text


def test_no_block_without_trigger():
    assert prompts.specialty_instructions("I feel stuck and I don't know why", "en") == ""
    assert prompts.specialty_instructions("What day is it today?", "en") == ""


def test_russian_user_gets_original_russian_blocks():
    for text, ru_const in (
        ("напиши сценарий для рилса", prompts.REELS_SCRIPT_STRUCTURE),
        ("нужен хук для видео", prompts.HOOK_FORMULA_INSTRUCTION),
        ("составь меню на неделю", prompts.FITNESS_NUTRITION_INSTRUCTION),
    ):
        out = prompts.specialty_instructions(text, "ru")
        assert ru_const.strip() in out


def test_default_language_keeps_old_behaviour():
    # callers that were not updated still get the Russian block
    out = prompts.specialty_instructions("напиши сценарий для рилса")
    assert prompts.REELS_SCRIPT_STRUCTURE.strip() in out


def test_english_blocks_keep_the_hard_rules():
    assert "1200 kcal" in sb.FITNESS_NUTRITION_INSTRUCTION_EN
    assert "No questions in the hook" in sb.HOOK_FORMULA_INSTRUCTION_EN
    assert "Subscribe" in sb.REELS_SCRIPT_STRUCTURE_EN
