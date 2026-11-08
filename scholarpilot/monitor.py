"""Daily monitor for official scholarship pages.

For each monitored URL it fetches the page, extracts readable text and
compares it with the last snapshot. When the page changes, it creates a
ChangeReview with the diff and, if an API key is configured, an AI summary
plus a proposed catalog patch. Nothing in the catalog changes until a human
reviewer approves the review.

Run once from the command line:   py -m scholarpilot.monitor
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, guides
from .config import ROOT_DIR
from .llm import complete_json
from .models import ChangeReview, PageSnapshot, Scholarship

log = logging.getLogger(__name__)
USER_AGENT = "ScholarPilotMonitor/0.1 (student project; checks official scholarship pages once a day)"
MAX_DIFF_CHARS = 8000
GUIDE_PREFIX = "guide:"


# ---------- fetching ----------

def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    lines = []
    for line in soup.get_text("\n").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) > 2:
            lines.append(line)
    return "\n".join(lines)


def fetch_text(url: str, client: httpx.Client) -> str:
    resp = client.get(url)
    resp.raise_for_status()
    return extract_text(resp.text)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_diff(old: str, new: str) -> str:
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "previous", "current", lineterm="", n=1)
    out = "\n".join(diff)
    return out if len(out) <= MAX_DIFF_CHARS else out[:MAX_DIFF_CHARS] + "\n... (diff truncated)"


# ---------- AI analysis (optional) ----------

class FieldChange(BaseModel):
    path: str
    new_value: str
    evidence: str


class ChangeAnalysis(BaseModel):
    relevant: bool
    summary: str
    changes: list[FieldChange]


ANALYSIS_PROMPT = """You review changes on an official scholarship web page for a catalog used by Pakistani students.

Current catalog entry (JSON):
{entry}

Diff of the page text (lines starting with - were removed, + were added):
{diff}

Decide whether the change affects anything students rely on (deadlines, eligibility, required tests or scores, age limits, funding, documents, whether Pakistan is eligible). Ignore cosmetic or navigation changes.

Write a short summary for the human reviewer. Then propose catalog changes only when the diff clearly supports them, using dotted paths that already exist in the entry, for example "tracks.master.deadline.month" or "tracks.bachelor.eligibility.english.accepted.ielts". Quote the supporting text in evidence. If nothing should change, return an empty list.

Reply with only a JSON object: {{"relevant": true/false, "summary": "...", "changes": [{{"path": "...", "new_value": "...", "evidence": "..."}}]}}"""


def analyse_change(entry: dict[str, Any], diff: str) -> ChangeAnalysis | None:
    trimmed = {k: v for k, v in entry.items() if k not in {"verification_status", "last_verified"}}
    data = complete_json(
        "You review changes on official scholarship pages. Reply with JSON only.",
        ANALYSIS_PROMPT.format(entry=json.dumps(trimmed), diff=diff),
        max_tokens=3000,
    )
    if data is None:
        return None
    try:
        return ChangeAnalysis.model_validate(data)
    except ValidationError:
        log.warning("Change analysis returned an unexpected shape: %s", data)
        return None


# ---------- patching ----------

def _coerce(value: Any, current: Any) -> Any:
    if isinstance(value, str):
        if isinstance(current, bool):
            return value.strip().lower() in {"true", "yes", "1"}
        if isinstance(current, int) and not isinstance(current, bool):
            return int(float(value))
        if isinstance(current, float):
            return float(value)
        if isinstance(current, list):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
            return [v.strip() for v in value.split(",") if v.strip()]
    return value


def apply_patch(data: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply {"dotted.path": value} changes to an existing entry. Only existing paths can change."""
    updated = copy.deepcopy(data)
    for path, value in patch.items():
        parts = path.split(".")
        node = updated
        for p in parts[:-1]:
            if not isinstance(node, dict) or p not in node:
                raise KeyError(f"Path '{path}' does not exist in the entry")
            node = node[p]
        last = parts[-1]
        if not isinstance(node, dict) or last not in node:
            raise KeyError(f"Path '{path}' does not exist in the entry")
        node[last] = _coerce(value, node[last])
    return updated


# ---------- main run ----------

