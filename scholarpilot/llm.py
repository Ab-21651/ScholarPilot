"""Small helper for one-off LLM calls that return JSON (e.g. summarizing a chat).

Kept separate from the agent loop so the model provider can be swapped in one place.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import settings

log = logging.getLogger(__name__)


def llm_available() -> bool:
    return bool(settings.anthropic_api_key)


def _parse_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def complete_json(system: str, prompt: str, max_tokens: int = 1500) -> dict[str, Any] | None:
    """Ask the model for a JSON object. Returns None if no model is configured or the call fails."""
    if not llm_available():
        return None
    import anthropic

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.AnthropicError, TypeError):
        log.exception("LLM JSON call failed")
        return None
    if response.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in response.content if b.type == "text")
    return _parse_json(text)
