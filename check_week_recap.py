"""Day-7 evening: week recap letter + the new-week goal opening, on test data.

Runs _generate_weekly_recap_message from main.py with the week's notes stubbed in
(no database), then prints the two messages that follow it
(new_week_opening + weekly_dialog_opening). Paid model calls.

Run from spicespace-bot:
    .\\.venv\\Scripts\\python.exe check_week_recap.py --out week_recap.txt
"""

from __future__ import annotations

import argparse
import asyncio
import ast
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import check_chat_ru as base  # noqa: E402
import onboarding_flow as ob  # noqa: E402
from claude_budget import usage_report  # noqa: E402
from claude_client import build_model_chain, generate as claude_generate, select_model_id  # noqa: E402
from lang_context import has_cyrillic  # noqa: E402

RU = {
    "language_code": "ru", "name": "Аня",
    "vision": "Своё дело и свои деньги, свобода решать самой",
    "main_goal": "К 12-й неделе запустить свой продукт и получить первые оплаты",
    "weekly_goal": "Оформить соцсети для продвижения бота: профили заполнены и готовы принимать людей",
}
EN = {
    "language_code": "en", "name": "Kate",
    "vision": "My own app with paying users",
    "main_goal": "Launch a habit-tracker app and get the first 100 users in 12 weeks",
    "weekly_goal": "Finish the onboarding screens",
}

GOOD_WEEK_RU = """2026-11-05: задача дня: Выбрать 2–3 соцсети для бота; задача дня: выполнена
2026-11-06: задача дня: Написать описание бота для профиля; задача дня: выполнена
2026-11-07: задача дня: Собрать аватарку и обложку в Canva; задача дня: выполнена; настроение: бодрое
2026-11-08: задача дня: Заполнить профиль в Instagram; задача дня: выполнена
2026-11-09: задача дня: Заполнить профиль в Threads; задача дня: не выполнена; деталь: дети заболели
2026-11-10: задача дня: Первый пост о боте; задача дня: выполнена"""
BAD_WEEK_RU = """2026-11-05: Говорила, что сил нет; задача дня: Выбрать соцсети; задача дня: не выполнена; настроение: без сил
2026-11-06: Разговор о том, что живёт не свою жизнь, деньги просит у мужа; настроение: тяжело
2026-11-08: задача дня: Собрать маркетинговую стратегию в Claude; задача дня: выполнена частично
2026-11-10: Плохо ест, нет аппетита; настроение: устала"""
GOOD_WEEK_EN = """2026-11-05: task of the day: Sketch welcome screen; task of the day: done
2026-11-06: task of the day: Sketch goal-picking screen; task of the day: done
2026-11-07: task of the day: Build welcome screen in Figma; task of the day: not done; detail: overtime at work
2026-11-08: task of the day: Build goal-picking screen; task of the day: done
2026-11-09: task of the day: Test screens with a friend; task of the day: done"""

CASES = [
    ("RU хорошая неделя (5 из 6)", RU, GOOD_WEEK_RU, 5, 6),
    ("RU тяжёлая неделя (мало сделано, нет сил)", RU, BAD_WEEK_RU, 0, 4),
    ("RU почти нет записей", RU, "", 0, 0),
    ("EN good week (4 of 5)", EN, GOOD_WEEK_EN, 4, 5),
]


def _load(week_text: str, done: int, notes: int) -> dict:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
             and n.name == "_generate_weekly_recap_message"]
    ns = base._load_from_main()
    ns.update({
        "asyncio": asyncio,
        "log": logging.getLogger("recap"),
        "claude_generate": claude_generate,
        "_collect_week_recap_facts": lambda cid, p, d: (week_text, done, notes),
    })
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    return ns


def check(text: str, lang: str) -> list[str]:
    p = []
    if "?" in text:
        p.append("question in recap")
    if re.search(r"\b(перв|втор|трет|\d+-?я)\w*\s+недел", text, re.IGNORECASE):
        p.append("week number")
    if re.search(r"воскресень|понедельник|sunday|monday", text, re.IGNORECASE):
        p.append("weekday named")
    if re.search(r"завтра утром|tomorrow morning", text, re.IGNORECASE):
        p.append("'tomorrow morning' (goal talk starts right now)")
    if lang == "en" and has_cyrillic(text):
        p.append("cyrillic in English")
    sentences = len(re.findall(r"[.!…]+(\s|$)", text))
    if sentences > 9:
        p.append(f"long: ~{sentences} sentences")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    if out:
        sys.stdout = base._Tee(sys.stdout, out)
    model = build_model_chain(select_model_id())[0]
    print(f"model: {model}\n")
    for title, prof, week, done, notes in CASES:
        ns = _load(week, done, notes)
        lang = prof["language_code"]
        recap = asyncio.run(ns["_generate_weekly_recap_message"](
            0, prof, [model], 1, days_since_start=6))
        print(f"=== {title}")
        print("[итог недели]")
        print(recap)
        for p in check(recap, lang):
            print(f"   ! {p}")
        print("\n[следом]")
        print(ob.new_week_opening(prof, lang))
        print("---")
        print(ob.ob_text("weekly_dialog_opening", lang))
        print()
    print(usage_report())
    if out:
        sys.stdout = sys.__stdout__
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
