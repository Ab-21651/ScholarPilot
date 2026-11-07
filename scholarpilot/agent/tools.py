"""Tool definitions given to the model, and the code that runs them."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .. import catalog
from ..models import EmailDraft, User
from ..profiles import EDITABLE_FIELDS, profile_to_dict, update_profile
from ..tools.eligibility import check_eligibility, rank_matches
from ..tools.planner import build_timeline, merge_timelines
from ..tools.scam import check_message

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_profile",
        "description": "Read the student's saved profile: target level, grades, tests, age, work experience, preferences and remembered notes. Call this at the start of a conversation and before giving any personalised advice.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "update_profile",
        "description": (
            "Save facts the student tells you about themselves so you remember them next time. "
            "Only save what the student actually stated; never guess values. "
            f"Allowed fields: {', '.join(EDITABLE_FIELDS)}. date_of_birth is YYYY-MM-DD; grade_percent is the HSSC/A-Level percentage; "
            "cgpa with cgpa_scale (usually 4); work_experience_months is a whole number; preferred_countries is a list. "
            "Use 'note' for other useful facts (e.g. 'wants an English-taught engineering course')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "updates": {"type": "object", "description": "Field name to new value."},
                "note": {"type": "string", "description": "Optional free-text fact to remember."},
            },
            "required": ["updates"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_scholarships",
        "description": "Search the verified-source catalog. All filters are optional. Defaults to the student's own level. Returns short summaries; use get_scholarship for full details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "e.g. Germany, United Kingdom, Hungary, Italy, Europe, Japan, Australia"},
                "field": {"type": "string", "description": "Subject area, e.g. engineering"},
                "funding": {"type": "string", "enum": ["full", "partial"]},
                "query": {"type": "string", "description": "Keywords in the name or provider"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_scholarship",
        "description": "Full catalog entry for one scholarship: funding, eligibility rules, documents, steps, deadline and official links.",
        "input_schema": {
            "type": "object",
            "properties": {"scholarship_id": {"type": "string"}},
            "required": ["scholarship_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_eligibility",
        "description": (
            "Run the rule-based eligibility engine against the student's saved profile. Always use this before telling a student whether they qualify. "
            "It returns a verdict (eligible / likely_eligible / not_eligible) and a pass/fail/unknown reason for each rule. "
            "Leave scholarship_ids empty to check every scholarship for the student's level and get a ranked list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"scholarship_ids": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_application",
        "description": "For the chosen scholarships, list the student's gaps (e.g. missing IELTS 6.5), the required documents and steps, and a dated timeline worked back from each deadline. Also returns one merged timeline.",
        "input_schema": {
            "type": "object",
            "properties": {"scholarship_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["scholarship_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "draft_email",
        "description": (
            "Save an email draft to an admissions or scholarship office. This does NOT send anything: the student must open the Drafts tab and approve it. "
            "Write it on the student's behalf, in their name, polite and short. Only use an address that appears on the official page; otherwise leave to_address empty."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_office": {"type": "string", "description": "e.g. 'DAAD Information Centre Islamabad'"},
                "to_address": {"type": "string", "description": "Official email address, if known"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "scholarship_id": {"type": "string"},
            },
            "required": ["to_office", "subject", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_scam",
        "description": "Check a pasted message, ad or link for scam signs (fees, guarantees, urgency, lookalike or unofficial domains, requests for documents or OTPs). Use it whenever the student shares a link or a message about a scholarship offer.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The full message or URL"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
]


class ToolContext:
    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user
        self.created_drafts: list[int] = []

    @property
    def level(self) -> str:
        return self.user.target_level


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' is required")
    return value.strip()


def _ids(args: dict[str, Any], key: str = "scholarship_ids") -> list[str]:
    value = args.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{key}' must be a list of ids")
    return value


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    db, user = ctx.db, ctx.user

    if name == "get_profile":
        return profile_to_dict(user)

    if name == "update_profile":
        updates = args.get("updates")
        if not isinstance(updates, dict):
            raise ValueError("'updates' must be an object")
        changed, errors = update_profile(db, user, updates, args.get("note"))
        db.commit()
        return {"saved": changed, "errors": errors}

    if name == "search_scholarships":
        results = catalog.search_scholarships(
            db, level=ctx.level, country=args.get("country"), field=args.get("field"), funding=args.get("funding"), query=args.get("query")
        )
        return {"level": ctx.level, "count": len(results), "results": [catalog.summarize(s, ctx.level) for s in results]}

    if name == "get_scholarship":
        sid = _require_str(args, "scholarship_id")
        s = catalog.get_scholarship(db, sid)
        if not s:
            raise ValueError(f"No scholarship with id '{sid}'. Use search_scholarships to find ids.")
        return s

    if name == "check_eligibility":
        profile = profile_to_dict(user)
        ids = _ids(args)
        if ids:
            items = []
            for sid in ids:
                s = catalog.get_scholarship(db, sid)
                if not s:
                    raise ValueError(f"No scholarship with id '{sid}'")
                items.append(s)
        else:
            items = catalog.search_scholarships(db, level=ctx.level)
        results = rank_matches([check_eligibility(s, profile, ctx.level) for s in items])
        return {"level": ctx.level, "results": [r.to_dict() for r in results]}

    if name == "plan_application":
        profile = profile_to_dict(user)
        timelines = []
        for sid in _ids(args):
            s = catalog.get_scholarship(db, sid)
            if not s:
                raise ValueError(f"No scholarship with id '{sid}'")
            timelines.append(build_timeline(s, check_eligibility(s, profile, ctx.level), profile=profile))
        return {"plans": timelines, "merged_timeline": merge_timelines(timelines)}

    if name == "draft_email":
        draft = EmailDraft(
            user_id=user.id,
            scholarship_id=args.get("scholarship_id"),
            to_office=_require_str(args, "to_office"),
            to_address=(args.get("to_address") or None),
            subject=_require_str(args, "subject"),
            body=_require_str(args, "body"),
            status="pending",
        )
        db.add(draft)
        db.commit()
        ctx.created_drafts.append(draft.id)
        return {"draft_id": draft.id, "status": "pending", "message": "Draft saved. The student must review and approve it in the Drafts tab before anything is sent."}

    if name == "check_scam":
        report = check_message(_require_str(args, "text"), catalog.official_domains(db))
        return report.to_dict()

    raise ValueError(f"Unknown tool '{name}'")


def execute(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool and return (json result, is_error)."""
    try:
        return json.dumps(run_tool(ctx, name, args or {}), default=str), False
    except Exception as e:  # tool errors go back to the model so it can recover
        ctx.db.rollback()
        return json.dumps({"error": str(e)}), True
