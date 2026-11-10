"""Run 10 'low state' dialogs through the live-chat prompt with the support-mode rule.

Builds the system prompt like _coach_reply for a Russian profile with a task today,
adds the support-mode rule when _in_support_mode says so (taken from main.py), and
calls Claude. Nothing is read from or written to the database. Crisis messages get the
vetted safety reply, as in the bot.

Flags per reply: work talk she did not ask for (task, goal, product, strategy...),
more than one "?", an "или" choice in the question, bookish phrases, diagnoses,
the doctor advice repeated within one dialog.

Run from spicespace-bot (paid model calls):
    .\\.venv\\Scripts\\python.exe check_support_mode.py --out support_mode.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import check_chat_ru as base  # noqa: E402
import safety  # noqa: E402
from claude_budget import parse_structured_reply, recent_history, usage_report  # noqa: E402
from claude_client import build_model_chain, generate as claude_generate, select_model_id  # noqa: E402
from lang_context import coach_style_for, facts_header  # noqa: E402
from prompts import (  # noqa: E402
    build_chat_system,
    refresh_user_time_in_system,
    user_message_with_fresh_time,
)

PROFILE = {
    "user_id": "0",
    "language_code": "ru",
    "name": "Аня",
    "vision": "Своё дело и свои деньги, свобода решать самой",
    "main_goal": "К 12-й неделе запустить свой продукт и получить первые оплаты",
    "weekly_goal": "Оформить соцсети для продвижения бота",
    "current_week": 2,
    "morning_time": "08:00",
    "evening_time": "21:00",
    "timezone": "Asia/Bangkok",
}
YESTERDAY = {"summary": "Доделывала бота, вечером сил не было.", "key_detail": "Устаёт к вечеру"}
TODAY = {
    "summary": "Утром выбрала задачу на день.",
    "task": "Собрать маркетинговую стратегию в Claude",
    "task_completed": "",
}
FACTS = [
    "Переехала в другую страну с семьёй",
    "Двое детей",
    "Раньше была своя школа и объекты, теперь помогает в бизнесе мужа",
]

DIALOGS: list[tuple[str, list[str]]] = [
    ("нет сил + твоя задача", ["Короче сил нет вообще никаких", "Твоя задача сейчас какая?"]),
    ("не своя жизнь", ["Ощущение что живу не свою жизнь", "Не знаю, всё как будто чужое"]),
    ("деньги у мужа", ["Опять пришлось просить у мужа деньги, подъедает это", "Да и что тут поменяешь"]),
    ("аппетит", ["Просыпаюсь а энергии нет", "Я вообще плохо ем, аппетита нет", "Ну чай пью с печеньем"]),
    ("дети и выгорание", ["Дети весь день орали, я выгорела", "Хочу просто лечь и чтобы никто не трогал"]),
    ("я ленивая", ["Ничего не хочется, наверное я просто ленивая", "Все вокруг что-то делают, а я лежу"]),
    ("зачем всё это", ["Устала. Зачем мне вообще этот бот", "Может бросить всё"]),
    ("вернуться к делам", ["Сил нет совсем", "Ладно, какая у меня задача на сегодня?"]),
    ("плачу", ["Сижу плачу и не знаю почему", "Просто навалилось"]),
    ("одиноко", ["Одиноко тут, поговорить не с кем", "Муж на работе, подруги далеко", "Спасибо 🙏"]),
]

WORK = re.compile(
    r"задач|стратег|\bцел[ьиюей]|продукт|соцсет|напомин|claude|canva|бот[ауеом]?\b|план на завтра",
    re.IGNORECASE,
)
BOOKISH = re.compile(
    r"в самом простом смысле|в моменте|на уровне ощущений|в теле|это горе|проживаешь потерю|"
    r"у тебя депресс|выгорание — это|не еда|серьёзно беспоко|психиатр|психотерапевт|в безопасности",
    re.IGNORECASE,
)
DOCTOR = re.compile(r"врач|терапевт|анализ", re.IGNORECASE)


def _load_main() -> dict:
    names = set(base._MAIN_NAMES) | {
        "_LOW_STATE_MARKERS", "_text_of_turn", "_in_support_mode",
        "SUPPORT_MODE_RULE_RU", "SUPPORT_MODE_RULE_EN", "_support_mode_rule",
        "_SENTENCE_RE", "_LEAD_CONJ_RE", "_keep_one_question", "_strip_leading_name",
    }
    base._MAIN_NAMES = names
    ns = base._load_from_main()
    ns["safety"] = safety
    return ns


def build_system(ns: dict, hist: list[dict], user_text: str) -> tuple[str, bool]:
    extra = [ns["_current_goal_only_rule"](PROFILE), ns["_chat_one_question_rule"](PROFILE)]
    support = ns["_in_support_mode"](hist, user_text)
    if support:
        extra.append(ns["_support_mode_rule"](PROFILE))
    extra.append(f"{facts_header('ru')}\n" + "\n".join(f"— {f}" for f in FACTS))
    system = build_chat_system(
        PROFILE, YESTERDAY, TODAY, extra="\n\n".join(extra),
        coach_style=coach_style_for("ru", ns["COACH_STYLE_INSTRUCTION"]),
    )
    return system, support


def ask(ns: dict, model: str, hist: list[dict], text: str) -> tuple[str, bool]:
    if safety.detect_crisis(text):
        return safety.crisis_reply("ru"), True
    system, support = build_system(ns, hist, text)
    system = refresh_user_time_in_system(PROFILE, system)
    messages = ns["_hist_to_claude_messages"](
        recent_history(hist), user_message_with_fresh_time(PROFILE, text)
    )
    raw = claude_generate(model, messages, system=system, feature="chat",
                          user_id="support-check", lang="ru")
    parsed = parse_structured_reply(raw)
    reply = ns["sanitize_bot_reply"]((parsed.get("reply") or raw).strip())
    if support:
        reply = ns["_keep_one_question"](ns["_strip_leading_name"](reply, PROFILE["name"]))
    return reply, support


def check(reply: str, user_text: str, doctor_seen: bool) -> list[str]:
    problems = []
    asked_work = bool(re.search(r"задач|работ|бот|стратег", user_text, re.IGNORECASE))
    if not asked_work and WORK.search(reply):
        problems.append("work talk: " + ", ".join(sorted(set(m.group(0) for m in WORK.finditer(reply)))))
    if reply.count("?") > 1:
        problems.append(f"questions: {reply.count('?')}")
    last_q = reply.rsplit(".", 1)[-1] if "?" in reply else ""
    if re.search(r"\bили\b", last_q):
        problems.append("'или' choice in question")
    for m in BOOKISH.finditer(reply):
        problems.append(f"bookish/diagnosis: {m.group(0)}")
    if doctor_seen and DOCTOR.search(reply):
        problems.append("doctor repeated")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    if out:
        sys.stdout = base._Tee(sys.stdout, out)
    ns = _load_main()
    model = build_model_chain(select_model_id())[0]
    print(f"model: {model}\n")
    flagged = 0
    total = 0
    for n, (title, turns) in enumerate(DIALOGS, 1):
        print(f"=== {n}. {title}")
        hist: list[dict] = []
        doctor_seen = False
        for text in turns:
            reply, support = ask(ns, model, hist, text)
            problems = check(reply, text, doctor_seen)
            doctor_seen = doctor_seen or bool(DOCTOR.search(reply))
            total += 1
            flagged += bool(problems)
            print(f"> {text}   [{'поддержка' if support else 'обычный'}]")
            print(reply)
            for p in problems:
                print(f"   ! {p}")
            print()
            hist += [{"role": "user", "parts": [text]}, {"role": "model", "parts": [reply]}]
    print("=" * 60)
    print(f"Replies flagged: {flagged} of {total}")
    print(usage_report())
    if out:
        sys.stdout = sys.__stdout__
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
