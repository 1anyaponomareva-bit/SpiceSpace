"""Run: pytest test_lang_context.py

The last test checks the real chat system prompt and passes only after the
edits in LANGUAGE_FIX.md are applied to prompts.py.
"""

from datetime import datetime

import lang_context as lc

SAT = datetime(2026, 10, 17, 14, 30)  # a Saturday


def test_time_is_in_user_language():
    assert lc.format_user_time(SAT, "en") == "14:30, Saturday October 17"
    assert lc.format_user_time(SAT, "ru") == "14:30, суббота 17 октября"
    assert lc.format_user_time(SAT, None) == "14:30, Saturday October 17"


def test_time_line_regex_matches_both_labels():
    ru = f"{lc.time_label('ru')}: 09:00, среда 1 января"
    en = f"{lc.time_label('en')}: 09:00, Wednesday January 1"
    assert lc.TIME_LINE_RE.search(ru)
    assert lc.TIME_LINE_RE.search(en)
    assert lc.TIME_LINE_RE.sub("X", "a\n" + en + "\nb") == "a\nX\nb"


def test_english_pieces_have_no_cyrillic():
    pieces = [
        lc.COACH_STYLE_INSTRUCTION_EN,
        lc.current_time_instruction("en"),
        lc.time_label("en"),
        lc.facts_header("en"),
        lc.personality_intro("en"),
        lc.last_week_text({"summary": "s", "achievements": "a", "challenges": "c"}, "en"),
        lc.personality_text({"strengths": "x", "blockers": "y"}, "en"),
        lc.refusal_fallback("en"),
        lc.empty_fallback("en"),
    ]
    for key in ("spark", "flow", "power", "legend", "unknown"):
        pieces.append(lc.level_context(key, "Spark", 3, "en"))
    for text in pieces:
        assert not lc.has_cyrillic(text), text[:80]


def test_russian_pieces_stay_russian():
    assert lc.has_cyrillic(lc.facts_header("ru"))
    assert lc.has_cyrillic(lc.current_time_instruction("ru"))
    assert lc.has_cyrillic(lc.level_context("flow", "Поток", 5, "ru"))


def test_english_level_ignores_russian_level_name():
    ru_names = {
        "spark": "Искра",
        "flow": "Поток",
        "power": "Сила",
        "legend": "Легенда",
    }
    for key, ru_name in ru_names.items():
        text = lc.level_context(key, ru_name, 5, "en")
        assert not lc.has_cyrillic(text), text


def test_coach_style_picks_by_language():
    ru_text = "Русский текст"
    assert lc.coach_style_for("ru", ru_text) == ru_text
    assert lc.coach_style_for("en", ru_text) == lc.COACH_STYLE_INSTRUCTION_EN
    assert lc.coach_style_for(None, ru_text) == lc.COACH_STYLE_INSTRUCTION_EN


def test_real_english_chat_system_has_no_cyrillic():
    import prompts

    profile = {
        "language_code": "en",
        "name": "Anna",
        "main_goal": "Launch the app",
        "weekly_goal": "Record three videos",
        "current_week": 2,
        "timezone": "Asia/Ho_Chi_Minh",
    }
    extra = "\n\n".join(
        [
            lc.facts_header("en") + "\n— prefers short answers",
            lc.personality_intro("en"),
            lc.level_context("flow", "Flow", 5, "en"),
        ]
    )
    system = prompts.build_chat_system(
        profile,
        None,
        None,
        extra=extra,
        coach_style=lc.coach_style_for("en", "RU-ONLY"),
    )
    assert not lc.has_cyrillic(system), [
        line for line in system.splitlines() if lc.has_cyrillic(line)
    ]
    assert prompts.get_current_time_for_user(profile).split(", ")[1].split()[0] in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    )
    msg = prompts.user_message_with_fresh_time(profile, "hello")
    assert not lc.has_cyrillic(msg)
