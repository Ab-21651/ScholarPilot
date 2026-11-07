"""FastAPI app: REST API plus the web frontend.

Run:  py -m uvicorn scholarpilot.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog
from .agent.runner import run_agent, visible_history
from .auth import admin_user, create_token, current_user, hash_password, verify_password
from .config import settings
from .db import get_db, init_db, session_scope
from .emailer import mailto_link, send_draft, smtp_configured
from .memory import (
    application_to_dict,
    delete_application,
    list_applications,
    recent_episodes,
    refresh_stale_episodes,
    upsert_application,
)
from .models import ChangeReview, Conversation, EmailDraft, Episode, Scholarship, User
from .monitor import approve_review, run_monitor
from .profiles import get_or_create_profile, profile_to_dict, update_profile
from .tools.eligibility import check_eligibility, rank_matches
from .tools.planner import build_timeline, merge_timelines
from .tools.scam import check_message

log = logging.getLogger("scholarpilot")


def _scheduled_monitor() -> None:
    with session_scope() as db:
        log.info("Daily monitor: %s", run_monitor(db))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    scheduler = None
    if settings.monitor_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        scheduler.add_job(_scheduled_monitor, "cron", hour=settings.monitor_hour, minute=0)
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown()


app = FastAPI(title="ScholarPilot", version="0.1.0", lifespan=lifespan)


# ---------- schemas ----------

class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=120)
    target_level: Literal["bachelor", "master"]
    role: Literal["student", "parent", "counsellor"] = "student"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ProfileIn(BaseModel):
    updates: dict[str, Any]


class IdsIn(BaseModel):
    scholarship_ids: list[str] = Field(min_length=1)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class DraftEditIn(BaseModel):
    to_address: str | None = None
    subject: str | None = None
    body: str | None = None


class ApplicationIn(BaseModel):
    status: str | None = None
    notes: str | None = None
    next_action: str | None = None
    next_action_date: str | None = None


class ReviewDecisionIn(BaseModel):
    patch: dict[str, Any] | None = None
    note: str | None = None


def _user_out(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "target_level": user.target_level, "role": user.role, "is_admin": user.is_admin}


def _draft_out(d: EmailDraft) -> dict[str, Any]:
    return {
        "id": d.id, "scholarship_id": d.scholarship_id, "to_office": d.to_office, "to_address": d.to_address,
        "subject": d.subject, "body": d.body, "status": d.status, "created_at": d.created_at, "approved_at": d.approved_at,
        "mailto": mailto_link(d),
    }


# ---------- auth ----------

@app.post("/api/auth/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(
        email=email, password_hash=hash_password(body.password), full_name=body.full_name.strip(),
        target_level=body.target_level, role=body.role, is_admin=email in settings.admin_emails,
    )
    db.add(user)
    db.flush()
    get_or_create_profile(db, user)
    db.commit()
    return {"token": create_token(user), "user": _user_out(user)}


@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    return {"token": create_token(user), "user": _user_out(user)}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)


# ---------- profile ----------

@app.get("/api/profile")
def get_profile(user: User = Depends(current_user)):
    return profile_to_dict(user)


@app.put("/api/profile")
def put_profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    changed, errors = update_profile(db, user, body.updates)
    if errors:
        db.rollback()
        raise HTTPException(422, "; ".join(errors))
    db.commit()
    return profile_to_dict(user)


# ---------- catalog and planning ----------

@app.get("/api/scholarships")
def list_scholarships(country: str | None = None, funding: str | None = None, field: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = catalog.search_scholarships(db, level=user.target_level, country=country, funding=funding, field=field)
    return [catalog.summarize(s, user.target_level) for s in items]


@app.get("/api/scholarships/{sid}")
def scholarship_detail(sid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = catalog.get_scholarship(db, sid)
    if not s:
        raise HTTPException(404, "Scholarship not found")
    return s


@app.get("/api/matches")
def matches(user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = profile_to_dict(user)
    items = catalog.search_scholarships(db, level=user.target_level)
    results = rank_matches([check_eligibility(s, profile, user.target_level) for s in items])
    by_id = {s["id"]: s for s in items}
    return [{**r.to_dict(), "country": by_id[r.scholarship_id]["country"], "funding": by_id[r.scholarship_id]["funding"], "official_url": by_id[r.scholarship_id]["official_url"]} for r in results]


@app.post("/api/plan")
def plan(body: IdsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = profile_to_dict(user)
    timelines = []
    for sid in body.scholarship_ids:
        s = catalog.get_scholarship(db, sid)
        if not s:
            raise HTTPException(404, f"Scholarship '{sid}' not found")
        timelines.append(build_timeline(s, check_eligibility(s, profile, user.target_level), profile=profile))
    return {"plans": timelines, "merged_timeline": merge_timelines(timelines)}


@app.post("/api/scam-check")
def scam_check(body: TextIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return check_message(body.text, catalog.official_domains(db)).to_dict()


# ---------- chat ----------

@app.get("/api/conversations")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()))
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in rows]


@app.post("/api/conversations")
def new_conversation(user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = Conversation(user_id=user.id, messages=[])
    db.add(c)
    db.commit()
    return {"id": c.id, "title": c.title}


def _own_conversation(db: Session, user: User, cid: int) -> Conversation:
    c = db.get(Conversation, cid)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    return c


@app.get("/api/conversations/{cid}")
def get_conversation(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _own_conversation(db, user, cid)
    return {"id": c.id, "title": c.title, "messages": visible_history(c)}


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _own_conversation(db, user, cid)
    # Deleting a chat also forgets its episode.
    episode = db.scalar(select(Episode).where(Episode.conversation_id == c.id))
    if episode:
        db.delete(episode)
    db.delete(c)
    db.commit()
    return {"ok": True}


@app.post("/api/conversations/{cid}/messages")
def send_message(cid: int, body: TextIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _own_conversation(db, user, cid)
    reply = run_agent(db, user, c, body.text)
    return {"reply": reply.text, "tool_calls": reply.tool_calls, "created_drafts": reply.created_drafts, "stopped_reason": reply.stopped_reason, "usage": reply.usage}


# ---------- memory: application tracker and episodes ----------

@app.get("/api/applications")
def get_applications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    names = {s["id"]: s for s in catalog.all_scholarships(db)}
    out = []
    for a in list_applications(db, user):
        s = names.get(a.scholarship_id, {})
        d = application_to_dict(a, s.get("name"))
        d["official_url"] = s.get("official_url")
        out.append(d)
    return out


@app.put("/api/applications/{sid}")
def put_application(sid: str, body: ApplicationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = catalog.get_scholarship(db, sid)
    if not s:
        raise HTTPException(404, "Scholarship not found")
    try:
        a = upsert_application(db, user, sid, status=body.status, notes=body.notes, next_action=body.next_action, next_action_date=body.next_action_date)
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return application_to_dict(a, s["name"])


@app.delete("/api/applications/{sid}")
def remove_application(sid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not delete_application(db, user, sid):
        raise HTTPException(404, "Not in your tracker")
    db.commit()
    return {"ok": True}


@app.get("/api/memory")
def get_memory(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Everything the assistant remembers: profile notes and past-chat episodes."""
    return {"notes": list(user.profile.notes or []) if user.profile else [], "episodes": recent_episodes(db, user, limit=20)}


