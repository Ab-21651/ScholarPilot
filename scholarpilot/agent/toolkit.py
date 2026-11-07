"""Tool hierarchy: tools are grouped so the model navigates a small tree
instead of a flat list, and only the groups a request needs are sent.

    ScholarPilot tools
    ├── student        profile facts, application tracker, past sessions
    │   ├── update_profile
    │   ├── update_application
    │   └── recall_past_sessions
    ├── scholarships   find and read catalog entries
    │   ├── search_scholarships
    │   └── get_scholarship
    ├── planning       qualify and plan
    │   ├── check_eligibility
    │   └── plan_application
    ├── outreach       contact offices
    │   └── draft_email
    └── safety         scam checks
        └── check_scam

How it works on each model call:
  1. The system prompt carries a short map of this tree (names + one line each).
  2. A keyword router picks the groups the student's message needs (free, no LLM call).
  3. Only those groups' full tool schemas are sent, plus `open_tools`, a tiny
     meta-tool the model calls to load any other group when it needs one.
  4. Groups the model has used stay loaded for the rest of the turn.
"""

from __future__ import annotations

import re
from typing import Any

from .tools import TOOLS

GROUPS: dict[str, dict[str, Any]] = {
    "student": {
        "about": "remember facts about the student, track applications, recall earlier chats",
        "tools": ["update_profile", "update_application", "recall_past_sessions"],
    },
    "scholarships": {
        "about": "find scholarships with the student's verdict, read one in full",
        "tools": ["search_scholarships", "get_scholarship"],
    },
    "planning": {
        "about": "check eligibility in detail and build gap lists and timelines",
        "tools": ["check_eligibility", "plan_application"],
    },
    "outreach": {
        "about": "draft emails to offices, sent only after the student approves",
        "tools": ["draft_email"],
    },
    "safety": {
        "about": "check links or messages for scam signs",
        "tools": ["check_scam"],
    },
}

ALL_GROUPS = tuple(GROUPS)
DEFAULT_GROUPS = ("scholarships", "planning")
TOOL_GROUP = {tool: group for group, g in GROUPS.items() for tool in g["tools"]}
_BY_NAME = {t["name"]: t for t in TOOLS}

ROUTES: dict[str, re.Pattern[str]] = {
    "student": re.compile(
        r"\b(my|i am|i'm|im|i have|i've|i did|i got|i finished|i work|ielts|toefl|pte|duolingo|sat|gre|jlpt|cgpa|gpa|marks|percent|"
        r"born|age|years old|experience|worked|graduat|applied|apply(ing)? to|interview|accepted|rejected|tracker|track|"
        r"remember|last time|before|earlier|decided|preparing)\b|%",
        re.I,
    ),
    "scholarships": re.compile(
        r"scholarship|options?|which|list|countr|germany|german|uk\b|britain|england|hungar|ital|japan|australia|erasmus|daad|"
        r"chevening|commonwealth|mext|adb|dsu|maeci|stipend|fund|details|documents?|deadline|requirement|official|link",
        re.I,
    ),
    "planning": re.compile(r"eligib|qualif|missing|gap|need|timeline|plan|schedule|deadline|chance|can i|am i|what do i|steps?", re.I),
    "outreach": re.compile(r"e-?mail|mail|write to|contact|letter|draft|message the|ask the (office|university)", re.I),
    "safety": re.compile(r"https?://|www\.|\.(com|xyz|net|org|info|top|ly)\b|scam|fake|fraud|real\?|legit|genuine|fee|pay|jazz ?cash|easy ?paisa|whatsapp|congratulations|selected|otp", re.I),
}

OPEN_TOOLS = {
    "type": "function",
    "function": {
        "name": "open_tools",
        "description": "Load more tool groups from the tool map when the tools you have don't cover what you need.",
        "parameters": {
            "type": "object",
            "properties": {"groups": {"type": "array", "items": {"type": "string", "enum": list(ALL_GROUPS)}}},
            "required": ["groups"],
            "additionalProperties": False,
        },
    },
}


def tool_map() -> str:
    """The tree shown in the system prompt."""
    lines = ["Tool map. Only some groups are loaded at a time; call open_tools to load a missing one."]
    for group, g in GROUPS.items():
        lines.append(f"- {group} ({g['about']}): {', '.join(g['tools'])}")
    return "\n".join(lines)


def route(message: str) -> set[str]:
    """Pick the groups a student message probably needs."""
    groups = {g for g, pattern in ROUTES.items() if pattern.search(message or "")}
    if not groups - {"student"}:
        groups |= set(DEFAULT_GROUPS)
    return groups


def tools_for(groups: set[str]) -> list[dict[str, Any]]:
    """OpenAI-format schemas for the loaded groups (in tree order), plus open_tools."""
    out = []
    for group in ALL_GROUPS:
        if group in groups:
            for name in GROUPS[group]["tools"]:
                t = _BY_NAME[name]
                out.append({"type": "function", "function": {"name": name, "description": t["description"], "parameters": t["input_schema"]}})
    if set(groups) != set(ALL_GROUPS):
        out.append(OPEN_TOOLS)
    return out


def open_groups(args: dict[str, Any], loaded: set[str]) -> dict[str, Any]:
    """Handle an open_tools call: add the groups and report what is now available."""
    requested = args.get("groups") or []
    if isinstance(requested, str):
        requested = [requested]
    unknown = [g for g in requested if g not in GROUPS]
    valid = [g for g in requested if g in GROUPS]
    loaded.update(valid)
    result: dict[str, Any] = {"loaded": {g: GROUPS[g]["tools"] for g in valid}}
    if unknown:
        result["unknown_groups"] = unknown
        result["available_groups"] = list(ALL_GROUPS)
    return result
