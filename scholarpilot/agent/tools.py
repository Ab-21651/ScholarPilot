"""Tool definitions given to the model, and the code that runs them.

Descriptions and outputs are kept short on purpose: every tool definition is
sent with every model call, and every tool output stays in the chat history.
The student's profile and tracker are already in the context message, so
there are no separate "read profile" / "read tracker" tools.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .. import catalog, guides
from ..memory import application_to_dict, search_episodes, upsert_application
from ..models import APPLICATION_STATUSES, EmailDraft, User
from ..profiles import EDITABLE_FIELDS, profile_to_dict, update_profile
from ..tools.eligibility import EligibilityResult, check_eligibility, rank_matches
from ..tools.planner import build_timeline

OBJ = "object"


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {"type": OBJ, "properties": props, "additionalProperties": False}
    if required:
        s["required"] = required
    return s


IDS = {"type": "array", "items": {"type": "string"}}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "update_profile",
        "description": (
            "Save facts the student stated about themselves (never guess). Fields: " + ", ".join(EDITABLE_FIELDS) + ". "
            "date_of_birth YYYY-MM-DD; grade_percent = HSSC/A-Level %; cgpa + cgpa_scale; work_experience_months integer. "
            "Use note for anything else worth remembering."
        ),
        "input_schema": _schema({"updates": {"type": OBJ}, "note": {"type": "string"}}, ["updates"]),
    },
    {
        "name": "search_scholarships",
        "description": "List catalog scholarships for the student's level, each with the rule engine's verdict for this student. Filters optional; most scholarships accept any subject, so don't filter by subject.",
        "input_schema": _schema({
            "country": {"type": "string"},
            "funding": {"type": "string", "enum": ["full", "partial"]},
            "query": {"type": "string", "description": "words in the name or provider"},
        }),
    },
    {
        "name": "get_scholarship",
        "description": "Full details of one scholarship: funding, rules, documents, steps, deadline, official links.",
        "input_schema": _schema({"scholarship_id": {"type": "string"}}, ["scholarship_id"]),
    },
    {
        "name": "check_eligibility",
        "description": "Rule engine verdict per scholarship (eligible / likely_eligible / not_eligible) with the failing or unknown rules. Always use it before saying whether the student qualifies. Empty ids = check all.",
        "input_schema": _schema({"scholarship_ids": IDS}),
    },
    {
        "name": "plan_application",
        "description": "Gaps, documents, steps and a dated timeline back from the deadline, for chosen scholarships.",
        "input_schema": _schema({"scholarship_ids": IDS}, ["scholarship_ids"]),
    },
    {
        "name": "update_application",
        "description": "Add or update a scholarship in the student's tracker when they decide to pursue it, apply, get an interview or hear back.",
        "input_schema": _schema({
            "scholarship_id": {"type": "string"},
            "status": {"type": "string", "enum": list(APPLICATION_STATUSES)},
            "next_action": {"type": "string"},
            "next_action_date": {"type": "string", "description": "YYYY-MM-DD"},
            "notes": {"type": "string"},
        }, ["scholarship_id"]),
    },
    {
        "name": "recall_past_sessions",
        "description": "Search older chats with this student (summaries, decisions, actions). Recent ones are already in context.",
        "input_schema": _schema({"query": {"type": "string"}, "scholarship_id": {"type": "string"}}),
    },
    {
        "name": "draft_email",
        "description": "Save an email draft to an office, in the student's name. Nothing is sent: the student approves it in the Drafts tab. Use only an address from the official page, else leave to_address empty.",
        "input_schema": _schema({
            "to_office": {"type": "string"},
            "to_address": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "scholarship_id": {"type": "string"},
        }, ["to_office", "subject", "body"]),
    },
    {
        "name": "browse_guides",
        "description": "Open the process-guide tree (passport, attestation, tests, proof of funds, visas). No path = list areas; then an area, a guide, or a section path to read it.",
        "input_schema": _schema({"path": {"type": "string"}}),
    },
    {
        "name": "search_guides",
        "description": "Keyword search across all process guides when you don't know which guide holds the answer. Returns section paths to open with browse_guides.",
        "input_schema": _schema({
            "query": {"type": "string"},
            "area": {"type": "string", "enum": ["identity", "education", "language", "money", "visa"]},
        }, ["query"]),
    },
    {
        "name": "check_scam",
        "description": "Check a pasted message or link for scam signs. Use whenever the student shares an offer or link.",
        "input_schema": _schema({"text": {"type": "string"}}, ["text"]),
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


def _lookup(db: Session, sid: str) -> dict[str, Any]:
    s = catalog.get_scholarship(db, sid)
    if not s:
        raise ValueError(f"No scholarship with id '{sid}'. Use search_scholarships to find ids.")
    return s


# ---------- compact views (what the model reads) ----------

def compact_profile(user: User) -> dict[str, Any]:
    """Profile without empty fields, for the context message."""
    p = profile_to_dict(user)
    if p.get("cgpa") is None:
        p.pop("cgpa_scale", None)
    return {k: v for k, v in p.items() if v not in (None, "", [], {}) and k not in {"full_name", "role", "target_level"}}


def compact_eligibility(r: EligibilityResult, detailed: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"id": r.scholarship_id, "name": r.scholarship_name, "verdict": r.verdict}
    issues = [c.detail for c in r.criteria if c.status != "pass"]
    if issues:
        out["issues"] = issues
    if detailed:
        out["passed"] = [c.detail for c in r.criteria if c.status == "pass"]
        if r.manual_checks:
            out["also_check"] = r.manual_checks[:5]
    return out


def compact_scholarship(s: dict[str, Any], level: str) -> dict[str, Any]:
    track = s["tracks"].get(level)
    out = {k: s[k] for k in ("id", "name", "country", "provider", "funding", "funding_details", "official_url", "apply_url") if k in s}
    out["verification_status"] = s.get("verification_status")
    if s.get("checked"):
        out["checked_on"] = s["checked"]
    if s.get("verification_status") == "retired":
        out["retired"] = s.get("retired_reason") or "This scholarship is no longer offered."
    if track is None:
        out["note"] = f"No {level}'s track; offered for: {', '.join(s['tracks'])}"
    else:
        out["eligibility"] = track["eligibility"]
        out["deadline"] = track.get("deadline")
        out["documents"] = track.get("documents", [])
        out["steps"] = track.get("steps", [])
    return out


def compact_plan(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t["scholarship_id"],
        "name": t["scholarship_name"],
        "verdict": t["verdict"],
        "deadline": t["deadline"],
        "gaps": [g["action"] for g in t["gaps"]],
        "documents": t["documents"],
        "milestones": [f'{m["date"]} {m["title"]}' + (" (OVERDUE)" if m["status"] == "overdue" else "") for m in t["milestones"]],
        "warnings": t["warnings"],
        "apply_url": t["apply_url"],
    }


MAX_SEARCH_RESULTS = 10


def _first_sentence(text: str, limit: int = 110) -> str:
    """Short, exact excerpt of what an award pays, so list answers don't invent it."""
    first = text.split(". ")[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 3].rsplit(" ", 1)[0] + "..."