@app.post("/api/memory/refresh")
def refresh_memory(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Write summaries for chats that changed since they were last summarized."""
    return {"summarized": refresh_stale_episodes(db, user)}


# ---------- email drafts (human approval required) ----------

def _own_draft(db: Session, user: User, did: int) -> EmailDraft:
    d = db.get(EmailDraft, did)
    if not d or d.user_id != user.id:
        raise HTTPException(404, "Draft not found")
    return d


@app.get("/api/drafts")
def list_drafts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(EmailDraft).where(EmailDraft.user_id == user.id).order_by(EmailDraft.created_at.desc()))
    return {"smtp_enabled": smtp_configured(), "drafts": [_draft_out(d) for d in rows]}


@app.put("/api/drafts/{did}")
def edit_draft(did: int, body: DraftEditIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _own_draft(db, user, did)
    if d.status != "pending":
        raise HTTPException(409, "Only pending drafts can be edited")
    for k in ("to_address", "subject", "body"):
        v = getattr(body, k)
        if v is not None:
            setattr(d, k, (v.strip() or None) if k == "to_address" else v)
    db.commit()
    return _draft_out(d)


@app.post("/api/drafts/{did}/approve")
def approve_draft(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The student's explicit approval. Sends via SMTP if configured, otherwise returns a mailto link."""
    d = _own_draft(db, user, did)
    if d.status != "pending":
        raise HTTPException(409, f"Draft is already {d.status}")
    d.approved_at = datetime.now(timezone.utc)
    if smtp_configured() and d.to_address:
        try:
            send_draft(d, reply_to=user.email)
        except Exception as e:
            raise HTTPException(502, f"Sending failed: {e}")
        d.status = "sent"
    else:
        d.status = "approved"
    db.commit()
    return _draft_out(d)


@app.post("/api/drafts/{did}/discard")
def discard_draft(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _own_draft(db, user, did)
    d.status = "discarded"
    db.commit()
    return _draft_out(d)


# ---------- reviewer (admin) ----------

@app.get("/api/admin/reviews")
def list_reviews(status: str = "pending", _: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ChangeReview).where(ChangeReview.status == status).order_by(ChangeReview.created_at.desc()))
    return [
        {"id": r.id, "scholarship_id": r.scholarship_id, "url": r.url, "diff": r.diff, "summary": r.summary,
         "proposed_patch": r.proposed_patch, "status": r.status, "created_at": r.created_at}
        for r in rows
    ]


@app.post("/api/admin/reviews/{rid}/approve")
def approve(rid: int, body: ReviewDecisionIn, reviewer: User = Depends(admin_user), db: Session = Depends(get_db)):
    r = db.get(ChangeReview, rid)
    if not r or r.status != "pending":
        raise HTTPException(404, "Pending review not found")
    try:
        approve_review(db, r, reviewer.id, body.patch, body.note)
    except KeyError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/reviews/{rid}/reject")
def reject(rid: int, body: ReviewDecisionIn, reviewer: User = Depends(admin_user), db: Session = Depends(get_db)):
    r = db.get(ChangeReview, rid)
    if not r or r.status != "pending":
        raise HTTPException(404, "Pending review not found")
    r.status, r.reviewer_id, r.reviewer_note, r.reviewed_at = "rejected", reviewer.id, body.note, datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/scholarships/{sid}/verify")
def mark_verified(sid: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    row = db.get(Scholarship, sid)
    if not row:
        raise HTTPException(404, "Scholarship not found")
    row.verification_status = "verified"
    row.last_verified = datetime.now(timezone.utc).date().isoformat()
    db.commit()
    return {"ok": True}


@app.post("/api/admin/monitor/run")
def monitor_now(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return run_monitor(db)


# ---------- frontend ----------

app.mount("/static", StaticFiles(directory=settings.web_dir), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(settings.web_dir / "index.html")
