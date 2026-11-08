"""Process guides: structure, sources, tree navigation, retrieval quality and agent use."""

import re
from datetime import date
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from scholarpilot import guides, monitor
from scholarpilot.agent import runner, toolkit
from scholarpilot.main import app
from scholarpilot.models import ChangeReview

from .fakes import call, fake_client, text

# Official domains each guide is allowed to cite.
OFFICIAL_HOSTS = (
    "gov.pk", "edu.pk", "hec.gov.pk", "nadra.gov.pk", "gov.uk", "diplo.de", "auswaertiges-amt.de", "study-in-germany.com",
    "esteri.it", "er-go.it", "edisu.piemonte.it", "oif.gov.hu", "stipendiumhungaricum.hu", "emb-japan.go.jp",
    "homeaffairs.gov.au", "ielts.idp.com", "pearsonpte.com", "hcch.net",
)


def test_every_guide_is_well_formed():
    gs = guides.load_guides()
    assert len(gs) >= 20
    assert {g.area for g in gs.values()} == set(guides.AREAS)
    for g in gs.values():
        assert re.fullmatch(r"[a-z0-9-]+", g.id), g.id
        assert date.fromisoformat(g.checked) <= date.today()
        assert g.sources, g.id
        for url in g.sources:
            host = urlparse(url).hostname or ""
            assert url.startswith("https://"), url
            assert any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS), f"{g.id}: {url} is not an official source"
        assert g.sections, g.id
        for s in g.sections:
            assert s.text or s.children, f"empty section {s.id}"


def test_section_ids_are_unique():
    ids = [s.id for g in guides.load_guides().values() for s in guides._all_sections(g)]
    assert len(ids) == len(set(ids))


def test_browse_walks_the_tree():
    root = guides.browse()
    assert [a["path"] for a in root["areas"]] == list(guides.AREAS)
    area = guides.browse("education")
    assert {"hec-degree-attestation", "ibcc", "mofa-attestation-apostille"} <= {g["path"] for g in area["guides"]}
    guide = guides.browse("hec-degree-attestation")
    fee = next(s for s in guide["sections"] if s["title"] == "Fee")
    leaf = guides.browse(fee["path"])
    assert "Rs 3,000 per document" in leaf["text"]
    assert leaf["sources"] and leaf["checked"] == "2026-11-08"
    with pytest.raises(ValueError):
        guides.browse("nope/nothing")


# Retrieval check: a question and the section that must be the top search hit.
RETRIEVAL_CASES = [
    ("how much is HEC degree attestation fee", "hec-degree-attestation/fee"),
    ("blocked account amount for germany", "germany-blocked-account/how-much"),
    ("TB test clinic in Lahore for UK visa", "uk-student-visa/tb-test-required-for-pakistan"),
    ("apostille fee for educational documents", "mofa-attestation-apostille/apostille-fees-revised-8-july-2024"),
    ("how long do IELTS results take", "ielts/results-and-validity"),
    ("passport urgent fee 72 pages", "passport/fees-ordinary-e-passport"),
    ("O level equivalence IBCC", "ibcc/o-level-and-a-level-equivalence"),
    ("does australia accept IELTS online at home", {"english-test-rules/australia-subclass-500", "ielts/which-test-is-accepted-where"}),
    ("UK student visa application fee", "uk-student-visa/costs"),
    ("japan visa VFS Gerry's", "japan-student-visa/where-to-apply"),
    ("documents for passport under 18 B-form", "passport/documents-you-need"),
    ("translate family income documents italy", "italy-dsu-income-documents/translation-into-italian"),
]


def _allowed(expected):
    return expected if isinstance(expected, set) else {expected}


@pytest.mark.parametrize("question,expected", RETRIEVAL_CASES)
def test_right_section_is_in_top_3(question, expected):
    # The agent receives the top 3 hits, so this is what matters most.
    hits = [h["path"] for h in guides.search(question)]
    assert set(hits) & _allowed(expected), hits


def test_right_section_is_usually_first():
    first = sum(1 for q, exp in RETRIEVAL_CASES if guides.search(q)[0]["path"] in _allowed(exp))
    assert first >= 11, f"top-1 accuracy {first}/{len(RETRIEVAL_CASES)}"


def test_routing_loads_guides_for_how_to_questions():
    assert "guides" in toolkit.route("How do I get my degree attested by HEC?")
    assert "guides" in toolkit.route("What is the blocked account amount?")
    assert "guides" not in toolkit.route("Which scholarships can I get?")


