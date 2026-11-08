"""Model provider layer.

ScholarPilot talks to any OpenAI-compatible chat API. The default is Groq
running openai/gpt-oss-120b. Changing LLM_BASE_URL, LLM_API_KEY and LLM_MODEL
points it at another provider without code changes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import openai

from .config import settings

log = logging.getLogger(__name__)

_client: openai.OpenAI | None = None


class LLMNotConfigured(RuntimeError):
    pass


def llm_available() -> bool:
    return bool(settings.llm_api_key)


def get_client() -> openai.OpenAI:
    global _client
    if not settings.llm_api_key:
        raise LLMNotConfigured("LLM_API_KEY (or GROQ_API_KEY) is not set")
    if _client is None:
        _client = openai.OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, max_retries=2, timeout=90)
    return _client


def chat(client: openai.OpenAI, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kwargs: Any):
    """One chat completion call with ScholarPilot's defaults."""
    params: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "max_completion_tokens": kwargs.pop("max_completion_tokens", settings.max_output_tokens),
        "reasoning_effort": kwargs.pop("reasoning_effort", settings.reasoning_effort),
        # GPT-OSS returns its reasoning separately; we don't show or store it.
        "extra_body": {"include_reasoning": False},
    }
    if tools:
        params["tools"] = tools
        params["tool_choice"] = "auto"
    params.update(kwargs)
    return client.chat.completions.create(**params)


def _parse_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def complete_json(system: str, prompt: str, max_tokens: int = 1500, reasoning_effort: str = "low") -> dict[str, Any] | None:
    """Ask the model for a JSON object. Returns None if no model is configured or the call fails."""
    try:
        client = get_client()
        response = chat(
            client,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_completion_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            response_format={"type": "json_object"},
        )
    except (LLMNotConfigured, openai.OpenAIError):
        log.exception("LLM JSON call failed")
        return None
    choice = response.choices[0]
    if choice.finish_reason == "content_filter":
        return None
    return _parse_json(choice.message.content or "")
