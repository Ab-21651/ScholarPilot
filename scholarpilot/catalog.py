"""Scholarship catalog: seeded from data/scholarships.json, stored in the database.

The database copy is the live catalog. Reviewers update it by approving
changes found by the monitor (see monitor.py).
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Scholarship

LEVELS = ("bachelor", "master")


def load_seed_file() -> list[dict[str, Any]]:
    with open(settings.catalog_path, encoding="utf-8") as f:
        return json.load(f)["scholarships"]


def seed_catalog(db: Session) -> int:
    """Insert seed entries that are not in the database yet. Returns how many were added."""
    existing = set(db.scalars(select(Scholarship.id)))
    added = 0
    for entry in load_seed_file():
        if entry["id"] not in existing:
            db.add(Scholarship(id=entry["id"], data=entry, verification_status="needs_review"))
            added += 1
    return added


def _with_status(row: Scholarship) -> dict[str, Any]:
    data = dict(row.data)
    data["verification_status"] = row.verification_status
    data["last_verified"] = row.last_verified
    return data


def all_scholarships(db: Session) -> list[dict[str, Any]]:
    return [_with_status(r) for r in db.scalars(select(Scholarship).order_by(Scholarship.id))]


def get_scholarship(db: Session, scholarship_id: str) -> dict[str, Any] | None:
    row = db.get(Scholarship, scholarship_id)
    return _with_status(row) if row else None


def search_scholarships(
    db: Session,
    level: str | None = None,
    country: str | None = None,
    field: str | None = None,
    funding: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    results = []
    for s in all_scholarships(db):
        if level and level not in s["tracks"]:
            continue
        if country and country.lower() not in s["country"].lower():
            continue
        if funding and s["funding"] != funding:
            continue
        if field:
            fields = [f.lower() for f in s.get("fields", [])]
            if "any" not in fields and not any(field.lower() in f or f in field.lower() for f in fields):
                continue
        if query:
            haystack = " ".join([s["name"], s["provider"], s["country"], s.get("funding_details", "")]).lower()
            if not all(word in haystack for word in query.lower().split()):
                continue
        results.append(s)
    return results


def summarize(s: dict[str, Any], level: str | None = None) -> dict[str, Any]:
    """Compact view used in lists and agent tool results."""
    out = {
        "id": s["id"],
        "name": s["name"],
        "country": s["country"],
        "funding": s["funding"],
        "levels": list(s["tracks"].keys()),
        "official_url": s["official_url"],
        "verification_status": s.get("verification_status"),
    }
    if level and level in s["tracks"]:
        dl = s["tracks"][level].get("deadline", {})
        out["typical_deadline"] = _deadline_text(dl)
    return out


def _deadline_text(dl: dict[str, Any]) -> str:
    if not dl:
        return "unknown"
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    text = f"{dl['day']} {months[dl['month'] - 1]}"
    return f"around {text}" if dl.get("approximate") else text


def official_domains(db: Session) -> set[str]:
    domains: set[str] = set()
    for s in all_scholarships(db):
        domains.update(d.lower() for d in s.get("official_domains", []))
    return domains