# ---------- dispatcher ----------

def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    db, user = ctx.db, ctx.user

    if name == "update_profile":
        updates = args.get("updates")
        if not isinstance(updates, dict):
            raise ValueError("'updates' must be an object")
        changed, errors = update_profile(db, user, updates, args.get("note"))
        db.commit()
        return {"saved": changed, **({"errors": errors} if errors else {})}

    if name == "search_scholarships":
        results = catalog.search_scholarships(
            db, level=ctx.level, country=args.get("country"), funding=args.get("funding"), query=args.get("query")
        )
        profile = profile_to_dict(user)
        rows = []
        for s in results:
            r = check_eligibility(s, profile, ctx.level)
            summary = catalog.summarize(s, ctx.level)
            deadline, status = summary.get("typical_deadline"), summary.get("deadline_status")
            row = {
                "id": s["id"], "name": s["name"], "country": s["country"], "funding": s["funding"],
                "deadline": f"{deadline} ({status})" if status and status != "varies" else deadline,
                "covers": _first_sentence(s.get("funding_details", "")),
                "verdict": r.verdict, "url": s["official_url"],
            }
            issues = [c.detail for c in r.criteria if c.status != "pass"]
            if issues:
                row["issues"] = [i if len(i) <= 90 else i[:87] + "..." for i in issues[:2]]
            rows.append(row)
        order = {"eligible": 0, "likely_eligible": 1, "not_eligible": 2}
        rows.sort(key=lambda x: order[x["verdict"]])
        out = {"count": len(rows), "results": rows[:MAX_SEARCH_RESULTS]}
        checked = sorted(s["checked"] for s in results if s.get("checked"))
        if checked:
            out["checked_on"] = checked[0]
        if len(rows) > MAX_SEARCH_RESULTS:
            # Keeps the request inside the free-tier token budget; the model can narrow by country.
            out["more"] = f"{len(rows) - MAX_SEARCH_RESULTS} more not shown; search again with a country to see them."
        return out

    if name == "get_scholarship":
        return compact_scholarship(_lookup(db, _require_str(args, "scholarship_id")), ctx.level)

    if name == "check_eligibility":
        profile = profile_to_dict(user)
        ids = _ids(args)
        items = [_lookup(db, sid) for sid in ids] if ids else catalog.search_scholarships(db, level=ctx.level)
        results = rank_matches([check_eligibility(s, profile, ctx.level) for s in items])
        detailed = len(results) <= 2
        return {"results": [compact_eligibility(r, detailed) for r in results]}

    if name == "plan_application":
        profile = profile_to_dict(user)
        plans = []
        for sid in _ids(args):
            s = _lookup(db, sid)
            plans.append(compact_plan(build_timeline(s, check_eligibility(s, profile, ctx.level), profile=profile)))
        if not plans:
            raise ValueError("Give at least one scholarship id")
        return {"plans": plans}

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
        return {"draft_id": draft.id, "status": "pending", "message": "Saved. The student must approve it in the Drafts tab; nothing was sent."}

    if name == "check_scam":
        from ..tools.scam import check_message

        report = check_message(_require_str(args, "text"), catalog.official_domains(db))
        return {
            "risk_level": report.risk_level,
            "score": report.score,
            "signals": [s.explanation for s in report.signals],
            "official_links": report.trusted_links,
            "advice": report.advice,
        }

    if name == "update_application":
        sid = _require_str(args, "scholarship_id")
        s = _lookup(db, sid)
        app = upsert_application(
            db, user, sid, status=args.get("status"), notes=args.get("notes"),
            next_action=args.get("next_action"), next_action_date=args.get("next_action_date"),
        )
        db.commit()
        a = application_to_dict(app, s["name"])
        return {"application": {k: a[k] for k in ("scholarship_id", "status", "next_action", "next_action_date") if a.get(k)}}

    if name == "browse_guides":
        return guides.browse(args.get("path"))

    if name == "search_guides":
        hits = guides.search(_require_str(args, "query"), area=args.get("area"))
        return {"results": hits, **({} if hits else {"note": "No match. Browse the tree, or say the guides don't cover this."})}

    if name == "recall_past_sessions":
        found = search_episodes(db, user, query=args.get("query"), scholarship_id=args.get("scholarship_id"))
        return {"count": len(found), "sessions": [{k: v for k, v in e.items() if v and k != "conversation_id"} for e in found]}

    raise ValueError(f"Unknown tool '{name}'")


def execute(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    """Run a tool and return (compact json result, is_error, result dict)."""
    try:
        result = run_tool(ctx, name, args or {})
        return json.dumps(result, default=str, ensure_ascii=False, separators=(",", ":")), False, result
    except Exception as e:  # tool errors go back to the model so it can recover
        ctx.db.rollback()
        result = {"error": str(e)}
        return json.dumps(result), True, result


def openai_tools() -> list[dict[str, Any]]:
    """TOOLS in the OpenAI / Groq function-calling format."""
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in TOOLS
    ]
