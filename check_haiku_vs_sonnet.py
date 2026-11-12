"""Same chat dialogs through Haiku and Sonnet, side by side, with the cost of each.

The system prompt is built like the live chat (profile, facts, today's task, rules),
with the same caching as the bot. Nothing is read from or written to the database.

Run from spicespace-bot (paid model calls):
    .\\.venv\\Scripts\\python.exe check_haiku_vs_sonnet.py --out haiku_vs_sonnet.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import check_support_mode as sm  # noqa: E402
import claude_client  # noqa: E402
import safety  # noqa: E402
from claude_budget import parse_structured_reply, recent_history_stable  # noqa: E402
from lang_context import TIME_LINE_RE, time_label  # noqa: E402
from prompts import user_message_with_fresh_time  # noqa: E402

MODELS = [("Haiku", "claude-haiku-5-5"), ("Sonnet", "claude-sonnet-5-5")]

DIALOGS: list[tuple[str, list[str]]] = [
    ("Практика: посты", [
        "Хочу сегодня сделать посты для продвижения бота, но не знаю с чего начать",
        "У меня есть часа два",
        "А про что первый пост?",
    ]),
    ("Стратегия: никто не платит", [
        "10 человек зашли в бота, никто не оплатил. Что делать?",
        "Подписка 590 в месяц",
        "Может снизить цену?",
    ]),
    ("Усталость", [
        "Устала, дети весь день на мне",
        "Ничего не успела по цели сегодня",
    ]),
    ("Обычный разговор", [
        "Сегодня наконец выспалась",
        "Что мне сегодня важнее всего сделать?",
    ]),
]

_costs: list[float] = []
_orig_persist = claude_client.persist_usage


def _capture(row: dict) -> None:
    try:
        _costs.append(float(row.get("cost_usd") or 0))
    except (TypeError, ValueError):
        pass


claude_client.persist_usage = _capture


def _stable_time(system: str) -> str:
    pointer = f"{time_label('ru')}: в квадратных скобках в начале её последнего сообщения"
    return TIME_LINE_RE.sub(lambda _m: pointer, system, count=1)


def ask(ns: dict, model: str, hist: list[dict], text: str) -> tuple[str, float]:
    if safety.detect_crisis(text):
        return safety.crisis_reply("ru"), 0.0
    system, support = sm.build_system(ns, hist, text)
    system = _stable_time(system)
    messages = ns["_hist_to_claude_messages"](
        recent_history_stable(hist), user_message_with_fresh_time(sm.PROFILE, text)
    )
    before = len(_costs)
    raw = claude_client.generate(model, messages, system=system, feature="chat",
                                 user_id="model-compare", lang="ru", cache_history=True)
    cost = sum(_costs[before:])
    parsed = parse_structured_reply(raw)
    reply = ns["sanitize_bot_reply"]((parsed.get("reply") or raw).strip())
    if support:
        reply = ns["_keep_one_question"](ns["_strip_leading_name"](reply, sm.PROFILE["name"]))
    return reply, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ns = sm._load_main()
    results: dict[str, list[list[tuple[str, str, float]]]] = {}
    totals: dict[str, float] = {}
    for label, model in MODELS:
        results[label] = []
        totals[label] = 0.0
        for _title, turns in DIALOGS:
            hist: list[dict] = []
            rows = []
            for text in turns:
                reply, cost = ask(ns, model, hist, text)
                totals[label] += cost
                rows.append((text, reply, cost))
                hist += [{"role": "user", "parts": [text]}, {"role": "model", "parts": [reply]}]
            results[label].append(rows)

    lines: list[str] = []
    for d, (title, turns) in enumerate(DIALOGS):
        lines.append(f"=== {d + 1}. {title}")
        for t in range(len(turns)):
            lines.append(f"\n> Она: {turns[t]}")
            for label, _model in MODELS:
                _text, reply, cost = results[label][d][t]
                lines.append(f"\n[{label}, ${cost:.4f}]\n{reply}")
        lines.append("\n")
    n = sum(len(t) for _x, t in DIALOGS)
    lines.append("=" * 60)
    for label, _model in MODELS:
        lines.append(f"{label}: всего ${totals[label]:.4f} за {n} ответов, "
                     f"в среднем ${totals[label] / n:.4f} за ответ")
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
