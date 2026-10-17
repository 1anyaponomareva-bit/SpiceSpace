"""Send a fixed English dialog through the live-chat prompt and report Cyrillic in replies.

Builds the system prompt the way _coach_reply does for an English profile and calls
Claude with feature="chat", lang="en". Nothing is read from or written to the database:
the profile, summaries, personality and last week are in-memory test data, and
state_updates are only printed, never applied.

Run from spicespace-bot:
    .\\.venv\\Scripts\\python.exe check_english_leaks.py
    .\\.venv\\Scripts\\python.exe check_english_leaks.py --no-history
    .\\.venv\\Scripts\\python.exe check_english_leaks.py --no-history --only 6,8,18
"""

from __future__ import annotations

import argparse
import ast
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from claude_budget import parse_structured_reply, recent_history, usage_report  # noqa: E402
from claude_client import build_model_chain, generate as claude_generate, select_model_id  # noqa: E402
from lang_context import (  # noqa: E402
    coach_style_for,
    facts_header,
    has_cyrillic,
    last_week_text,
    level_context,
    personality_intro,
    personality_text,
)
from prompts import (  # noqa: E402
    build_chat_system,
    refresh_user_time_in_system,
    specialty_instructions,
    user_message_with_fresh_time,
)

MESSAGES = [
    "hi",
    "I'm so tired today, can't do anything",
    "I want to launch my app in 12 weeks",
    "What day is it today?",
    "I skipped my task again",
    "Can you write me a script for a 30-second video about procrastination?",
    "I don't want to post every day, don't suggest that again",
    "Make me a meal plan for a busy week",
    "I feel stuck and I don't know why",
    "Should I post three videos at once?",
    "My friend says I'm doing too many things at once",
    "I did the task!",
    "What time is it?",
    "I want to change my goal to learning Spanish",
    "Remind me to call mom tomorrow at 6pm",
    "I'm anxious about money",
    "What model are you?",
    "Give me a hook for a TikTok about burnout",
    "I want to change my morning time",
    "Thanks, that's all for today",
]

PROFILE = {
    "user_id": "0",
    "language_code": "en",
    "name": "Emma",
    "vision": "Have my own app with paying users and work from anywhere",
    "main_goal": "Launch my habit-tracker app and get the first 100 users",
    "weekly_goal": "Finish the onboarding screens",
    "current_week": 3,
    "morning_time": "08:30",
    "evening_time": "21:00",
    "timezone": "Europe/London",
}
YESTERDAY = {
    "summary": "Worked on the login screen, got distracted by social media in the afternoon.",
    "key_detail": "Feels guilty when she skips a day",
}
TODAY = {
    "summary": "Morning check-in: planned to design two onboarding screens.",
    "task": "Design two onboarding screens",
    "task_completed": "",
}
FACTS = [
    "Works a full-time job until 6pm",
    "Prefers short, direct answers",
]
PERSONALITY = {
    "communication_style": "short and direct",
    "motivation_triggers": "visible progress",
    "best_time_of_day": "early morning",
    "blockers": "perfectionism",
}
LAST_WEEK = {
    "summary": "Set up the project and the database schema.",
    "achievements": "Finished the data model",
    "challenges": "Lost two evenings to overtime",
}
LEVEL_KEY, LEVEL_NAME, COMPLETED = "spark", "Spark", 4

ENGLISH_ONLY_PREFIX = (
    "CRITICAL: This user speaks ONLY English. "
    "NEVER write in Russian. NEVER mix languages. "
    "If you accidentally wrote Russian — that is a bug. "
    "Every single word must be in English.\n\n"
)

_MAIN_NAMES = {
    "CHAT_ONE_QUESTION_RULE",
    "CHAT_ONE_QUESTION_RULE_EN",
    "COACH_STYLE_INSTRUCTION",
    "_current_goal_only_rule",
    "_chat_one_question_rule",
    "_hist_to_claude_messages",
}


