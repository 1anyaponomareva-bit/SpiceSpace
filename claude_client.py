"""Anthropic Claude client with prompt caching."""

from __future__ import annotations

import logging
import os

import anthropic

from prompts import SPICESPACE_CORE_SYSTEM

log = logging.getLogger("coach_bot")

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


def _system_blocks(system: str, *, cache_core: bool) -> list[dict] | str:
    if not system:
        return ""
    if not cache_core or not system.startswith(SPICESPACE_CORE_SYSTEM):
        return system
    suffix = system[len(SPICESPACE_CORE_SYSTEM) :].lstrip("\n")
    blocks: list[dict] = [
        {
            "type": "text",
            "text": SPICESPACE_CORE_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if suffix:
        blocks.append({"type": "text", "text": suffix})
    return blocks


def generate(
    model_id: str,
    messages: list[dict],
    *,
    system: str = "",
    max_tokens: int = 1024,
    cache_core: bool = True,
) -> str:
    client = get_client()
    kwargs: dict = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = _system_blocks(system, cache_core=cache_core)
    response = client.messages.create(**kwargs)
    usage = getattr(response, "usage", None)
    if usage:
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if read or created:
            log.debug("prompt cache read=%s created=%s", read, created)
    return response_text(response)
