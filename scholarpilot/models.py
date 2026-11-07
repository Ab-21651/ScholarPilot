"""Database tables."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    # "bachelor" or "master": chosen at signup, it drives the whole experience
    target_level: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16), default="student")  # student | parent | counsellor
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class Profile(Base):
    """What the agent remembers about a student."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    nationality: Mapped[str] = mapped_column(String(64), default="Pakistan")
    date_of_birth: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Education so far
    current_education: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. FSc, A-Levels, BS
    field_of_study: Mapped[str | None] = mapped_column(String(128), nullable=True)
    intended_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    grade_percent: Mapped[float | None] = mapped_column(Float, nullable=True)  # school leavers: HSSC/FSc %
    cgpa: Mapped[float | None] = mapped_column(Float, nullable=True)  # graduates
    cgpa_scale: Mapped[float | None] = mapped_column(Float, nullable=True, default=4.0)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Tests
    ielts: Mapped[float | None] = mapped_column(Float, nullable=True)
    toefl_ibt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duolingo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gre: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jlpt: Mapped[str | None] = mapped_column(String(4), nullable=True)  # N1..N5

    work_experience_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_countries: Mapped[list] = mapped_column(JSON, default=list)
    financial_need: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Free-form facts the agent decided to remember (e.g. "wants a STEM course in English")
    notes: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    # Full API message history, stored exactly as exchanged with the model
    messages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Episode(Base):
    """Episodic memory: what happened in one conversation.

    `events` are recorded automatically from tool calls (no LLM needed).
    `summary`, `decisions` and `next_steps` are written by the LLM once the
    conversation is finished, so later chats can recall it.
    """

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), unique=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    scholarship_ids: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decisions: Mapped[list] = mapped_column(JSON, default=list)
    next_steps: Mapped[list] = mapped_column(JSON, default=list)
    # When the summary was last written; older than the conversation means it is stale.
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


APPLICATION_STATUSES = ("interested", "preparing", "applied", "interview", "accepted", "rejected", "withdrawn")


class Application(Base):
    """Application tracker: one row per scholarship the student is pursuing."""

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "scholarship_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scholarship_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="interested")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    next_action_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    # [{"status": "applied", "date": "2026-11-07"}, ...]
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Scholarship(Base):
    """Catalog entry. `data` follows the schema in data/scholarships.json."""

    __tablename__ = "scholarships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    verification_status: Mapped[str] = mapped_column(String(32), default="needs_review")
    last_verified: Mapped[str | None] = mapped_column(String(10), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EmailDraft(Base):
    """Emails the agent drafts. Nothing is sent until the student approves."""

    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scholarship_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_office: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | sent | discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PageSnapshot(Base):
    """Latest text of an official page, used to detect changes."""

    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scholarship_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(1024), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChangeReview(Base):
    """A detected change on an official page, waiting for a human reviewer."""

    __tablename__ = "change_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scholarship_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    diff: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_patch: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
