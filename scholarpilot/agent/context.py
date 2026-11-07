"""What gets sent to the model on each call, kept small enough for Groq's free tier.

The full chat is always saved in the database. What we *send* is:

  1. the static system prompt (identical every time, so the provider can cache it)
  2. a short context message: who the student is, episodic memory, tracker,
     and a rolling summary of the older part of this chat
  3. the last few user turns word for word, with tool outputs from earlier
     turns shortened (the current turn's tool outputs are sent in full)

When the chat grows past CONTEXT_KEEP_TURNS (+ a small margin), the oldest
turns are folded into the rolling summary with one cheap LLM call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..llm import complete_json
from ..models import Conversation

log = logging.getLogger(__name__)

SUMMARIZE_MARGIN = 2  # summarize only when this many extra turns have piled up, not on every turn
OLD_TOOL_OUTPUT_CHARS = 400
CHARS_PER_TOKEN = 3.2  # rough estimate for English + JSON with the GPT-OSS tokenizer
MIN_OUTPUT_TOKENS = 512


def user_turn_starts(messages: list[dict[str, Any]]) -> list[int]:
    return [i for i, m in enumerate(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)]


# ---------------- rolling summary ----------------

ROLLING_SYSTEM = "You keep running notes of a scholarship-advice chat so it can continue without the full transcript. Reply with only a JSON object."

ROLLING_PROMPT = """Notes so far:
{previous}

New part of the chat:
{chunk}

Return {{"summary": "..."}}: updated notes, at most 120 words. Keep every concrete fact the student gave, scholarships discussed with their results (eligible / not and why), decisions, drafts created, and open questions. Drop greetings and repetition."""


def _render(messages: list[dict[str, Any]], tool_chars: int = 250) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            lines.append(f"TOOL RESULT: {str(m.get('content', ''))[:tool_chars]}")
        elif role == "assistant":
            calls = [tc["function"]["name"] for tc in m.get("tool_calls", [])]
            if m.get("content"):
                lines.append(f"ASSISTANT: {m['content']}")
            if calls:
                lines.append(f"ASSISTANT CALLED: {', '.join(calls)}")
        elif role == "user":
            lines.append(f"STUDENT: {m.get('content', '')}")
    return "\n".join(lines)


def _fallback_summary(previous: str | None, chunk: list[dict[str, Any]]) -> str:
    said = [m["content"] for m in chunk if m.get("role") == "user" and isinstance(m.get("content"), str)]
    text = (previous + " " if previous else "") + "Student said: " + " | ".join(s[:150] for s in said)
    return text[-900:]


def maybe_compact(conversation: Conversation, history: list[dict[str, Any]]) -> None:
    """Fold old turns into conversation.summary when too many have piled up.

    `history` is the full message list including the new user message.
    """
    keep = max(1, settings.context_keep_turns)
    upto = conversation.summary_upto or 0
    starts = [i for i in user_turn_starts(history) if i >= upto]
    if len(starts) <= keep + SUMMARIZE_MARGIN:
        return
    cut = starts[-keep]
    chunk = history[upto:cut]
    data = complete_json(
        ROLLING_SYSTEM,
        ROLLING_PROMPT.format(previous=conversation.summary or "(none)", chunk=_render(chunk)[-8000:]),
        max_tokens=600,
    )
    summary = data.get("summary") if data else None
    conversation.summary = summary.strip() if isinstance(summary, str) and summary.strip() else _fallback_summary(conversation.summary, chunk)
    conversation.summary_upto = cut


# ---------------- building the request ----------------

def _shorten_tool_output(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[:limit] + f"... [shortened; {len(content)} chars in full]"


def recent_messages(history: list[dict[str, Any]], upto: int, tool_chars: int = OLD_TOOL_OUTPUT_CHARS) -> list[dict[str, Any]]:
    """Messages after the summarized part. Tool outputs before the current turn are shortened."""
    recent = history[upto:]
    starts = user_turn_starts(recent)
    current_turn = starts[-1] if starts else 0
    out = []
    for i, m in enumerate(recent):
        if m.get("role") == "tool" and i < current_turn:
            m = {**m, "content": _shorten_tool_output(str(m.get("content", "")), tool_chars)}
        out.append(m)
    return out


def estimate_tokens(payload: Any) -> int:
    return int(len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) / CHARS_PER_TOKEN)


def build_messages(system: str, context: str, conversation: Conversation, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
    ctx = context
    if conversation.summary:
        ctx += "\n\nEarlier in this chat (summary): " + conversation.summary
    msgs.append({"role": "system", "content": ctx})
    msgs.extend(recent_messages(history, conversation.summary_upto or 0))
    return msgs


def fit_to_budget(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Shrink the request if it would not fit the per-minute token budget.

    Returns (messages, max_output_tokens). Groq counts the output reservation
    against the budget too, so the output cap shrinks before the input does.
    """
    limit = settings.tpm_limit
    if limit <= 0:
        return messages, settings.max_output_tokens
    tool_tokens = estimate_tokens(tools)

    def room(msgs: list[dict[str, Any]]) -> int:
        return limit - estimate_tokens(msgs) - tool_tokens - 150  # safety margin

    if room(messages) >= MIN_OUTPUT_TOKENS:
        return messages, min(settings.max_output_tokens, room(messages))

    # 1) shorten every tool output, including this turn's
    head, rest = messages[:2], messages[2:]
    for chars in (1500, 600, 250):
        rest = [{**m, "content": _shorten_tool_output(str(m.get("content", "")), chars)} if m.get("role") == "tool" else m for m in rest]
        if room(head + rest) >= MIN_OUTPUT_TOKENS:
            return head + rest, min(settings.max_output_tokens, room(head + rest))

    # 2) drop the oldest complete turns, but always keep the current one
    while True:
        starts = user_turn_starts(rest)
        if len(starts) <= 1:
            break
        rest = rest[starts[1]:]
        if room(head + rest) >= MIN_OUTPUT_TOKENS:
            break
    final = head + rest
    log.info("Request trimmed to fit %d TPM (estimated %d input tokens)", limit, estimate_tokens(final) + tool_tokens)
    return final, max(MIN_OUTPUT_TOKENS, min(settings.max_output_tokens, room(final)))