def check_url(db: Session, scholarship: dict[str, Any], url: str, client: httpx.Client) -> str:
    sid = scholarship["id"]
    try:
        text = fetch_text(url, client)
    except httpx.HTTPError as e:
        note = f"FETCH ERROR: {e}"
        duplicate = db.scalar(
            select(ChangeReview).where(ChangeReview.url == url, ChangeReview.status == "pending", ChangeReview.diff == note)
        )
        if not duplicate:
            db.add(ChangeReview(scholarship_id=sid, url=url, diff=note, summary="The official page could not be fetched. The link may have moved; please check it.", proposed_patch={}))
        return "error"

    new_hash = text_hash(text)
    last = db.scalar(
        select(PageSnapshot).where(PageSnapshot.url == url, PageSnapshot.scholarship_id == sid).order_by(PageSnapshot.fetched_at.desc())
    )
    if last is None:
        db.add(PageSnapshot(scholarship_id=sid, url=url, content_hash=new_hash, text=text))
        db.flush()
        return "baseline"
    if last.content_hash == new_hash:
        last.fetched_at = datetime.now(timezone.utc)
        return "unchanged"

    diff = make_diff(last.text, text)
    if sid.startswith(GUIDE_PREFIX):
        # Guides are hand-written files: the reviewer re-checks the file, there is no automatic patch.
        guide_file = scholarship.get("file", "")
        review = ChangeReview(
            scholarship_id=sid,
            url=url,
            diff=diff,
            summary=f"An official source of the guide '{scholarship.get('title')}' changed. Re-check {guide_file} against this page, update it if needed, and set its 'checked' date.",
            proposed_patch={},
        )
        db.add(review)
        db.add(PageSnapshot(scholarship_id=sid, url=url, content_hash=new_hash, text=text))
        db.flush()
        return "changed"
    analysis = analyse_change(scholarship, diff)
    review = ChangeReview(
        scholarship_id=sid,
        url=url,
        diff=diff,
        summary=analysis.summary if analysis else "Page changed. No AI summary (API key not set); please read the diff.",
        proposed_patch={c.path: c.new_value for c in analysis.changes} if analysis and analysis.relevant else {},
    )
    db.add(review)
    db.add(PageSnapshot(scholarship_id=sid, url=url, content_hash=new_hash, text=text))
    db.flush()
    return "changed"


def run_monitor(db: Session) -> dict[str, int]:
    counts = {"baseline": 0, "unchanged": 0, "changed": 0, "error": 0}
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for s in catalog.all_scholarships(db):
            for url in s.get("monitor_urls", []):
                result = check_url(db, s, url, client)
                counts[result] += 1
                db.commit()
        all_guides = guides.load_guides()
        for gid, url in guides.monitored_sources():
            g = all_guides[gid]
            entry = {"id": f"{GUIDE_PREFIX}{gid}", "title": g.title, "file": str(g.path.relative_to(ROOT_DIR)).replace("\\", "/")}
            result = check_url(db, entry, url, client)
            counts[result] += 1
            db.commit()
    return counts


def approve_review(db: Session, review: ChangeReview, reviewer_id: int, patch: dict[str, Any] | None, note: str | None) -> Scholarship | None:
    if review.scholarship_id.startswith(GUIDE_PREFIX):
        # Guide files are edited by hand; approving just records that the change was reviewed.
        review.status = "approved"
        review.reviewer_id = reviewer_id
        review.reviewer_note = note
        review.reviewed_at = datetime.now(timezone.utc)
        return None
    row = db.get(Scholarship, review.scholarship_id)
    if row is None:
        raise KeyError("Scholarship no longer exists")
    final_patch = review.proposed_patch if patch is None else patch
    if final_patch:
        row.data = apply_patch(row.data, final_patch)
    row.verification_status = "verified"
    row.last_verified = date.today().isoformat()
    review.proposed_patch = final_patch
    review.status = "approved"
    review.reviewer_id = reviewer_id
    review.reviewer_note = note
    review.reviewed_at = datetime.now(timezone.utc)
    return row


def main() -> None:
    from .db import init_db, session_scope

    logging.basicConfig(level=logging.INFO)
    init_db()
    with session_scope() as db:
        counts = run_monitor(db)
    print("Monitor finished:", counts)


if __name__ == "__main__":
    main()
