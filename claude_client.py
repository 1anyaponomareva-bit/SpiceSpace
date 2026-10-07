"""Anthropic Claude client with prompt caching."""

from __future__ import annotations

import inspect
import logging
import os

import anthropic

from claude_budget import DYNAMIC_MARKER, is_billing_error, record_usage, usage_report

log = logging.getLogger("coach_bot")


class ClaudeBillingError(RuntimeError):
    """Anthropic rejected the call because the credit balance is too low."""


_billing_stopped = False

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError("В .env нужен ANTHROPIC_API_KEY")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def configure() -> None:
    get_client()


def select_model_id() -> str:
    preferred = os.getenv("CLAUDE_MODEL", "").strip()
    if preferred:
        return preferred
    return "claude-sonnet-4-5"


SCHEDULE_MODEL = "claude-haiku-4-5"


def schedule_model_chain(model_names: list[str] | None = None) -> list[str]:
    """Cheap model first for morning, evening, summary, and re-engagement."""
    names: list[str] = []
    seen: set[str] = set()
    for mid in [SCHEDULE_MODEL, *(model_names or [])]:
        if mid and mid not in seen:
            names.append(mid)
            seen.add(mid)
    return names


def build_model_chain(primary: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for part in [primary] + [
        x.strip() for x in os.getenv("CLAUDE_FALLBACK_MODELS", "").split(",") if x.strip()
    ]:
        if part not in seen:
            names.append(part)
            seen.add(part)
    for mid in (
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ):
        if mid not in seen:
            names.append(mid)
            seen.add(mid)
    return names


def response_text(response: object) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    text = "".join(parts).strip()
    if text:
        return text
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        return (
            "Не смогла ответить на эту формулировку. "
            "Переформулируй короче — продолжим."
        )
    return "Напиши ещё раз — я слушаю."


def estimate_tokens(text: str) -> int:
    """Local Claude-like estimate: ~4 Latin chars or ~2 non-Latin chars per token."""
    if not text:
        return 0
    ascii_n = 0
    other = 0
    for ch in text:
        if ord(ch) < 128:
            ascii_n += 1
        else:
            other += 1
    return (ascii_n + 3) // 4 + (other + 1) // 2


def _caller_label() -> str:
    try:
        for frame in inspect.stack()[1:]:
            if os.path.basename(frame.filename) == "claude_client.py":
                continue
            return f"{os.path.basename(frame.filename)}:{frame.function}:{frame.lineno}"
    except Exception:
        return "unknown"
    return "unknown"


def _system_blocks(system: str, *, cache_core: bool) -> list[dict] | str:
    if not system:
        return ""
    if not cache_core:
        return system
    if DYNAMIC_MARKER in system:
        static, dynamic = system.split(DYNAMIC_MARKER, 1)
        blocks: list[dict] = []
        if static.strip():
            blocks.append(
                {
                    "type": "text",
                    "text": static,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if dynamic.strip():
            blocks.append({"type": "text", "text": dynamic.strip()})
        return blocks or ""
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def generate(
    model_id: str,
    messages: list[dict],
    *,
    system: str = "",
    max_tokens: int = 1024,
    cache_core: bool = True,
    feature: str = "unspecified",
    user_id: str | int = "",
) -> str:
    global _billing_stopped
    if _billing_stopped:
        raise ClaudeBillingError("credit balance too low — further Claude calls are paused")
    client = get_client()
    kwargs: dict = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = _system_blocks(system, cache_core=cache_core)
    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:
        if is_billing_error(exc):
            _billing_stopped = True
            log.error("claude billing stopped feature=%s: %s", feature, exc)
            raise ClaudeBillingError(str(exc)) from exc
        raise
    usage = getattr(response, "usage", None)
    read = created = input_tokens = output_tokens = 0
    if usage:
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        created = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    system_tokens = estimate_tokens(system)
    caller = _caller_label()
    system_head = " ".join((system or "").split())[:80]
    if read > 0:
        cache_status = "hit"
    elif created > 0:
        cache_status = "write"
    else:
        cache_status = "miss"
    record_usage(
        feature,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=created,
    )
    log.info(
        "claude_usage user=%s feature=%s caller=%s model=%s system_tokens=%s "
        "cache_core=%s cache_read_input_tokens=%s cache_creation_input_tokens=%s "
        "input_tokens=%s output_tokens=%s cache=%s system_head=%s",
        user_id,
        feature,
        caller,
        model_id,
        system_tokens,
        cache_core,
        read,
        created,
        input_tokens,
        output_tokens,
        cache_status,
        system_head,
    )
    log.info(usage_report())
    if read == 0:
        log.info(
            "claude cache_read_input_tokens=0 caller=%s model=%s cache=%s "
            "— кэш на этом запросе не прочитан",
            caller,
            model_id,
            cache_status,
        )
    return response_text(response)
