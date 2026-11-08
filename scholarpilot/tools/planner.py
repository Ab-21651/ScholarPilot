"""Gap analysis and deadline timeline.

Turns eligibility results into a to-do list ("you still need IELTS 6.5") and
works back from each deadline to milestone dates.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .eligibility import EligibilityResult, next_deadline

# How many days before the deadline each preparation step should be finished.
LEAD_TIMES = {
    "book_english_test": 70,
    "take_english_test": 42,
    "attestation": 56,
    "request_references": 42,
    "draft_essays": 56,
    "final_review": 14,
    "submit": 3,
}


def find_gaps(result: EligibilityResult) -> list[dict[str, Any]]:
    gaps = []
    for c in result.criteria:
        if c.status == "fail":
            gaps.append({"type": c.name, "action": c.detail, "fixable": c.fixable})
        elif c.status == "unknown":
            gaps.append({"type": c.name, "action": c.detail, "fixable": True, "missing_info": True})
    return gaps


def _milestone(deadline: date, key: str, title: str, today: date) -> dict[str, Any]:
    due = deadline - timedelta(days=LEAD_TIMES[key])
    status = "overdue" if due < today else ("this_week" if (due - today).days <= 7 else "upcoming")
    return {"date": due.isoformat(), "title": title, "status": status}


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)


def build_timeline(
    scholarship: dict[str, Any],
    result: EligibilityResult,
    today: date | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    track = scholarship["tracks"].get(result.level)
    if not track:
        return {"scholarship_id": scholarship["id"], "error": f"No {result.level}'s track."}
    dl_info = track.get("deadline")
    deadline = next_deadline(dl_info, today)
    milestones: list[dict[str, Any]] = []
    warnings: list[str] = []

    if deadline:
        failed = {c.name for c in result.criteria if c.status == "fail"}
        if "English" in failed:
            milestones.append(_milestone(deadline, "book_english_test", "Book your English test (IELTS/TOEFL/PTE)", today))
            milestones.append(_milestone(deadline, "take_english_test", "Take the English test so results arrive in time", today))
        milestones.append(_milestone(deadline, "draft_essays", "Draft motivation letter / essays", today))
        milestones.append(_milestone(deadline, "attestation", "Get degree and transcripts attested (HEC / IBCC)", today))
        milestones.append(_milestone(deadline, "request_references", "Ask referees for recommendation letters", today))
        milestones.append(_milestone(deadline, "final_review", "Final review of all documents", today))
        milestones.append(_milestone(deadline, "submit", "Submit the application (leave a few days' buffer)", today))
        milestones.append({"date": deadline.isoformat(), "title": "Deadline", "status": "deadline"})
        milestones.sort(key=lambda m: m["date"])

        if "work experience" in failed:
            need = track["eligibility"].get("min_work_experience_months") or 0
            have = int((profile or {}).get("work_experience_months") or 0)
            by_deadline = have + max(0, _months_between(today, deadline))
            if profile is not None and by_deadline >= need:
                warnings.append(
                    f"If you keep working full-time, you'll have about {by_deadline} months of experience by this deadline "
                    f"({need} needed), so you can aim for this cycle."
                )
            else:
                warnings.append("You won't have enough work experience by this deadline. Plan for a later cycle.")
        if any(m["status"] == "overdue" for m in milestones):
            days_left = (deadline - today).days
            warnings.append(f"Only {days_left} days left. Some steps are already late; consider the next cycle if you can't catch up.")
        if dl_info and dl_info.get("approximate"):
            warnings.append("This deadline is approximate. Confirm the exact date on the official page: " + scholarship["official_url"])
    else:
        note = (dl_info or {}).get("note") or ""
        warnings.append(f"There is no single deadline: each university or course sets its own. {note}".strip())

    return {
        "scholarship_id": scholarship["id"],
        "scholarship_name": scholarship["name"],
        "level": result.level,
        "deadline": deadline.isoformat() if deadline else None,
        "deadline_note": (dl_info or {}).get("note"),
        "verdict": result.verdict,
        "gaps": find_gaps(result),
        "documents": track.get("documents", []),
        "steps": track.get("steps", []),
        "milestones": milestones,
        "warnings": warnings,
        "official_url": scholarship["official_url"],
        "apply_url": scholarship.get("apply_url", scholarship["official_url"]),
    }


def merge_timelines(timelines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Single chronological list across several scholarships."""
    events = []
    for t in timelines:
        for m in t.get("milestones", []):
            events.append({**m, "scholarship_id": t["scholarship_id"], "scholarship_name": t["scholarship_name"]})
    return sorted(events, key=lambda e: e["date"])
