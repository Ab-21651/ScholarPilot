"""The agent loop: the model plans, calls tools, reads the results, and repeats until it can answer."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Conversation, User
from .prompts import SYSTEM_PROMPT, user_context
from .tools import TOOLS, ToolContext, execute

log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    return _client


@dataclass
class AgentReply:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    created_drafts: list[int] = field(default_factory=list)
    stopped_reason: str = "end_turn"


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True)


def _short(result_json: str, limit: int = 400) -> str:
    return result_json if len(result_json) <= limit else result_json[:limit] + "..."


def _call_model(client: anthropic.Anthropic, system: list[dict[str, Any]], messages: list[dict[str, Any]]):
    return client.beta.messages.create(
        model=settings.model,
        max_tokens=16000,
        system=system,
        tools=TOOLS,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.effort},
        cache_control={"type": "ephemeral"},
        # If the main model declines, the API retries on a suitable fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )


def run_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    client: anthropic.Anthropic | None = None,
) -> AgentReply:
    try:
        client = client or get_client()
    except anthropic.AnthropicError:
        log.exception("Could not create the Anthropic client")
        return AgentReply(text="The assistant isn't configured yet. Add ANTHROPIC_API_KEY to the .env file and restart the server.", stopped_reason="error")
    ctx = ToolContext(db, user)
    system = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": user_context(user.full_name, user.target_level, user.role)},
    ]
    messages: list[dict[str, Any]] = list(conversation.messages or [])
    messages.append({"role": "user", "content": user_message})
    reply = AgentReply(text="")

    try:
        for _ in range(settings.max_agent_steps):
            response = _call_model(client, system, messages)
            content = [_block_to_dict(b) for b in response.content]
            reply.stopped_reason = response.stop_reason or "end_turn"

            if response.stop_reason == "refusal":
                reply.text = "Sorry, I can't help with that request. I can help you find and apply for scholarships."
                # Drop the refused turn entirely so the saved history stays valid.
                messages = list(conversation.messages or [])
                break

            messages.append({"role": "assistant", "content": content})

            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                reply.text = "\n\n".join(b["text"] for b in content if b.get("type") == "text").strip()
                if response.stop_reason == "max_tokens":
                    reply.text += "\n\n(My answer was cut short. Ask me to continue.)"
                break

            results = []
            for tu in tool_uses:
                output, is_error = execute(ctx, tu["name"], tu.get("input") or {})
                reply.tool_calls.append({"tool": tu["name"], "input": tu.get("input"), "is_error": is_error, "result": _short(output)})
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        else:
            reply.text = "I took too many steps on this one. Could you ask again, a bit more specifically?"
            reply.stopped_reason = "max_steps"
    except anthropic.AuthenticationError:
        log.exception("Anthropic authentication failed")
        reply.text = "The assistant isn't configured yet (missing or invalid ANTHROPIC_API_KEY)."
        reply.stopped_reason = "error"
        return reply
    except anthropic.RateLimitError:
        reply.text = "The assistant is busy right now. Please try again in a minute."
        reply.stopped_reason = "error"
        return reply
    except anthropic.APIStatusError as e:
        log.exception("Anthropic API error")
        reply.text = f"The assistant hit an error ({e.status_code}). Please try again."
        reply.stopped_reason = "error"
        return reply
    except anthropic.APIConnectionError:
        reply.text = "I couldn't reach the AI service. Check the internet connection and try again."
        reply.stopped_reason = "error"
        return reply
    except anthropic.AnthropicError:
        log.exception("Anthropic client error")
        reply.text = "The assistant isn't configured correctly. Check ANTHROPIC_API_KEY in the .env file."
        reply.stopped_reason = "error"
        return reply
    except TypeError as e:
        # The SDK raises TypeError when no credentials are configured at all.
        if "authentication" not in str(e).lower():
            raise
        reply.text = "The assistant isn't configured yet. Add ANTHROPIC_API_KEY to the .env file and restart the server."
        reply.stopped_reason = "error"
        return reply

    # The history must end with a finished assistant turn so the next request is valid.
    if messages and messages[-1]["role"] == "user" and isinstance(messages[-1]["content"], list):
        messages.append({"role": "assistant", "content": [{"type": "text", "text": reply.text}]})

    conversation.messages = messages
    if conversation.title == "New chat":
        conversation.title = user_message[:60]
    reply.created_drafts = ctx.created_drafts
    db.commit()
    return reply


def visible_history(conversation: Conversation) -> list[dict[str, Any]]:
    """Messages the UI shows: user text and assistant text only."""
    out = []
    for m in conversation.messages or []:
        if isinstance(m["content"], str):
            out.append({"role": m["role"], "text": m["content"]})
            continue
        texts = [b.get("text", "") for b in m["content"] if b.get("type") == "text"]
        tools = [b["name"] for b in m["content"] if b.get("type") == "tool_use"]
        if m["role"] == "assistant" and (texts or tools):
            out.append({"role": "assistant", "text": "\n\n".join(t for t in texts if t).strip(), "tools": tools})
    merged: list[dict[str, Any]] = []
    for m in out:
        if merged and merged[-1]["role"] == "assistant" and m["role"] == "assistant":
            merged[-1]["text"] = "\n\n".join(t for t in (merged[-1]["text"], m["text"]) if t)
            merged[-1]["tools"] = merged[-1].get("tools", []) + m.get("tools", [])
        else:
            merged.append(m)
    return [m for m in merged if m["text"] or m["role"] == "user"]


__all__ = ["run_agent", "visible_history", "AgentReply"]
