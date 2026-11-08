"""Process guides: a tree-shaped knowledge base the agent navigates (vectorless RAG).

The guides live in data/guides/<area>/<guide>.md. Each file has front matter
(id, title, checked date, official sources) and is split into sections by its
"##" headings and subsections by "###" headings.

    root
    ├── area (folder)            e.g. education
    │   └── guide (file)         e.g. hec-degree-attestation
    │       └── section (##)     e.g. hec-degree-attestation/fee
    │           └── subsection   e.g. passport/documents-you-need/adults-18-and-over

The agent retrieves in two ways:
  browse(path)  walk the tree like a table of contents (main method)
  search(query) keyword search over every section (fallback when it doesn't
                know where to look)
No embeddings or vector database are needed: the tree is small and the model
picks branches by reading titles.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

GUIDES_DIR = ROOT_DIR / "data" / "guides"

AREAS: dict[str, str] = {
    "identity": "Passport, family registration certificate (FRC), police certificate",
    "education": "Degree and certificate attestation: HEC, IBCC, MOFA, apostille, O/A Level equivalence",
    "language": "English tests (IELTS, PTE) and which tests visas accept",
    "money": "Proof of funds: blocked account, UK/Australia amounts, Hungary means, Italian DSU income documents",
    "visa": "Student visa and residence permit steps for the UK, Germany, Italy, Hungary, Japan and Australia",
}


@dataclass
class Section:
    id: str  # guide-id/section-slug[/subsection-slug]
    title: str
    text: str
    children: list["Section"] = field(default_factory=list)


@dataclass
class Guide:
    id: str
    area: str
    title: str
    checked: str
    countries: str
    sources: list[str]
    monitor: bool
    intro: str
    sections: list[Section]
    path: Path


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _parse_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        raise ValueError("missing front matter")
    _, fm, body = raw.split("---", 2)
    meta: dict[str, Any] = {}
    current_list: str | None = None
    for line in fm.strip().splitlines():
        if line.startswith("  - ") and current_list:
            meta[current_list].append(line[4:].strip())
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value == "":
            meta[key] = []
            current_list = key
        else:
            meta[key] = value
            current_list = None
    return meta, body.strip()


def parse_guide(path: Path, area: str) -> Guide:
    meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    gid = meta["id"]
    intro_lines: list[str] = []
    sections: list[Section] = []
    current: Section | None = None
    sub: Section | None = None
    for line in body.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            title = line[3:].strip()
            current = Section(id=f"{gid}/{slug(title)}", title=title, text="")
            sections.append(current)
            sub = None
        elif line.startswith("### ") and current is not None:
            title = line[4:].strip()
            sub = Section(id=f"{current.id}/{slug(title)}", title=title, text="")
            current.children.append(sub)
        elif sub is not None:
            sub.text += line + "\n"
        elif current is not None:
            current.text += line + "\n"
        else:
            intro_lines.append(line)
    for s in sections:
        s.text = s.text.strip()
        for c in s.children:
            c.text = c.text.strip()
    return Guide(
        id=gid,
        area=area,
        title=meta["title"],
        checked=meta["checked"],
        countries=meta.get("countries", "all"),
        sources=list(meta.get("sources", [])),
        monitor=str(meta.get("monitor", "false")).lower() == "true",
        intro="\n".join(intro_lines).strip(),
        sections=sections,
        path=path,
    )


@lru_cache(maxsize=1)
def load_guides() -> dict[str, Guide]:
    guides: dict[str, Guide] = {}
    for area in AREAS:
        folder = GUIDES_DIR / area
        for path in sorted(folder.glob("*.md")):
            g = parse_guide(path, area)
            if g.id in guides:
                raise ValueError(f"duplicate guide id {g.id}")
            guides[g.id] = g
    return guides


def _all_sections(g: Guide):
    for s in g.sections:
        yield s
        yield from s.children


def find_section(section_id: str) -> tuple[Guide, Section] | None:
    gid = section_id.split("/", 1)[0]
    g = load_guides().get(gid)
    if not g:
        return None
    for s in _all_sections(g):
        if s.id == section_id:
            return g, s
    return None


def _section_full_text(s: Section) -> str:
    parts = [s.text] + [f"### {c.title}\n{c.text}" for c in s.children]
    return "\n\n".join(p for p in parts if p).strip()


# ---------------- browse (tree navigation) ----------------

def browse(path: str | None = None) -> dict[str, Any]:
    """Navigate the tree. No path: areas. Area: its guides. Guide: its outline. Section: its text."""
    guides = load_guides()
    path = (path or "").strip().strip("/")
    if not path:
        return {
            "areas": [
                {"path": a, "about": about, "guides": sum(1 for g in guides.values() if g.area == a)}
                for a, about in AREAS.items()
            ],
            "next": "browse an area, e.g. path='education'",
        }
    if path in AREAS:
        return {
            "area": path,
            "guides": [{"path": g.id, "title": g.title, "countries": g.countries} for g in guides.values() if g.area == path],
            "next": "browse a guide to see its sections",
        }
    if path in guides:
        g = guides[path]
        return {
            "guide": g.id,
            "title": g.title,
            "summary": g.intro,
            "checked": g.checked,
            "sections": [
                {"path": s.id, "title": s.title, **({"subsections": [c.title for c in s.children]} if s.children else {})}
                for s in g.sections
            ],
            "next": "browse a section path to read it",
        }
    found = find_section(path)
    if found:
        g, s = found
        return {
            "guide": g.title,
            "section": s.title,
            "text": _section_full_text(s),
            "checked": g.checked,
            "sources": g.sources,
        }
    raise ValueError(f"No guide path '{path}'. Browse with no path to see the areas.")


# ---------------- search (keyword fallback) ----------------

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are", "it", "be", "you", "your", "i", "my",
    "what", "how", "do", "does", "can", "with", "from", "at", "by", "this", "that", "need", "get", "me", "much",
}


def _tokens(text: str) -> list[str]:
    # No stemming: on these short, precise guides it ranked worse (see docs/rag.md).
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@lru_cache(maxsize=1)
def _index() -> tuple[list[tuple[str, list[str]]], dict[str, float], float]:
    docs = []
    for g in load_guides().values():
        for s in g.sections:
            # Title words are repeated so they weigh more than body words.
            sub_titles = " ".join(c.title for c in s.children)
            text = f"{g.title} {g.title} {s.title} {s.title} {sub_titles} {_section_full_text(s)}"
            docs.append((s.id, _tokens(text)))
    n = len(docs)
    df: Counter[str] = Counter()
    for _, toks in docs:
        df.update(set(toks))
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    avg = sum(len(t) for _, t in docs) / max(n, 1)
    return docs, idf, avg


def search(query: str, area: str | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """BM25 keyword ranking over all sections."""
    docs, idf, avg = _index()
    q = _tokens(query)
    if not q:
        return []
    k1, b = 1.5, 0.75
    scored = []
    for sid, toks in docs:
        g = load_guides()[sid.split("/", 1)[0]]
        if area and g.area != area:
            continue
        tf = Counter(toks)
        score = 0.0
        for t in q:
            if t in tf:
                score += idf.get(t, 0) * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / avg))
        if score > 0:
            scored.append((score, sid))
    scored.sort(reverse=True)
    out = []
    for rank, (score, sid) in enumerate(scored[:limit]):
        g, s = find_section(sid)
        text = _section_full_text(s)
        hit: dict[str, Any] = {"path": sid, "guide": g.title, "section": s.title, "score": round(score, 2)}
        if rank == 0:
            # The best match comes back in full, with its sources, so the model answers
            # from the real text instead of a preview.
            hit.update({"text": text, "checked": g.checked, "sources": g.sources})
        else:
            hit["snippet"] = text[:200] + ("..." if len(text) > 200 else "")
        out.append(hit)
    return out


def monitored_sources() -> list[tuple[str, str]]:
    """(guide id, url) pairs the daily monitor should watch."""
    return [(g.id, url) for g in load_guides().values() if g.monitor for url in g.sources]
