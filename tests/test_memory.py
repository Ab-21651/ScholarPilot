"""Episodic memory and application tracker."""

import pytest
from fastapi.testclient import TestClient

from scholarpilot import memory
from scholarpilot.agent import runner
from scholarpilot.main import app

from .fakes import call, fake_client, text


def signup(c, email, level="master"):
    r = c.post("/api/auth/signup", json={"email": email, "password": "password123", "full_name": "Hina Raza", "target_level": level})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_describe_event_lines():
    line, ids = memory.describe_event("check_eligibility", {}, {"results": [{"id": "chevening", "verdict": "not_eligible"}]})
    assert line == "checked eligibility: chevening=not_eligible" and ids == ["chevening"]
    line, _ = memory.describe_event("update_profile", {}, {"saved": {"ielts": 6.5}, "errors": []})
    assert line == "saved to profile: ielts=6.5"
    assert memory.describe_event("search_scholarships", {}, {"results": []}) == (None, [])
    assert memory.describe_event("check_eligibility", {}, {"error": "x"}) == (None, [])


def test_tracker_api_flow():
    with TestClient(app) as c:
        h = signup(c, "tracker@example.com")
        assert c.put("/api/applications/chevening", headers=h, json={"status": "interested"}).status_code == 200
        r = c.put("/api/applications/chevening", headers=h, json={"status": "applied", "next_action": "Prepare for interview", "next_action_date": "2027-02-01"})
        body = r.json()
        assert body["status"] == "applied"
        assert [x["status"] for x in body["history"]] == ["interested", "applied"]
        assert c.put("/api/applications/chevening", headers=h, json={"status": "maybe"}).status_code == 422
        assert c.put("/api/applications/chevening", headers=h, json={"next_action_date": "next week"}).status_code == 422
        assert c.put("/api/applications/not-a-scholarship", headers=h, json={}).status_code == 404
        apps = c.get("/api/applications", headers=h).json()
        assert len(apps) == 1 and apps[0]["scholarship_name"] == "Chevening Scholarships"
        assert c.delete("/api/applications/chevening", headers=h).status_code == 200
        assert c.get("/api/applications", headers=h).json() == []


def test_second_chat_remembers_first(monkeypatch):
    monkeypatch.setattr(
        memory,
        "complete_json",
        lambda system, prompt, **kw: {
            "summary": "Checked Stipendium Hungaricum; student is eligible and decided to apply.",
            "decisions": ["Apply to Stipendium Hungaricum"],
            "next_steps": ["Get transcripts attested by HEC"],
        },
    )
    first = fake_client([
        call("a1", "check_eligibility", {"scholarship_ids": ["stipendium-hungaricum"]}),
        call("a2", "update_application", {"scholarship_id": "stipendium-hungaricum", "status": "preparing", "next_action": "HEC attestation"}),
        text("You're eligible. I added it to your tracker."),
    ])
    second = fake_client([text("Welcome back! Last time you decided to apply to Stipendium Hungaricum.")])

    with TestClient(app) as c:
        h = signup(c, "episodic@example.com")
        c1 = c.post("/api/conversations", headers=h).json()["id"]
        monkeypatch.setattr(runner, "get_client", lambda: first)
        c.post(f"/api/conversations/{c1}/messages", headers=h, json={"text": "Can I get Stipendium Hungaricum?"})

        # events were recorded without any LLM call
        eps = c.get("/api/memory", headers=h).json()["episodes"]
        assert eps and "stipendium-hungaricum" in eps[0]["scholarship_ids"]

        c2 = c.post("/api/conversations", headers=h).json()["id"]
        monkeypatch.setattr(runner, "get_client", lambda: second)
        r = c.post(f"/api/conversations/{c2}/messages", headers=h, json={"text": "Hi again"}).json()
        assert "Welcome back" in r["reply"]

        # the first chat was summarized and loaded into the second chat's context
        context = second.chat.completions.calls[0]["messages"][1]["content"]
        assert "decided to apply" in context
        assert "Get transcripts attested by HEC" in context
        assert "stipendium-hungaricum" in context and "preparing" in context

        eps = c.get("/api/memory", headers=h).json()["episodes"]
        first_ep = next(e for e in eps if e["conversation_id"] == c1)
        assert first_ep["decisions"] == ["Apply to Stipendium Hungaricum"]

        # recall tool finds it by keyword
        third = fake_client([
            call("r1", "recall_past_sessions", {"query": "hungaricum"}),
            text("Found it."),
        ])
        monkeypatch.setattr(runner, "get_client", lambda: third)
        r = c.post(f"/api/conversations/{c2}/messages", headers=h, json={"text": "What did we decide before?"}).json()
        assert '"count":1' in r["tool_calls"][0]["result"]

        # deleting a chat forgets its episode
        c.delete(f"/api/conversations/{c1}", headers=h)
        assert all(e["conversation_id"] != c1 for e in c.get("/api/memory", headers=h).json()["episodes"])


def test_summary_falls_back_without_llm(monkeypatch, db):
    monkeypatch.setattr(memory, "complete_json", lambda *a, **k: None)
    from scholarpilot.models import Conversation, User

    user = db.query(User).first()
    conv = Conversation(user_id=user.id, messages=[{"role": "user", "content": "hi"}])
    db.add(conv)
    db.flush()
    memory.record_tool_events(db, conv, [("check_scam", {"text": "x"}, {"risk_level": "high", "signals": [1, 2]})])
    ep = memory.summarize_episode(db, conv)
    assert ep.summary.startswith("Actions: scam check: high risk")


def test_invalid_application_status_raises(db):
    from scholarpilot.models import User

    user = db.query(User).first()
    with pytest.raises(ValueError):
        memory.upsert_application(db, user, "chevening", status="pending")
