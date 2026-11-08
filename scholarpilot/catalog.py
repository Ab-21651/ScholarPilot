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


RETIRED = "retired"


def _load() -> dict[str, Any]:
    with open(settings.catalog_path, encoding="utf-8") as f:
        return json.load(f)


def load_seed_file() -> list[dict[str, Any]]:
    return _load()["scholarships"]


def retired_entries() -> list[dict[str, Any]]:
    return _load().get("_meta", {}).get("retired", [])


def seed_catalog(db: Session) -> int:
    """Sync the seed file into the database. Returns how many entries were added or refreshed.

    - New entries are inserted. Entries with a `checked` date start as verified.
    - An existing row is refreshed when the seed was checked more recently than the row
      (so re-verified data reaches databases created earlier). Rows a reviewer verified
      later than the seed are left alone.
    - Scholarships listed under `_meta.retired` are marked retired and hidden from search.
    """
    rows = {r.id: r for r in db.scalars(select(Scholarship))}
    changed = 0
    for entry in load_seed_file():
        checked = entry.get("checked")
        status = "verified" if checked else "needs_review"
        row = rows.get(entry["id"])
        if row is None:
            db.add(Scholarship(id=entry["id"], data=entry, verification_status=status, last_verified=checked))
            changed += 1
        elif checked and (row.last_verified is None or checked > row.last_verified):
            row.data, row.verification_status, row.last_verified = entry, status, checked
            changed += 1
    for old in retired_entries():
        row = rows.get(old["id"])
        if row is not None and row.verification_status != RETIRED:
            row.verification_status = RETIRED
            row.data = {**row.data, "retired_reason": old.get("reason"), "retired_source": old.get("source")}
            changed += 1
    return changed


def _with_status(row: Scholarship) -> dict[str, Any]:
    data = dict(row.data)
    data["verification_status"] = row.verification_status
    data["last_verified"] = row.last_verified
    return data


def all_scholarships(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(Scholarship).where(Scholarship.verification_status != RETIRED).order_by(Scholarship.id))
    return [_with_status(r) for r in rows]


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
        if dl.get("status"):
            out["deadline_status"] = dl["status"]
    return out


def _deadline_text(dl: dict[str, Any]) -> str:
    if not dl:
        return "unknown"
    if not dl.get("month") or not dl.get("day"):
        return "varies by course"
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    text = f"{dl['day']} {months[dl['month'] - 1]}"
    return f"around {text}" if dl.get("approximate") else text


def official_domains(db: Session) -> set[str]:
    domains: set[str] = set()
    for s in all_scholarships(db):
        domains.update(d.lower() for d in s.get("official_domains", []))
    return domains