def _load_from_main() -> dict:
    """Take the chat rules straight from main.py so the script follows the bot."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _MAIN_NAMES:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _MAIN_NAMES for t in node.targets
        ):
            nodes.append(node)
    ns: dict = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    missing = _MAIN_NAMES - ns.keys()
    if missing:
        raise SystemExit(f"main.py is missing: {sorted(missing)}")
    return ns


def build_system(main_ns: dict, user_text: str) -> str:
    lang = PROFILE["language_code"]
    extra_parts = [
        main_ns["_current_goal_only_rule"](PROFILE),
        main_ns["_chat_one_question_rule"](PROFILE),
    ]
    topic = specialty_instructions(user_text, lang=lang)
    if topic:
        extra_parts.append(topic)
    facts_text = "\n".join(f"— {f}" for f in FACTS[:3])
    extra_parts.append(f"{facts_header(lang)}\n{facts_text}")
    extra_parts.append(f"{personality_intro(lang)}\n{personality_text(PERSONALITY, lang)}")
    extra_parts.append(last_week_text(LAST_WEEK, lang))
    extra_parts.append(level_context(LEVEL_KEY, LEVEL_NAME, COMPLETED, lang))
    extra = ENGLISH_ONLY_PREFIX + "\n\n".join(extra_parts)
    return build_chat_system(
        PROFILE,
        YESTERDAY,
        TODAY,
        extra=extra,
        coach_style=coach_style_for(lang, main_ns["COACH_STYLE_INSTRUCTION"]),
    )


def ask(main_ns: dict, model: str, hist: list[dict], user_text: str) -> tuple[str, str, dict]:
    system = refresh_user_time_in_system(PROFILE, build_system(main_ns, user_text))
    messages = main_ns["_hist_to_claude_messages"](
        recent_history(hist),
        user_message_with_fresh_time(PROFILE, user_text),
    )
    raw = claude_generate(
        model,
        messages,
        system=system,
        feature="chat",
        user_id="leak-check",
        lang="en",
    )
    parsed = parse_structured_reply(raw)
    reply = (parsed.get("reply") or raw).strip()
    return raw, reply, parsed.get("state_updates") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="send every message as a fresh chat instead of one running dialog",
    )
    parser.add_argument("--model", default="", help="override the model id")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated message numbers from the list, e.g. 6,8,18",
    )
    args = parser.parse_args()
    selected = list(enumerate(MESSAGES, 1))
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        bad = wanted - set(range(1, len(MESSAGES) + 1))
        if bad:
            parser.error(f"--only: no such message numbers {sorted(bad)}")
        selected = [(i, t) for i, t in selected if i in wanted]

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING)
    main_ns = _load_from_main()
    model = args.model or build_model_chain(select_model_id())[0]
    print(f"model: {model}, history: {'off' if args.no_history else 'on'}\n")

    hist: list[dict] = []
    leaks: list[tuple[int, str, str]] = []
    for i, text in selected:
        raw, reply, updates = ask(main_ns, model, [] if args.no_history else hist, text)
        leaked = has_cyrillic(raw)
        mark = "CYRILLIC" if leaked else "ok"
        prompt_note = " (system prompt has Cyrillic)" if has_cyrillic(build_system(main_ns, text)) else ""
        print(f"[{i:2}] {mark:8} > {text}{prompt_note}")
        print(f"     {reply[:300].replace(chr(10), ' / ')}")
        if any(v not in (None, "", False) for v in updates.values()):
            print(f"     state_updates (not applied): {updates}")
        if leaked:
            leaks.append((i, text, raw))
        hist.append({"role": "user", "parts": [text]})
        hist.append({"role": "model", "parts": [reply]})

    print("\n" + "=" * 70)
    print(f"Replies with Cyrillic: {len(leaks)} of {len(selected)}")
    for i, text, raw in leaks:
        print(f"\n[{i}] > {text}\n{raw}")
    print("\n" + usage_report())
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
