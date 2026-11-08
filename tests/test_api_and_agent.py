"""End-to-end API tests, with a scripted fake model standing in for the LLM."""

from fastapi.testclient import TestClient

from scholarpilot import monitor
from scholarpilot.agent import runner
from scholarpilot.main import app
from scholarpilot.models import ChangeReview, Scholarship

from .fakes import call, fake_client, text


def signup(client, email, level="master"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "password123", "full_name": "Ayesha Khan", "target_level": level})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_signup_login_and_profile():
    with TestClient(app) as c:
        h = signup(c, "ayesha@example.com")
        assert c.post("/api/auth/signup", json={"email": "ayesha@example.com", "password": "password123", "full_name": "X Y", "target_level": "master"}).status_code == 409
        assert c.post("/api/auth/login", json={"email": "ayesha@example.com", "password": "wrong-pass"}).status_code == 401
        assert c.get("/api/me", headers=h).json()["target_level"] == "master"
        r = c.put("/api/profile", headers=h, json={"updates": {"cgpa": 3.5, "ielts": 7, "work_experience_months": 30}})
        assert r.status_code == 200 and r.json()["cgpa"] == 3.5
        assert c.put("/api/profile", headers=h, json={"updates": {"ielts": 12}}).status_code == 422
        matches = c.get("/api/matches", headers=h).json()
        assert matches[0]["verdict"] in {"eligible", "likely_eligible"}
        assert all(m["level"] == "master" for m in matches)
        assert c.get("/api/matches").status_code == 401


def test_bachelor_account_sees_only_bachelor_tracks():
    with TestClient(app) as c:
        h = signup(c, "bilal@example.com", level="bachelor")
        ids = {s["id"] for s in c.get("/api/scholarships", headers=h).json()}
        assert "stipendium-hungaricum" in ids and "chevening" not in ids


def test_agent_loop_uses_tools_and_remembers(monkeypatch):
    script = [
        call("t1", "update_profile", {"updates": {"ielts": 6.0, "cgpa": 3.2, "work_experience_months": 10}}, content="Let me save that."),
        call("t2", "check_eligibility", {"scholarship_ids": ["chevening"]}),
        call("t3", "draft_email", {"to_office": "Chevening Secretariat", "subject": "Question about work experience", "body": "Dear Chevening team, ..."}),
        text("Chevening needs 2 years (2,800 hours) of work experience. I drafted an email for your approval."),
    ]
    fake = fake_client(script)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        h = signup(c, "sara@example.com")
        cid = c.post("/api/conversations", headers=h).json()["id"]
        r = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "My IELTS is 6, CGPA 3.2 and I have 10 months of work. Can I get Chevening?"}).json()
        assert "2,800 hours" in r["reply"]
        assert [t["tool"] for t in r["tool_calls"]] == ["update_profile", "check_eligibility", "draft_email"]
        assert '"not_eligible"' in r["tool_calls"][1]["result"]

        # memory: the profile was saved
        assert c.get("/api/profile", headers=h).json()["ielts"] == 6.0

        # request shape: system prompt first, tools in function format, tool result sent back with the matching id
        first = fake.chat.completions.calls[0]
        assert first["messages"][0]["role"] == "system"
        assert first["tools"][0]["type"] == "function" and first["tool_choice"] == "auto"
        second = fake.chat.completions.calls[1]["messages"]
        assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "t1"
        assert second[-1] == {**second[-1], "role": "tool", "tool_call_id": "t1"}

        # the draft waits for approval and is not sent by the agent
        drafts = c.get("/api/drafts", headers=h).json()["drafts"]
        assert drafts[0]["status"] == "pending"
        approved = c.post(f"/api/drafts/{drafts[0]['id']}/approve", headers=h).json()
        assert approved["status"] == "approved" and approved["mailto"].startswith("mailto:")

        history = c.get(f"/api/conversations/{cid}", headers=h).json()["messages"]
        assert history[0]["role"] == "user" and history[-1]["role"] == "assistant"
        assert "Let me save that." in history[-1]["text"]


def test_tool_errors_are_returned_to_model(monkeypatch):
    script = [
        call("t1", "get_scholarship", {"scholarship_id": "does-not-exist"}),
        call("t2", "get_scholarship", "{not json"),
        text("That scholarship isn't in my catalog."),
    ]
    fake = fake_client(script)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        h = signup(c, "omar@example.com")
        cid = c.post("/api/conversations", headers=h).json()["id"]
        r = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "Tell me about Fulbright"}).json()
        assert [t["is_error"] for t in r["tool_calls"]] == [True, True]
        sent = fake.chat.completions.calls[1]["messages"][-1]
        assert sent["role"] == "tool" and "No scholarship" in sent["content"]
        assert "not valid JSON" in fake.chat.completions.calls[2]["messages"][-1]["content"]


def test_missing_key_gives_friendly_reply():
    with TestClient(app) as c:
        h = signup(c, "nokey@example.com")
        cid = c.post("/api/conversations", headers=h).json()["id"]
        r = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "hi"})
        assert r.status_code == 200 and "GROQ_API_KEY" in r.json()["reply"]


def test_old_block_format_history_is_converted():
    old = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Checking"}, {"type": "tool_use", "id": "x1", "name": "get_profile", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x1", "content": "{}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
    ]
    new = runner.normalize_history(old)
    assert [m["role"] for m in new] == ["user", "assistant", "tool", "assistant"]
    assert new[1]["tool_calls"][0]["function"]["name"] == "get_profile"
    assert new[2]["tool_call_id"] == "x1"


def test_monitor_review_flow(monkeypatch, db):
    pages = iter(["<html><body><p>Deadline: 7 October</p></body></html>", "<html><body><p>Deadline: 3 November</p></body></html>"])

    class FakeResp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class FakeHttp:
        def get(self, url):
            return FakeResp(next(pages))

    s = {"id": "chevening", **db.get(Scholarship, "chevening").data}
    assert monitor.check_url(db, s, "https://www.chevening.org/apply/", FakeHttp()) == "baseline"
    assert monitor.check_url(db, s, "https://www.chevening.org/apply/", FakeHttp()) == "changed"
    db.commit()
    review = db.query(ChangeReview).filter_by(scholarship_id="chevening", status="pending").first()
    assert "3 November" in review.diff

    with TestClient(app) as c:
        student = signup(c, "student2@example.com")
        assert c.get("/api/admin/reviews", headers=student).status_code == 403
        admin = signup(c, "admin@example.com")
        rows = c.get("/api/admin/reviews", headers=admin).json()
        rid = next(r["id"] for r in rows if r["scholarship_id"] == "chevening")
        bad = c.post(f"/api/admin/reviews/{rid}/approve", headers=admin, json={"patch": {"tracks.master.nope": 1}})
        assert bad.status_code == 422
        ok = c.post(f"/api/admin/reviews/{rid}/approve", headers=admin, json={"patch": {"tracks.master.deadline.month": "11", "tracks.master.deadline.day": "3"}})
        assert ok.status_code == 200
        detail = c.get("/api/scholarships/chevening", headers=admin).json()
        assert detail["tracks"]["master"]["deadline"]["month"] == 11
        assert detail["verification_status"] == "verified"
