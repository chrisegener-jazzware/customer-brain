"""Tiny shared Claude wrapper for sales artifacts.

Reuses the same client construction as RollupService so we honor the
ANTHROPIC_API_KEY env var, model selection, and the token-tracker wrapper.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("sales.claude")


@dataclass
class ClaudeResponse:
    text: str
    model: str
    is_fallback: bool = False


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.startswith("***"):
        return None
    try:
        import anthropic  # type: ignore
        try:
            from _token_tracker import track as _tt_track
            return _tt_track(
                anthropic.Anthropic(api_key=key),
                project="customer-brain",
            )
        except Exception:  # noqa: BLE001
            return anthropic.Anthropic(api_key=key)
    except Exception as e:  # noqa: BLE001
        log.warning("anthropic client unavailable: %s", e)
        return None


def call(
    system: str,
    user_msg: str,
    *,
    max_tokens: int = 1500,
    fallback: str = "AI unavailable — Claude API key not set or API error.",
) -> ClaudeResponse:
    """Call Claude with a system prompt and a single user message."""
    client = _get_client()
    if client is None:
        return ClaudeResponse(text=fallback, model="heuristic-fallback", is_fallback=True)
    try:
        resp = client.messages.create(
            model=_model(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text  # type: ignore[attr-defined]
        return ClaudeResponse(text=text, model=_model())
    except Exception as e:  # noqa: BLE001
        log.exception("Claude call failed: %s", e)
        return ClaudeResponse(text=fallback + f"\n\n(error: {e})", model="error", is_fallback=True)


def render_payload(payload: dict[str, Any]) -> str:
    """Compact JSON for embedding in user messages."""
    return json.dumps(payload, indent=2, default=str)
