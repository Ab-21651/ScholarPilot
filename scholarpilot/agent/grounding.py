"""Grounding check: a second, cheap model call that removes unsupported claims.

When an answer was built from the process guides, the drafting model sometimes adds
"helpful" details from its own memory (a photo size, an extra step, a test name). For
documents students rely on, that is not acceptable. This step compares the draft with
the exact guide text that was retrieved in this turn and rewrites the draft so every
factual claim is supported by that text.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import complete_json

log = logging.getLogger(__name__)

GUIDE_TOOLS = {"browse_guides", "search_guides"}
MAX_SOURCE_CHARS = 7000

CHECK_SYSTEM = "You are a strict fact checker for a student-advice service. Reply with only a JSON object."

CHECK_PROMPT = """SOURCE TEXT (official and verified; the only facts allowed):
{sources}

DRAFT ANSWER to a student:
{draft}

Step 1. Split the draft into its factual claims (steps, documents, fees, amounts, dates, times, test names,
options, requirements, how to log in or upload, delivery, addresses, websites, phone numbers).
Step 2. For each claim, copy the exact sentence from the SOURCE TEXT that states it. If no sentence states
it, the claim is unsupported, even if it is common knowledge or probably true.
Step 3. Write the final answer using only supported claims. Where an unsupported claim was needed for the
student to act, write "check the official page" instead, without repeating the removed detail anywhere
(not even in brackets or as an example). Keep links, the friendly tone, bullet formatting
and the source line. Facts the student told us about themselves (e.g. "8 months left") may stay.

Return {{"claims": [{{"claim": "...", "evidence": "<exact source sentence or empty>"}}],
"answer": "<final answer>", "removed": ["<each unsupported claim>"]}}"""


def _source_texts(tool_results: list[tuple[str, dict[str, Any]]]) -> str:
    parts: list[str] = []
    for name, result in tool_results:
        if name == "browse_guides" and result.get("text"):
            parts.append(f"[{result.get('guide')} / {result.get('section')}; sources: {', '.join(result.get('sources', []))}; checked {result.get('checked')}]\n{result['text']}")
        elif name == "search_guides":
            for hit in result.get("results", []):
                if hit.get("text"):
                    parts.append(f"[{hit.get('guide')} / {hit.get('section')}; sources: {', '.join(hit.get('sources', []))}; checked {hit.get('checked')}]\n{hit['text']}")
    text = "\n\n".join(dict.fromkeys(parts))  # drop duplicates, keep order
    return text[:MAX_SOURCE_CHARS]


def ground_answer(draft: str, tool_results: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    """Return (answer, report). The draft is returned unchanged if there is nothing to check
    against or the check fails."""
    sources = _source_texts([(n, r) for n, r in tool_results if n in GUIDE_TOOLS])
    if not sources or not draft.strip():
        return draft, {"checked": False}
    data = complete_json(
        CHECK_SYSTEM, CHECK_PROMPT.format(sources=sources, draft=draft), max_tokens=3000, reasoning_effort="medium"
    )
    answer = (data or {}).get("answer")
    if not isinstance(answer, str) or not answer.strip():
        log.warning("Grounding check failed; keeping the draft")
        return draft, {"checked": False}
    removed = [str(x) for x in (data.get("removed") or []) if x]
    if removed:
        log.info("Grounding check removed: %s", json.dumps(removed, ensure_ascii=False))
    return answer.strip(), {"checked": True, "removed": removed}
