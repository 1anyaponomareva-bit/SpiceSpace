"""Run scripted goal-setting dialogs through the real onboarding prompts and check the replies.

Each scenario walks the onboarding stages the way the bot does: WHY_DIG_SYSTEM while
she says what she wants (_claude_vision_dialog), GOAL_DIALOG_SYSTEM with the goal card
(_claude_goal_dialog), then WEEKLY_TACTICS_DIALOG_SYSTEM (_claude_weekly_tactics_dialog).
Nothing is read from or written to the database and nothing is sent to Telegram.

Next user turn after each bot reply: the next scripted line if any is left; otherwise
"да"/"yes" if the reply has the card question or is the WHY_DIG draft; otherwise the
scenario ends. At most 4 bot replies per scenario. The button card the bot shows after
a confirmed 12-week goal is skipped: the weekly stage opens right away.

Not mirrored: the "don't know" side path (only flagged), the retry when the model repeats
its last question, and the 8-turn cap.

Run from spicespace-bot:
    .\\.venv\\Scripts\\python.exe check_goal_dialog.py
    .\\.venv\\Scripts\\python.exe check_goal_dialog.py --only 6,8,1en
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import onboarding_flow as ob  # noqa: E402
from claude_budget import usage_report, usage_snapshot  # noqa: E402
from claude_client import build_model_chain, select_model_id  # noqa: E402
from goal_change import can_lock_as_goal, looks_like_confusion  # noqa: E402
from lang_context import has_cyrillic  # noqa: E402

MAX_BOT_REPLIES = 4
YES = {"ru": "да", "en": "yes"}
STOP_PHRASES = (
    "really want",
    "what is behind",
    "fulfilled",
    "на самом деле хочешь",
    "что за этим стоит",
    "по-настоящему важно",
)

S6_RU = (
    "Хочу запустить телеграм-бота и получить первых 10 платных пользователей "
    "за 12 недель, могу тратить 3 часа в день"
)
S6_EN = (
    "I want to launch a Telegram bot and get the first 10 paying users in 12 weeks, "
    "I can spend 3 hours a day"
)

# (id, lang, situation, scripted user lines)
SCENARIOS: list[tuple[str, str, str, list[str]]] = [
    ("1", "ru", "does not know what she wants", ["Хочу реализации, что и как не знаю"]),
    ("2", "ru", "incompatible wishes", [
        "Хочу и похудеть на 10 кг, и запустить бизнес, и переехать в другую страну за три месяца",
    ]),
    ("3", "ru", "goal imposed by others", [
        "Родители говорят, что мне надо купить квартиру, наверное это моя цель",
    ]),
    ("4", "ru", "too big", ["Хочу заработать миллион долларов за 12 недель"]),
    ("5", "ru", "too small", ["Хочу пить воду"]),
    ("6", "ru", "clear goal", [S6_RU]),
    ("7", "ru", "tired", ["Нет сил ни на что, даже думать о цели"]),
    ("8", "ru", "changes goal", ["хочу худеть", "нет, хочу научиться снимать видео"]),
    ("9", "ru", "wants result, not talk", ["Просто скажи, какой мне контент снимать"]),
    ("10", "ru", "irritated", ["Бесит эти вопросы, ответь нормально"]),
    ("11", "ru", "side question mid-dialog", [S6_RU, "А вообще тикток лучше или ютуб?"]),
    ("1en", "en", "does not know what she wants", [
        "I want to make something of myself, but I don't know what or how",
    ]),
    ("4en", "en", "too big", ["I want to earn a million dollars in 12 weeks"]),
    ("6en", "en", "clear goal", [S6_EN]),
    ("10en", "en", "irritated", ["These questions are annoying, just answer normally"]),
]

# Mirrors the hints in the OB_GOAL_DIALOG branch of onboarding_flow; keep in sync.
_GOAL_HINT_CONFUSION = {
    "en": (
        "She did not understand the previous question. Admit the wording failed "
        "and explain it more simply or change approach. Do not start a new questionnaire. "
        "ready=false and goal empty."
    ),
    "ru": (
        "Она не поняла предыдущий вопрос. Признай, что формулировка не удалась, "
        "и объясни проще или смени подход. Новую анкету не начинай. "
        "ready=false, goal пустой."
    ),
}
_GOAL_HINT_DEFAULT = {
    "en": (
        "Respond to what she just said. Do not use a stock coaching question. "
        "ready=true only after she confirms a goal she actually stated."
    ),
    "ru": (
        "Ответь на то, что она только что сказала. Не используй заготовленный коучинговый вопрос. "
        "ready=true только после подтверждения цели, которую она сама назвала."
    ),
}
# Mirrors dig_hint in the OB_VISION_DIALOG branch of onboarding_flow; keep in sync.
_DIG_HINT = {
    "en": (
        "She already named a concrete goal. If her words already show why it matters, "
        "do not ask why: move to a draft. Ask one question only if a fact needed for "
        "the draft is missing. Set ready_for_goal=true once the intention is clear."
    ),
    "ru": (
        "Она уже назвала конкретную цель. Если из её слов уже понятно, зачем ей это, "
        "не спрашивай зачем: переходи к черновику. Один вопрос — только если для "
        "черновика не хватает факта. ready_for_goal=true, как только намерение ясно."
    ),
}

_raw_calls: list[str] = []
_feature = ["goal_check"]
_real_generate = ob.claude_generate


def _capturing_generate(*args, **kwargs):
    kwargs.setdefault("feature", _feature[0])
    text = _real_generate(*args, **kwargs)
    _raw_calls.append(text)
    return text


ob.claude_generate = _capturing_generate


@dataclass
class BotReply:
    stage: str
    user_text: str | None
    message: str
    flags: dict
    fallback: bool
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _card_question(stage: str, lang: str) -> str:
    if stage == "goal":
        return ob.s("goal_final_question", lang)
    if stage == "weekly":
        return ob.s("weekly_confirm_question", lang)
    return ""


def _total_tokens() -> tuple[int, int]:
    snap = usage_snapshot()
    inp = sum(
        b["input_tokens"] + b["cache_read_input_tokens"] + b["cache_creation_input_tokens"]
        for b in snap.values()
    )
    out = sum(b["output_tokens"] for b in snap.values())
    return inp, out


async def _call_stage(
    stage: str,
    lang: str,
    models: list[str],
    st: dict,
    user_text: str | None,
) -> tuple[dict, bool, list[str]]:
    notes: list[str] = []
    _raw_calls.clear()
    _feature[0] = f"goal_check:{stage}"
    if stage == "why":
        turns = st["why_turns"]
        same_streak = ob._assistant_same_question_streak(turns)
        dig_hint = _DIG_HINT[lang] if ob._looks_like_concrete_goal(user_text or "") else ""
        hint = dig_hint or ob._switch_approach_hint(lang)
        result = await ob._claude_vision_dialog(
            turns,
            models,
            extra_user_hint=hint if (same_streak >= 2 or dig_hint) else "",
            lang=lang,
        )
        parser = ob._parse_vision_dialog_json
        default = ob.ob_text("vision_fallback", lang)
    elif stage == "goal":
        turns = st["goal_turns"]
        same_streak = ob._assistant_same_question_streak(turns)
        confused = looks_like_confusion(user_text or "")
        why_extra = _GOAL_HINT_CONFUSION[lang] if confused else _GOAL_HINT_DEFAULT[lang]
        extra = why_extra if confused else (
            ob._switch_approach_hint(lang) if same_streak >= 2 else why_extra
        )
        send_hint = confused or same_streak >= 2 or len(turns) <= 4
        result = await ob._claude_goal_dialog(
            turns,
            models,
            vision=st["vision"],
            extra_user_hint=extra if send_hint else "",
            lang=lang,
            prior_dialog="",
        )
        parser = ob._parse_goal_dialog_json
        default = ob.ob_text("goal_fallback", lang)
    else:
        turns = st["weekly_turns"]
        result = await ob._claude_weekly_tactics_dialog(
            turns,
            models,
            main_goal=st["main_goal"],
            user_message=user_text or "",
            lang=lang,
        )
        parser = ob._parse_weekly_change_json
        default = ob.ob_text("weekly_dialog_fallback", lang)
    fallback = not _raw_calls or parser(_raw_calls[-1]) is None
    if fallback:
        notes.append("FALLBACK: no valid JSON from the model, the bot's canned reply was used")
        for raw in _raw_calls:
            notes.append(f"raw model output: {raw}")
    if not str(result.get("message") or "").strip():
        result["message"] = default
    return result, fallback, notes


def _check(reply: BotReply, lang: str, confirm_turn: bool) -> None:
    msg = reply.message
    card = _card_question(reply.stage, lang)
    has_card = bool(card) and card in msg
    other_q = msg.count("?") - (card.count("?") if has_card else 0)
    allowed = 0 if has_card else 1
    if other_q > allowed:
        reply.failures.append(
            f"too many '?': {other_q} besides the card question (allowed {allowed})"
        )
    low = msg.lower()
    for phrase in STOP_PHRASES:
        if phrase in low:
            reply.failures.append(f"stop phrase: «{phrase}»")
    if reply.stage in ("goal", "weekly") and reply.flags.get("ready") and not confirm_turn:
        reply.failures.append("ready=true before a confirmation turn")
    if lang == "en" and has_cyrillic(msg):
        reply.failures.append("Cyrillic in an English run")


async def run_scenario(
    sid: str, lang: str, lines: list[str], models: list[str]
) -> tuple[list[BotReply], list[str]]:
    st = {"why_turns": [], "goal_turns": [], "weekly_turns": [], "vision": "", "main_goal": ""}
    pending = list(lines)
    stage = "why"
    user_text: str | None = pending.pop(0)
    confirm_turn = False
    replies: list[BotReply] = []
    end_note = ""

    while len(replies) < MAX_BOT_REPLIES:
        turns = st[f"{stage}_turns"]
        notes: list[str] = []
        if user_text is not None:
            turns.append({"role": "user", "content": user_text})
            if stage in ("why", "goal") and ob._user_looks_unsure_about_goal(user_text):
                notes.append(
                    "note: in the bot this turn goes to the \"don't know\" question, not this prompt"
                )
        result, fallback, call_notes = await _call_stage(stage, lang, models, st, user_text)
        message = str(result["message"]).strip()
        turns.append({"role": "assistant", "content": message})
        flags = {k: v for k, v in result.items() if k != "message"}
        reply = BotReply(stage, user_text, message, flags, fallback, notes + call_notes)
        _check(reply, lang, confirm_turn)
        replies.append(reply)

        card = _card_question(stage, lang)
        has_card = bool(card) and card in message
        drafted = stage == "why" and bool(result.get("ready_for_goal"))

        if drafted:
            st["vision"] = ob._collect_vision_from_turns(st["why_turns"])
            stage = "goal"
        elif stage == "goal":
            goal = str(result.get("goal") or "")
            if (
                result.get("ready")
                and goal
                and can_lock_as_goal(goal)
                and not looks_like_confusion(user_text or "")
            ):
                st["main_goal"] = goal
                stage = "weekly"
                user_text = None
                confirm_turn = False
                continue
        elif stage == "weekly" and result.get("ready") and result.get("weekly_goal"):
            end_note = f"weekly goal set: {result['weekly_goal']}"
            break

        if pending:
            user_text = pending.pop(0)
            confirm_turn = False
        elif has_card or drafted:
            user_text = YES[lang]
            confirm_turn = has_card
        else:
            end_note = "script ended; the bot is waiting for her answer"
            break
    else:
        end_note = end_note or f"stopped at {MAX_BOT_REPLIES} bot replies"

    summary: list[str] = []
    if sid.startswith("6"):
        card_at = next(
            (
                i
                for i, r in enumerate(replies, 1)
                if _card_question(r.stage, lang)
                and _card_question(r.stage, lang) in r.message
                and r.stage == "goal"
            ),
            None,
        )
        if card_at is None or card_at > 2:
            summary.append(f"goal card not reached by bot reply 2 (got: {card_at or 'never'})")
    if end_note:
        summary.insert(0, end_note)
    return replies, summary


def _print_dialog(sid: str, lang: str, situation: str, replies: list[BotReply]) -> None:
    print("=" * 78)
    print(f"Scenario {sid} [{lang}] {situation}")
    print("=" * 78)
    for i, r in enumerate(replies, 1):
        if r.user_text is not None:
            print(f"\nShe: {r.user_text}")
        flag_text = ", ".join(f"{k}={v!r}" for k, v in r.flags.items())
        print(f"\nSpace #{i} [{r.stage}{'; ' + flag_text if flag_text else ''}]:")
        print(r.message)
        for note in r.notes:
            print(f"  ({note})")
        for fail in r.failures:
            print(f"  !! {fail}")


async def main_async(args: argparse.Namespace) -> int:
    models = [args.model] if args.model else build_model_chain(select_model_id())
    selected = SCENARIOS
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        known = {sid for sid, *_ in SCENARIOS}
        bad = wanted - known
        if bad:
            raise SystemExit(f"--only: no such scenarios {sorted(bad)}; known: {sorted(known)}")
        selected = [sc for sc in SCENARIOS if sc[0] in wanted]

    print(f"models: {models}\n")
    results: list[tuple[str, str, list[str], int, int]] = []
    for sid, lang, situation, lines in selected:
        in0, out0 = _total_tokens()
        replies, summary = await run_scenario(sid, lang, lines, models)
        in1, out1 = _total_tokens()
        _print_dialog(sid, lang, situation, replies)
        failures = [f"reply #{i}: {f}" for i, r in enumerate(replies, 1) for f in r.failures]
        failures += [s for s in summary if s.startswith("goal card")]
        info = [s for s in summary if not s.startswith("goal card")]
        print("\n--- checks:", "OK" if not failures else f"{len(failures)} failed")
        for line in failures:
            print(f"    FAIL {line}")
        for line in info:
            print(f"    {line}")
        print(f"--- tokens: {in1 - in0} input / {out1 - out0} output\n")
        results.append((sid, lang, failures, in1 - in0, out1 - out0))

    print("=" * 78)
    print("SUMMARY")
    for sid, lang, failures, tin, tout in results:
        status = "OK" if not failures else f"{len(failures)} failed"
        print(f"  {sid:>5} [{lang}] {status:<10} {tin} in / {tout} out")
    tin, tout = _total_tokens()
    print(f"\nTotal: {tin} input / {tout} output tokens")
    print("\n" + usage_report())
    return 1 if any(f for _, _, f, _, _ in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="", help="override the model id")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated scenario ids, e.g. 6,8,1en",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
