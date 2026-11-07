"""Episodic memory and the application tracker.

Memory types in ScholarPilot:
  working     the current chat history sent with each request (agent/runner.py)
  semantic    facts: the student profile and notes (profiles.py), the catalog (catalog.py)
  episodic    what happened in past chats: Episode rows (this file)
  procedural  how to do things: the system prompt and the tools' code

Episodes are built in two layers:
  1. events: recorded automatically after every tool call, so even without an
     LLM we know e.g. "checked eligibility: chevening = not_eligible".
  2. summary: written by the LLM once a chat is finished (lazily, the next time
     the student starts or continues another chat), with decisions and next steps.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm import complete_json
from .models import APPLICATION_STATUSES, Application, Conversation, Episode, User

log = logging.getLogger(__name__)

MAX_EVENTS = 40
RECENT_EPISODES_IN_CONTEXT = 3
MAX_SUMMARIES_PER_TURN = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------- events (automatic, no LLM) ----------------

def describe_event(tool: str, args: dict[str, Any], result: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Turn one tool call into a short event line plus the scholarship ids it touched."""
    if "error" in result:
        return None, []
    if tool == "update_profile":
        saved = result.get("saved") or {}
        if not saved:
            return None, []
        parts = [f"{k}={v}" for k, v in saved.items() if k != "note"]
        if "note" in saved:
            parts.append(f'note "{saved["note"]}"')
        return "saved to profile: " + ", ".join(parts), []
    if tool == "check_eligibility":
        rows = result.get("results", [])
        ids = [r["id"] for r in rows]
        shown = ", ".join(f'{r["id"]}={r["verdict"]}' for r in rows[:6])
        more = f" (+{len(rows) - 6} more)" if len(rows) > 6 else ""
        return f"checked eligibility: {shown}{more}", ids if len(rows) <= 6 else []
    if tool == "plan_application":
        plans = result.get("plans", [])
        ids = [p["id"] for p in plans]
        deadlines = ", ".join(f'{p["id"]} (deadline {p.get("deadline")})' for p in plans)
        return f"built application plan for: {deadlines}", ids
    if tool == "draft_email":
        return f'drafted email "{args.get("subject", "")}" to {args.get("to_office", "")} (waiting for approval)', [args["scholarship_id"]] if args.get("scholarship_id") else []
    if tool == "check_scam":
        return f'scam check: {result.get("risk_level")} risk ({len(result.get("signals", []))} signals)', []
    if tool == "update_application":
        app = result.get("application", {})
        return f'application {app.get("scholarship_id")}: {app.get("status")}', [app["scholarship_id"]] if app.get("scholarship_id") else []
    if tool == "get_scholarship":
        return None, [args["scholarship_id"]] if args.get("scholarship_id") else []
    return None, []


def get_or_create_episode(db: Session, conversation: Conversation) -> Episode:
    ep = db.scalar(select(Episode).where(Episode.conversation_id == conversation.id))
    if ep is None:
        ep = Episode(user_id=conversation.user_id, conversation_id=conversation.id, events=[], scholarship_ids=[], decisions=[], next_steps=[])
        db.add(ep)
        db.flush()
    return ep


def record_tool_events(db: Session, conversation: Conversation, calls: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> None:
    if not calls:
        return
    ep = get_or_create_episode(db, conversation)
    events = list(ep.events or [])
    ids = list(ep.scholarship_ids or [])
    today = date.today().isoformat()
    for tool, args, result in calls:
        line, touched = describe_event(tool, args, result)
        if line:
            events.append(f"{today} {line}")
        for sid in touched:
            if sid not in ids:
                ids.append(sid)
    ep.events = events[-MAX_EVENTS:]
    ep.scholarship_ids = ids
    ep.updated_at = _now()


# ---------------- summaries (LLM, lazy) ----------------

SUMMARY_SYSTEM = (
    "You write short memory notes for a scholarship advisor about a finished chat with a student. "
    "Reply with only a JSON object."
)

SUMMARY_PROMPT = """Chat date: {date}

Events recorded from tool calls:
{events}

Transcript (may be shortened):
{transcript}

Return JSON with these keys:
  "summary": two or three sentences on what was discussed and found, written so the advisor can pick up next time
  "decisions": list of things the student decided (e.g. "will apply to Stipendium Hungaricum"), empty if none
  "next_steps": list of concrete things the student still has to do, empty if none
Only include what actually happened in the chat."""


def _transcript(conversation: Conversation, limit: int = 6000) -> str:
    lines = []
    for m in conversation.messages or []:
        if m.get("role") not in ("user", "assistant"):
            continue  # tool results are already captured as events
        content = m.get("content")
        if isinstance(content, list):  # older block format
            content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        if content:
            lines.append(f"{m['role'].upper()}: {content}")
    text = "\n".join(lines)
    return text if len(text) <= limit else "...\n" + text[-limit:]


def fallback_summary(ep: Episode) -> str:
    if not ep.events:
        return "Short chat with no recorded actions."
    return "Actions: " + "; ".join(e.split(" ", 1)[1] for e in ep.events[-6:])


def summarize_episode(db: Session, conversation: Conversation) -> Episode:
    ep = get_or_create_episode(db, conversation)
    data = complete_json(
        SUMMARY_SYSTEM,
        SUMMARY_PROMPT.format(
            date=(_aware(conversation.updated_at) or _now()).date().isoformat(),
            events="\n".join(ep.events or []) or "(none)",
            transcript=_transcript(conversation),
        ),
    )
    if data and isinstance(data.get("summary"), str):
        ep.summary = data["summary"].strip()
        ep.decisions = [str(x) for x in data.get("decisions", []) if x][:10]
        ep.next_steps = [str(x) for x in data.get("next_steps", []) if x][:10]
    else:
        ep.summary = fallback_summary(ep)
    ep.summarized_at = _now()
    return ep


def _needs_summary(ep: Episode | None, conversation: Conversation) -> bool:
    if not conversation.messages:
        return False
    if ep is None or ep.summarized_at is None:
        return True
    return _aware(ep.summarized_at) < _aware(conversation.updated_at)


def refresh_stale_episodes(db: Session, user: User, exclude_conversation_id: int | None = None) -> int:
    """Summarize the user's other chats that changed since their last summary. Returns how many."""
    convos = db.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).limit(10)
    )
    done = 0
    for c in convos:
        if c.id == exclude_conversation_id:
            continue
        ep = db.scalar(select(Episode).where(Episode.conversation_id == c.id))
        if _needs_summary(ep, c):
            summarize_episode(db, c)
            done += 1
            if done >= MAX_SUMMARIES_PER_TURN:
                break
    if done:
        db.commit()
    return done


