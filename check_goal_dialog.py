"""Run scripted goal-setting dialogs through the real onboarding prompts and check the replies.

Each scenario walks the onboarding stages the way the bot does: WHY_DIG_SYSTEM while
she says what she wants and until it sends the goal card (_claude_vision_dialog),
GOAL_DIALOG_SYSTEM taking her answer to the card (_claude_goal_dialog), then
WEEKLY_TACTICS_DIALOG_SYSTEM (_claude_weekly_tactics_dialog).
Nothing is read from or written to the database and nothing is sent to Telegram.

Next user turn after each bot reply: the next scripted line if any is left; otherwise
"да"/"yes" if the reply has the card question or ends the WHY_DIG stage; otherwise, if the
bot asked a question, her next backup answer from BACKUPS; otherwise the scenario ends.
"да"/"yes" right after a card question is the confirmation turn.
At most 4 bot replies per scenario. The button card the bot shows after
a confirmed 12-week goal is skipped: the weekly stage opens right away.

Not mirrored: the "don't know" side path (only flagged), the retry when the model repeats
its last question, and the 8-turn cap.

Run from spicespace-bot:
    .\\.venv\\Scripts\\python.exe check_goal_dialog.py
    .\\.venv\\Scripts\\python.exe check_goal_dialog.py --only 6,8,1en
    .\\.venv\\Scripts\\python.exe check_goal_dialog.py --out goal_dialogs.txt
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
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
    "запрос помощи",
    "request for help",
    "я спросила другое",
    "i asked something else",
    "нереально",
    "невозможно",
    "not going to happen",
    "unrealistic",
    "impossible",
    "won't happen",
    "won’t happen",
    "can't happen",
    "can’t happen",
    "не выйдет",
    "не получится",
    "не случится",
)
MIN_CARD_LINES = 3
CARD_LABELS = ("goal card", "цель:")
YES_WORDS = {"да", "yes"}
MASCULINE_RU = re.compile(
    r"(?:^|[.!?…]\s+)(Понял|Услышал|Записал|Перегнул|Согласен|Рад)\b"
)
CLASSIFYING_START = re.compile(
    r"^\s*(?:это|твоё последнее сообщение|твое последнее сообщение|this is|your last message)\b"
    r"[^.?!]{0,40}(?:запрос|просьб|request|wish|feeling|желание|эмоци)",
    re.IGNORECASE,
)
INVENTED_STEMS = ("курс", "нутрицио", "course", "nutrition")

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

# Her answers, in order, when the bot asks a question and the scripted lines are used up.
BACKUPS: dict[str, list[str]] = {
    "1": [
        "Наверное, хочу работу, которая нравится и приносит деньги",
        "Мне нравится рисовать и общаться с людьми",
        "Может, что-то своё, пусть маленькое",
    ],
    "2": [
        "Наверное, бизнес сейчас важнее всего",
        "Хочу запустить онлайн-магазин украшений, могу тратить 2 часа в день",
        "Переезд потом, сначала деньги",
    ],
    "3": [
        "Честно, квартиру я не очень хочу, хочу накопить на учёбу дизайну",
        "Хочу за 12 недель отложить 150 тысяч на курс",
        "Могу откладывать по 12 тысяч в неделю",
    ],
    "4": [
        "Сейчас я дизайнер на фрилансе, получаю около 1500 долларов в месяц",
        "Ну хотя бы выйти на 5000 в месяц",
        "Могу работать по 4 часа в день",
    ],
    "5": [
        "Я пью мало, к вечеру болит голова",
        "Хочу пить 2 литра в день",
        "Да, каждый день все 12 недель",
    ],
    "6": [
        "Бот будет напоминать мастерам маникюра о записях клиентов",
        "Мастерам маникюра, я сама этим занималась",
        "Да, всё так",
    ],
    "7": [
        "Работа выматывает, сплю по 5 часов",
        "Хочу просто высыпаться, ложиться до 23:00",
        "Хотя бы 5 дней в неделю",
    ],
    "8": [
        "Хочу снимать рилсы для блога о путешествиях",
        "Хочу за 12 недель выложить 24 ролика, могу тратить 5 часов в неделю",
        "Да",
    ],
    "9": [
        "У меня блог про уход за кожей, 800 подписчиков",
        "Хочу за 12 недель дойти до 3000 подписчиков, могу снимать 3 раза в неделю",
        "Да",
    ],
    "10": [
        "Хочу больше зарабатывать",
        "Хочу за 12 недель найти работу тестировщиком, учусь по 2 часа в день",
        "Да",
    ],
    "11": [
        "Бот для записи к психологу",
        "Да, давай так",
        "Окей",
    ],
    "1en": [
        "Maybe a job I actually enjoy that pays well",
        "I like drawing and talking to people",
        "Something of my own, even a small one",
    ],
    "4en": [
        "I'm a freelance designer making about $1,500 a month",
        "Okay, maybe $5,000 a month",
        "I can work 4 extra hours a day",
    ],
    "6en": [
        "The bot reminds nail artists about client bookings",
        "Nail artists, I used to be one",
        "Yes, that's right",
    ],
    "10en": [
        "I want to earn more",
        "I want to land a QA job in 12 weeks, I study 2 hours a day",
        "Yes",
    ],
}

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
        "do not ask why: send the goal card. Ask one question only if a fact needed for "
        "the card is missing. Set ready_for_goal=true once the intention is clear."
    ),
    "ru": (
        "Она уже назвала конкретную цель. Если из её слов уже понятно, зачем ей это, "
        "не спрашивай зачем: присылай карточку цели. Один вопрос — только если для "
        "карточки не хватает факта. ready_for_goal=true, как только намерение ясно."
    ),
}

_raw_calls: list[tuple[str, str]] = []
_feature = ["goal_check"]
_real_generate = ob.claude_generate


def _capturing_generate(model_id, messages, **kwargs):
    kwargs.setdefault("feature", _feature[0])
    text = _real_generate(model_id, messages, **kwargs)
    prefilled = bool(messages) and messages[-1] == ob._JSON_PREFILL
    body = text.strip()
    json_text = "{" + body if prefilled and not body.startswith("{") else body
    _raw_calls.append((json_text, body))
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
    if stage == "weekly":
        return ob.s("weekly_confirm_question", lang)
    return ob.s("goal_final_question", lang)


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
        json_keys: tuple[str, ...] = ("ready_for_goal",)
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
        json_keys = ('"ready"', '"goal"')
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
        json_keys = ('"ready"', '"weekly_goal"')
        default = ob.ob_text("weekly_dialog_fallback", lang)
    json_text, model_text = _raw_calls[-1] if _raw_calls else ("", "")
    bad_json = not _raw_calls or parser(json_text) is None
    fallback = bad_json and not ob.plain_text_reply(model_text, *json_keys)
    if bad_json and not fallback:
        notes.append("model skipped the JSON; its plain text was used as the reply")
    if fallback:
        notes.append("FALLBACK: no valid JSON from the model, the bot's canned reply was used")
        for _json_text, raw in _raw_calls:
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
    for label in CARD_LABELS:
        if label in low:
            reply.failures.append(f"card heading or label: «{label}»")
    final = ob.s("goal_final_question", lang)
    if reply.stage != "weekly" and msg.endswith(final):
        lines = [line for line in msg.splitlines() if line.strip()]
        if len(lines) < MIN_CARD_LINES:
            reply.failures.append(
                f"card has {len(lines)} line(s), expected at least {MIN_CARD_LINES}"
            )
    if reply.stage in ("goal", "weekly") and reply.flags.get("ready") and not confirm_turn:
        reply.failures.append("ready=true before a confirmation turn")
    if lang == "en" and has_cyrillic(msg):
        reply.failures.append("Cyrillic in an English run")
    if lang == "ru":
        masc = MASCULINE_RU.search(msg)
        if masc:
            reply.failures.append(f"masculine form: «{masc.group(1)}»")


async def run_scenario(
    sid: str, lang: str, lines: list[str], models: list[str]
) -> tuple[list[BotReply], list[str], list[str]]:
    st = {"why_turns": [], "goal_turns": [], "weekly_turns": [], "vision": "", "main_goal": ""}
    pending = list(lines)
    backups = list(BACKUPS.get(sid, []))
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
            ob.start_goal_dialog_after_dig(st, st["why_turns"])
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
        elif has_card or drafted:
            user_text = YES[lang]
        elif "?" in message and backups:
            user_text = backups.pop(0)
        else:
            end_note = "script ended; the bot is waiting for her answer"
            break
        confirm_turn = has_card and user_text.strip(" .!").lower() in YES_WORDS
    else:
        end_note = end_note or f"stopped at {MAX_BOT_REPLIES} bot replies"

    info = [end_note] if end_note else []
    return replies, info, _scenario_failures(sid, lang, lines, replies)


def _scenario_failures(
    sid: str, lang: str, lines: list[str], replies: list[BotReply]
) -> list[str]:
    failures: list[str] = []
    final = ob.s("goal_final_question", lang)
    if sid.startswith("6"):
        card_at = next(
            (i for i, r in enumerate(replies, 1) if r.stage != "weekly" and final in r.message),
            None,
        )
        if card_at is None or card_at > 2:
            failures.append(f"goal card not reached by bot reply 2 (got: {card_at or 'never'})")
    if sid.startswith("10") and replies:
        first = replies[0]
        if first.flags.get("ready_for_goal"):
            failures.append("reply #1: ready_for_goal=True on an irritated message")
        said = lines[0].lower()
        invented = [w for w in INVENTED_STEMS if w in first.message.lower() and w not in said]
        if invented:
            failures.append(f"reply #1: words she never used: {', '.join(invented)}")
    if sid == "9":
        for i, r in enumerate(replies, 1):
            if CLASSIFYING_START.search(r.message):
                failures.append(f"reply #{i}: starts by classifying her message")
    return failures


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
        replies, info, scenario_failures = await run_scenario(sid, lang, lines, models)
        in1, out1 = _total_tokens()
        _print_dialog(sid, lang, situation, replies)
        failures = [f"reply #{i}: {f}" for i, r in enumerate(replies, 1) for f in r.failures]
        failures += scenario_failures
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
    parser.add_argument("--out", default="", help="also write the whole output to this UTF-8 file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out_file = None
    if args.out:
        out_file = open(args.out, "w", encoding="utf-8")
        sys.stdout = _Tee(sys.stdout, out_file)
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        return asyncio.run(main_async(args))
    finally:
        if out_file:
            sys.stdout = sys.__stdout__
            out_file.close()
            print(f"output written to {Path(args.out).resolve()}")


class _Tee:
    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


if __name__ == "__main__":
    sys.exit(main())
