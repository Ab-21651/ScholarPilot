"""Reading and updating the student profile (the agent's long-term memory)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from .models import Profile, User

EDITABLE_FIELDS: dict[str, type] = {
    "nationality": str,
    "date_of_birth": str,
    "city": str,
    "current_education": str,
    "field_of_study": str,
    "intended_field": str,
    "grade_percent": float,
    "cgpa": float,
    "cgpa_scale": float,
    "graduation_year": int,
    "ielts": float,
    "toefl_ibt": int,
    "pte": int,
    "duolingo": int,
    "sat": int,
    "gre": int,
    "jlpt": str,
    "work_experience_months": int,
    "preferred_countries": list,
    "financial_need": bool,
}

RANGES = {
    "grade_percent": (0, 100),
    "cgpa": (0, 5),
    "cgpa_scale": (1, 5),
    "graduation_year": (1980, 2040),
    "ielts": (0, 9),
    "toefl_ibt": (0, 120),
    "pte": (10, 90),
    "duolingo": (10, 160),
    "sat": (400, 1600),
    "gre": (260, 340),
    "work_experience_months": (0, 600),
}


def get_or_create_profile(db: Session, user: User) -> Profile:
    if user.profile is None:
        user.profile = Profile(user_id=user.id, preferred_countries=[], notes=[])
        db.add(user.profile)
        db.flush()
    return user.profile


def profile_to_dict(user: User) -> dict[str, Any]:
    p = user.profile
    data: dict[str, Any] = {"full_name": user.full_name, "target_level": user.target_level, "role": user.role}
    if p is None:
        return data
    for name in EDITABLE_FIELDS:
        data[name] = getattr(p, name)
    data["notes"] = list(p.notes or [])
    if p.date_of_birth:
        try:
            dob = date.fromisoformat(p.date_of_birth)
            today = date.today()
            data["age"] = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        except ValueError:
            pass
    return data


def _coerce(name: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    kind = EDITABLE_FIELDS[name]
    if kind is bool:
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1", "y"}
        return bool(value)
    if kind is list:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return list(value)
    if name == "date_of_birth":
        date.fromisoformat(str(value))  # validates format
        return str(value)
    if name == "jlpt":
        v = str(value).upper().strip()
        if v not in {"N1", "N2", "N3", "N4", "N5"}:
            raise ValueError("jlpt must be N1-N5")
        return v
    coerced = kind(value)
    if name in RANGES:
        lo, hi = RANGES[name]
        if not lo <= coerced <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}")
    return coerced


def update_profile(db: Session, user: User, updates: dict[str, Any], note: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Apply validated updates. Returns (changed fields, errors)."""
    profile = get_or_create_profile(db, user)
    changed: dict[str, Any] = {}
    errors: list[str] = []
    for name, value in updates.items():
        if name not in EDITABLE_FIELDS:
            errors.append(f"Unknown field '{name}'")
            continue
        try:
            coerced = _coerce(name, value)
        except (TypeError, ValueError) as e:
            errors.append(f"{name}: {e}")
            continue
        setattr(profile, name, coerced)
        changed[name] = coerced
    if note:
        notes = list(profile.notes or [])
        if note not in notes:
            notes.append(note)
            profile.notes = notes[-30:]
            changed["note"] = note
    db.flush()
    return changed, errors