def episode_to_dict(ep: Episode) -> dict[str, Any]:
    return {
        "conversation_id": ep.conversation_id,
        "date": (_aware(ep.updated_at) or _now()).date().isoformat(),
        "summary": ep.summary or fallback_summary(ep),
        "decisions": ep.decisions or [],
        "next_steps": ep.next_steps or [],
        "scholarship_ids": ep.scholarship_ids or [],
    }


def recent_episodes(db: Session, user: User, exclude_conversation_id: int | None = None, limit: int = RECENT_EPISODES_IN_CONTEXT) -> list[dict[str, Any]]:
    q = select(Episode).where(Episode.user_id == user.id).order_by(Episode.updated_at.desc())
    if exclude_conversation_id is not None:
        q = q.where(Episode.conversation_id != exclude_conversation_id)
    return [episode_to_dict(e) for e in db.scalars(q.limit(limit))]


def search_episodes(db: Session, user: User, query: str | None = None, scholarship_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    rows = db.scalars(select(Episode).where(Episode.user_id == user.id).order_by(Episode.updated_at.desc()))
    words = [w for w in (query or "").lower().split() if len(w) > 2]
    out = []
    for ep in rows:
        d = episode_to_dict(ep)
        if scholarship_id and scholarship_id not in d["scholarship_ids"]:
            continue
        if words:
            haystack = json.dumps(d).lower() + " " + " ".join(ep.events or []).lower()
            if not any(w in haystack for w in words):
                continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


# ---------------- application tracker ----------------

def application_to_dict(app: Application, scholarship_name: str | None = None) -> dict[str, Any]:
    return {
        "scholarship_id": app.scholarship_id,
        "scholarship_name": scholarship_name,
        "status": app.status,
        "notes": app.notes,
        "next_action": app.next_action,
        "next_action_date": app.next_action_date,
        "history": app.history or [],
        "updated_at": (_aware(app.updated_at) or _now()).date().isoformat(),
    }


def list_applications(db: Session, user: User) -> list[Application]:
    return list(db.scalars(select(Application).where(Application.user_id == user.id).order_by(Application.updated_at.desc())))


def upsert_application(
    db: Session,
    user: User,
    scholarship_id: str,
    status: str | None = None,
    notes: str | None = None,
    next_action: str | None = None,
    next_action_date: str | None = None,
) -> Application:
    if status is not None and status not in APPLICATION_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(APPLICATION_STATUSES)}")
    if next_action_date:
        date.fromisoformat(next_action_date)  # validates YYYY-MM-DD
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.scholarship_id == scholarship_id))
    today = date.today().isoformat()
    if app is None:
        app = Application(user_id=user.id, scholarship_id=scholarship_id, status=status or "interested", history=[])
        db.add(app)
        app.history = [{"status": app.status, "date": today}]
    elif status and status != app.status:
        app.status = status
        app.history = list(app.history or []) + [{"status": status, "date": today}]
    if notes is not None:
        app.notes = notes.strip() or None
    if next_action is not None:
        app.next_action = next_action.strip() or None
    if next_action_date is not None:
        app.next_action_date = next_action_date or None
    app.updated_at = _now()
    db.flush()
    return app


def delete_application(db: Session, user: User, scholarship_id: str) -> bool:
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.scholarship_id == scholarship_id))
    if app is None:
        return False
    db.delete(app)
    db.flush()
    return True


# ---------------- what goes into the model's context ----------------

def memory_context(db: Session, user: User, conversation_id: int, names: dict[str, str]) -> str:
    """Short text block with recent episodes and tracked applications, added to the system prompt."""
    parts = []
    episodes = recent_episodes(db, user, exclude_conversation_id=conversation_id)
    if episodes:
        lines = []
        for e in episodes:
            line = f"- {e['date']}: {e['summary']}"
            if e["decisions"]:
                line += " Decided: " + "; ".join(e["decisions"]) + "."
            if e["next_steps"]:
                line += " To do: " + "; ".join(e["next_steps"]) + "."
            lines.append(line)
        parts.append("Earlier chats with this student (most recent first):\n" + "\n".join(lines))
    apps = list_applications(db, user)
    if apps:
        lines = []
        for a in apps:
            line = f"- {names.get(a.scholarship_id, a.scholarship_id)} [{a.scholarship_id}]: {a.status}"
            if a.next_action:
                line += f"; next: {a.next_action}" + (f" by {a.next_action_date}" if a.next_action_date else "")
            lines.append(line)
        parts.append("Applications the student is tracking:\n" + "\n".join(lines))
    return "\n\n".join(parts)