def test_agent_reads_a_guide(monkeypatch):
    fake = fake_client([
        call("g1", "search_guides", {"query": "HEC attestation fee"}),
        call("g2", "browse_guides", {"path": "hec-degree-attestation/fee"}),
        text("HEC charges Rs 3,000 per document. Source: hec.gov.pk, checked on 2026-11-08."),
    ])
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={"email": "guide@example.com", "password": "password123", "full_name": "Ali Khan", "target_level": "master"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        cid = c.post("/api/conversations", headers=h).json()["id"]
        out = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "How much does HEC attestation cost?"}).json()
        assert c.get("/api/guides/hec-degree-attestation", headers=h).status_code == 200
        assert c.get("/api/guides/not-a-guide", headers=h).status_code == 404

    first_tools = [t["function"]["name"] for t in fake.chat.completions.calls[0]["tools"]]
    assert "search_guides" in first_tools
    assert out["tool_calls"][0]["result"].startswith('{"results":[{"path":"hec-degree-attestation/fee"')
    assert "Rs 3,000" in out["tool_calls"][0]["result"]  # best hit comes back in full
    assert "Rs 3,000" in out["tool_calls"][1]["result"]


def test_monitor_flags_changed_guide_source(db):
    pages = iter(["<p>Fee Rs 3,000</p>", "<p>Fee Rs 4,000</p>"])

    class Resp:
        def __init__(self, t):
            self.text = t

        def raise_for_status(self):
            pass

    class Http:
        def get(self, url):
            return Resp(next(pages))

    entry = {"id": "guide:ibcc", "title": "IBCC", "file": "data/guides/education/ibcc.md"}
    url = "https://ibcc.edu.pk/attestation-application-process/"
    assert monitor.check_url(db, entry, url, Http()) == "baseline"
    assert monitor.check_url(db, entry, url, Http()) == "changed"
    db.commit()
    review = db.query(ChangeReview).filter_by(scholarship_id="guide:ibcc").first()
    assert "data/guides/education/ibcc.md" in review.summary and review.proposed_patch == {}
    assert monitor.approve_review(db, review, 1, None, "checked") is None
    assert review.status == "approved"


def test_only_reachable_sources_are_monitored():
    hosts = {urlparse(u).hostname for _, u in guides.monitored_sources()}
    for blocked in ("www.hec.gov.pk", "mofa.gov.pk", "www.nadra.gov.pk", "immi.homeaffairs.gov.au", "www.pk.emb-japan.go.jp"):
        assert blocked not in hosts


def _ask(monkeypatch, script, email, question):
    fake = fake_client(script)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={"email": email, "password": "password123", "full_name": "Ali Khan", "target_level": "master"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        cid = c.post("/api/conversations", headers=h).json()["id"]
        out = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": question}).json()
        history = c.get(f"/api/conversations/{cid}", headers=h).json()["messages"]
    return out, history


def test_grounding_check_removes_unsupported_claims(monkeypatch):
    from scholarpilot.agent import grounding

    seen = {}

    def fake_check(system, prompt, **kw):
        seen["prompt"] = prompt
        return {"answer": "Renew online at https://onlinemrp.dgip.gov.pk/renew-passport/ . Source: dgip.gov.pk, checked on 2026-11-08.",
                "removed": ["photo size 35x45 mm"]}

    monkeypatch.setattr(grounding, "complete_json", fake_check)
    out, history = _ask(monkeypatch, [
        call("s1", "search_guides", {"query": "passport renewal"}),
        text("Renew online. Upload a 35x45 mm photo. Source: dgip.gov.pk"),
    ], "ground1@example.com", "How do I renew my passport?")
    assert "35x45" not in out["reply"]
    assert out["grounding"] == {"checked": True, "removed": ["photo size 35x45 mm"]}
    assert "onlinemrp.dgip.gov.pk" in seen["prompt"]  # the retrieved guide text was the reference
    assert "35x45" not in history[-1]["text"]  # the saved chat holds the checked answer


def test_grounding_check_skipped_without_guides(monkeypatch):
    from scholarpilot.agent import grounding

    monkeypatch.setattr(grounding, "complete_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    out, _ = _ask(monkeypatch, [text("Hello! How can I help?")], "ground2@example.com", "hi")
    assert out["grounding"] == {}


def test_grounding_check_failure_keeps_draft(monkeypatch):
    from scholarpilot.agent import grounding

    monkeypatch.setattr(grounding, "complete_json", lambda *a, **k: None)
    out, _ = _ask(monkeypatch, [
        call("s1", "search_guides", {"query": "HEC fee"}),
        text("HEC charges Rs 3,000 per document."),
    ], "ground3@example.com", "HEC fee?")
    assert out["reply"] == "HEC charges Rs 3,000 per document."
    assert out["grounding"] == {"checked": False}
