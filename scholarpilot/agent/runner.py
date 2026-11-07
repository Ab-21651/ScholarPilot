"""The agent loop: the model plans, calls tools, reads the results, and repeats until it can answer.

Messages use the OpenAI chat format (system / user / assistant with tool_calls / tool),
which Groq and most other providers accept.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import openai
from sqlalchemy.orm import Session

from .. import catalog
from ..config import settings
from ..llm import LLMNotConfigured, chat, get_client
from ..memory import memory_context, record_tool_events, refresh_stale_episodes
from ..models import Conversation, User
from .context import build_messages, fit_to_budget, maybe_compact
from .prompts import SYSTEM_PROMPT, user_context
from .tools import ToolContext, compact_profile, execute, openai_tools

log = logging.getLogger(__name__)

TOOL_FORMAT_RETRIES = 2  # Groq returns 400 tool_use_failed when the model writes a malformed tool call


@dataclass
class AgentReply:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    created_drafts: list[int] = field(default_factory=list)
    stopped_reason: str = "stop"
    # Token usage for this turn, summed over every model call
    usage: dict[str, int] = field(default_factory=lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0})

    def add_usage(self, response: Any) -> None:
        u = getattr(response, "usage", None)
        self.usage["calls"] += 1
        if u is None:
            return
        self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
        self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
        details = getattr(u, "prompt_tokens_details", None)
        self.usage["cached_tokens"] += (getattr(details, "cached_tokens", 0) or 0) if details else 0


def _short(result_json: str, limit: int = 400) -> str:
    return result_json if len(result_json) <= limit else result_json[:limit] + "..."


def normalize_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert chats saved in the older block format (content lists with tool_use /
    tool_result blocks) to the OpenAI format. Already-converted messages pass through."""
    out: list[dict[str, Any]] = []
    for m in messages or []:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        if m["role"] == "assistant":
            text = "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [
                {"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                for b in content if b.get("type") == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            results = [b for b in content if b.get("type") == "tool_result"]
            for b in results:
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content", ""))})
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _assistant_message(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in msg.tool_calls
        ]
    return out


def _parse_args(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        return None, f"Arguments were not valid JSON ({e}). Call the tool again with a valid JSON object."
    if not isinstance(value, dict):
        return None, "Arguments must be a JSON object."
    return value, None


def _call_with_retry(client: openai.OpenAI, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_out: int):
    for attempt in range(TOOL_FORMAT_RETRIES + 1):
        try:
            return chat(client, messages, tools, max_completion_tokens=max_out)
        except openai.BadRequestError as e:
            if "tool_use_failed" in str(e) and attempt < TOOL_FORMAT_RETRIES:
                log.warning("Model produced a malformed tool call; retrying (%d)", attempt + 1)
                continue
            raise


def run_agent(
    db: Session,
    user: User,
    conversation: Conversation,
    user_message: str,
    client: openai.OpenAI | None = None,
) -> AgentReply:
    try:
        client = client or get_client()
    except LLMNotConfigured:
        return AgentReply(text="The assistant isn't configured yet. Add GROQ_API_KEY to the .env file and restart the server.", stopped_reason="error")
    ctx = ToolContext(db, user)

    # Episodic memory: summarize other chats that changed since last time, then
    # load recent episodes and the application tracker into the context.
    try:
        refresh_stale_episodes(db, user, exclude_conversation_id=conversation.id)
    except Exception:
        log.exception("Episode summarization failed")
        db.rollback()
    names = {s["id"]: s["name"] for s in catalog.all_scholarships(db)}
    memory = memory_context(db, user, conversation.id, names)

    def context() -> str:
        # Rebuilt before every call so a profile saved mid-turn is visible straight away.
        return user_context(user.full_name, user.target_level, user.role, memory, compact_profile(user))

    history = normalize_history(list(conversation.messages or []))
    history.append({"role": "user", "content": user_message})
    # Working memory: fold old turns into the rolling summary when the chat gets long.
    try:
        maybe_compact(conversation, history)
    except Exception:
        log.exception("Chat compaction failed")
    tools = openai_tools()
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    reply = AgentReply(text="")

    try:
        for _ in range(settings.max_agent_steps):
            request, max_out = fit_to_budget(build_messages(SYSTEM_PROMPT, context(), conversation, history), tools)
            response = _call_with_retry(client, request, tools, max_out)
            reply.add_usage(response)
            choice = response.choices[0]
            msg = choice.message
            reply.stopped_reason = choice.finish_reason or "stop"

            if choice.finish_reason == "content_filter":
                reply.text = "Sorry, I can't help with that request. I can help you find and apply for scholarships."
                # Drop the refused turn entirely so the saved history stays valid.
                history = normalize_history(list(conversation.messages or []))
                break

            assistant = _assistant_message(msg)
            history.append(assistant)

            if not msg.tool_calls:
                reply.text = (msg.content or "").strip()
                if choice.finish_reason == "length":
                    reply.text += "\n\n(My answer was cut short. Ask me to continue.)"
                break

            for tc in msg.tool_calls:
                name = tc.function.name
                args, parse_error = _parse_args(tc.function.arguments)
                if parse_error:
                    output, is_error, result = json.dumps({"error": parse_error}), True, {"error": parse_error}
                else:
                    output, is_error, result = execute(ctx, name, args)
                events.append((name, args or {}, result))
                reply.tool_calls.append({"tool": name, "input": args, "is_error": is_error, "result": _short(output)})
                history.append({"role": "tool", "tool_call_id": tc.id, "content": output})
        else:
            reply.text = "I took too many steps on this one. Could you ask again, a bit more specifically?"
            reply.stopped_reason = "max_steps"
    except openai.AuthenticationError:
        log.exception("LLM authentication failed")
        reply.text = "The assistant's API key is invalid. Check GROQ_API_KEY in the .env file."
        reply.stopped_reason = "error"
        return reply
    except openai.RateLimitError:
        reply.text = "The AI service's usage limit has been reached. Please try again in a minute (or tomorrow, if the daily limit is used up)."
        reply.stopped_reason = "rate_limited"
        return reply
    except openai.APIConnectionError:
        reply.text = "I couldn't reach the AI service. Check the internet connection and try again."
        reply.stopped_reason = "error"
        return reply
    except openai.APIStatusError as e:
        log.exception("LLM API error")
        reply.text = f"The assistant hit an error ({e.status_code}). Please try again."
        reply.stopped_reason = "error"
        return reply

    # The history must end with an assistant turn so the next request is valid.
    if history and history[-1]["role"] == "tool":
        history.append({"role": "assistant", "content": reply.text})

    conversation.messages = history
    if conversation.title == "New chat":
        conversation.title = user_message[:60]
    conversation.updated_at = datetime.now(timezone.utc)
    record_tool_events(db, conversation, events)
    reply.created_drafts = ctx.created_drafts
    db.commit()
    return reply


def visible_history(conversation: Conversation) -> list[dict[str, Any]]:
    """Messages the UI shows: user text and assistant text only."""
    out: list[dict[str, Any]] = []
    for m in normalize_history(conversation.messages or []):
        if m["role"] == "user":
            out.append({"role": "user", "text": m["content"]})
        elif m["role"] == "assistant":
            tools = [tc["function"]["name"] for tc in m.get("tool_calls", [])]
            text = (m.get("content") or "").strip()
            if out and out[-1]["role"] == "assistant":
                out[-1]["text"] = "\n\n".join(t for t in (out[-1]["text"], text) if t)
                out[-1]["tools"] += tools
            else:
                out.append({"role": "assistant", "text": text, "tools": tools})
    return [m for m in out if m["text"] or m["role"] == "user"]


__all__ = ["run_agent", "visible_history", "normalize_history", "AgentReply", "get_client"]
