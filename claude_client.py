"""Anthropic Claude client with prompt caching."""

from __future__ import annotations

import inspect
import logging
import os
import threading
import time

import anthropic

import spend_budget

from claude_budget import (
    DYNAMIC_MARKER,
    SHARED_MARKER,
    is_billing_error,
    record_usage,
    usage_cost_usd,
    usage_report,
)
from lang_context import empty_fallback, refusal_fallback

log = logging.getLogger("coach_bot")


def _save_usage_row(row: dict) -> None:
    import db as db_store

    db_store.save_claude_usage(row)


def persist_usage(row: dict) -> None:
    """Write the usage row off the reply path; tests replace this function."""
    threading.Thread(target=_save_usage_row, args=(row,), daemon=True).start()


class ClaudeBillingError(RuntimeError):
    """Anthropic rejected the call because the credit balance is too low."""


class ClaudeEmptyReply(RuntimeError):
    """The model returned no text or refused; strict callers send their own template."""


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


CHAT_MODEL = "claude-sonnet-5-5"
SCHEDULE_MODEL = "claude-haiku-5-5"
# Older generation kept as a last-resort fallback only.
LEGACY_FALLBACKS = ("claude-sonnet-4-5", "claude-haiku-4-5")


def select_model_id() -> str:
    preferred = os.getenv("CLAUDE_MODEL", "").strip()
    if preferred:
        return preferred
    return CHAT_MODEL


def is_new_generation(model_id: str) -> bool:
    """Claude 5 family: no assistant prefill, no temperature, thinking/effort controls."""
    mid = str(model_id or "")
    return mid.startswith(("claude-sonnet-5", "claude-haiku-5", "claude-opus-5", "claude-fable"))


def supports_prefill(model_id: str) -> bool:
    return not is_new_generation(model_id)


def model_request_options(model_id: str) -> dict:
    """Per-model request fields.

    Replies here are short chat messages, often with small max_tokens (12-400), so
    extended thinking stays off: it would spend those tokens before any text appears.
    Effort (CLAUDE_EFFORT, default low) keeps answers short and cheap.
    """
    mid = str(model_id or "")
    effort = os.getenv("CLAUDE_EFFORT", "").strip() or "low"
    if mid.startswith("claude-sonnet-5-5"):
        return {"thinking": {"type": "between_tools"}, "output_config": {"effort": effort}}
    if mid.startswith("claude-haiku-5-5"):
        return {"thinking": {"type": "disabled"}, "output_config": {"effort": effort}}
    return {}


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
    for mid in (CHAT_MODEL, *LEGACY_FALLBACKS):
        if mid not in seen:
            names.append(mid)
            seen.add(mid)
    return names


def response_text(response: object, lang: str = "ru", *, strict: bool = False) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    text = "".join(parts).strip()
    if text:
        return text
    stop = getattr(response, "stop_reason", None)
    if strict:
        raise ClaudeEmptyReply(f"empty model reply stop_reason={stop}")
    if stop == "refusal":
        return refusal_fallback(lang)
    return empty_fallback(lang)


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


def long_cache(feature: str) -> bool:
    """Frequent prompts shared by everyone keep their common part for an hour: with the
    5-minute TTL it was paid for again ~100 times in two days (23.11.2026)."""
    return feature in ("chat", "photo") or str(feature).startswith("onb_")


def _system_blocks(
    system: str, *, cache_core: bool, cache_dynamic: bool = False, hour: bool = False
) -> list[dict] | str:
    """Up to 3 blocks: common rules, a shared piece (SHARED_MARKER, on either side of
    DYNAMIC_MARKER), the rest. hour: the common pieces get the 1-hour TTL (they come
    before any 5-minute block, as the API requires)."""
    if not system:
        return ""
    if not cache_core:
        # never send the marker itself to the model
        return system.replace(DYNAMIC_MARKER, "\n\n").replace(SHARED_MARKER, "\n\n")
    if DYNAMIC_MARKER in system:
        shared_cc = {"type": "ephemeral", "ttl": "1h"} if hour else {"type": "ephemeral"}
        static, dynamic = system.split(DYNAMIC_MARKER, 1)
        shared = [p for p in static.split(SHARED_MARKER) if p.strip()]
        step_shared = SHARED_MARKER in dynamic
        if step_shared:
            head, dynamic = dynamic.split(SHARED_MARKER, 1)
            dynamic = dynamic.replace(SHARED_MARKER, "\n\n")
            if head.strip():
                shared.append(head.strip())
        blocks: list[dict] = [
            {"type": "text", "text": p, "cache_control": dict(shared_cc)} for p in shared
        ]
        if dynamic.strip():
            block: dict = {"type": "text", "text": dynamic.strip()}
            if cache_dynamic:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks or ""
    system = system.replace(SHARED_MARKER, "\n\n")
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def with_history_cache(messages: list[dict]) -> list[dict]:
    """Mark the last turn before the new message as a cache breakpoint."""
    if len(messages) < 2:
        return messages
    out = [dict(m) for m in messages]
    target = out[-2]
    content = target.get("content")
    if isinstance(content, str):
        if not content:
            return messages
        target["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        blocks = [dict(b) for b in content]
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        target["content"] = blocks
    return out


def generate(
    model_id: str,
    messages: list[dict],
    *,
    system: str = "",
    max_tokens: int = 1024,
    cache_core: bool = True,
    feature: str = "unspecified",
    user_id: str | int = "",
    lang: str = "ru",
    strict: bool = False,
    cache_history: bool = False,
    cache_dynamic: bool | None = None,
) -> str:
    """cache_dynamic: also cache the part of the system prompt after DYNAMIC_MARKER
    (defaults to cache_history); for prompts whose whole system is the same for everyone.
    cache_history: also cache the per-user part of the system prompt and the
    conversation up to the new message (chat), so a run of messages reads them
    from cache instead of paying for them again each time."""
    global _billing_stopped
    if _billing_stopped:
        raise ClaudeBillingError("credit balance too low — further Claude calls are paused")
    client = get_client()
    kwargs: dict = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": with_history_cache(messages) if cache_history else messages,
        **model_request_options(model_id),
    }
    if system:
        kwargs["system"] = _system_blocks(
            system,
            cache_core=cache_core,
            cache_dynamic=cache_history if cache_dynamic is None else cache_dynamic,
            hour=long_cache(feature),
        )
    t0 = time.perf_counter()
    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:
        log.info("claude_time feature=%s model=%s s=%.1f failed=%s", feature, model_id,
                 time.perf_counter() - t0, type(exc).__name__)
        if is_billing_error(exc):
            _billing_stopped = True
            log.error("claude billing stopped feature=%s: %s", feature, exc)
            raise ClaudeBillingError(str(exc)) from exc
        raise
    log.info("claude_time feature=%s model=%s s=%.1f", feature, model_id, time.perf_counter() - t0)
    usage = getattr(response, "usage", None)
    read = created = created_1h = input_tokens = output_tokens = 0
    if usage:
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        created = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        by_ttl = getattr(usage, "cache_creation", None)
        created_1h = getattr(by_ttl, "ephemeral_1h_input_tokens", 0) if by_ttl is not None else 0
        created_1h = created_1h if isinstance(created_1h, int) else 0
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
    served_model = str(getattr(response, "model", "") or model_id)
    cost = usage_cost_usd(
        served_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=created,
        cache_creation_1h_tokens=created_1h,
    )
    spend_budget.record(user_id, cost)
    persist_usage(
        {
            "user_id": user_id,
            "feature": feature,
            "model": served_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": read,
            "cache_write_tokens": created,
            "cost_usd": round(cost, 6),
        }
    )
    log.info(
        "claude_usage user=%s feature=%s caller=%s model=%s system_tokens=%s "
        "cache_core=%s cache_read_input_tokens=%s cache_creation_input_tokens=%s "
        "input_tokens=%s output_tokens=%s cache=%s cost_usd=%.5f system_head=%s",
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
        cost,
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
    return response_text(response, lang, strict=strict)
