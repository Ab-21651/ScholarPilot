"""End-to-end API tests, with a scripted fake model standing in for the LLM."""

import copy
from types import SimpleNamespace

from fastapi.testclient import TestClient

from scholarpilot import monitor
from scholarpilot.agent import runner
from scholarpilot.main import app
from scholarpilot.models import ChangeReview, Scholarship


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # snapshot: the runner keeps appending to the same list
        content, stop = self.script.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop)


class FakeClient:
    def __init__(self, script):
        self.beta = SimpleNamespace(messages=FakeMessages(script))


def tool_use(id_, name, args):
    return {"type": "tool_use", "id": id_, "name": name, "input": args}


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
        ([{"type": "text", "text": "Let me save that."}, tool_use("t1", "update_profile", {"updates": {"ielts": 6.0, "cgpa": 3.2}})], "tool_use"),
        ([tool_use("t2", "check_eligibility", {"scholarship_ids": ["erasmus-mundus"]})], "tool_use"),
        ([tool_use("t3", "draft_email", {"to_office": "Programme coordinator", "subject": "Question about IELTS", "body": "Dear coordinator, ..."})], "tool_use"),
        ([{"type": "text", "text": "You need IELTS 6.5 for Erasmus Mundus. I drafted an email for your approval."}], "end_turn"),
    ]
    fake = FakeClient(script)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        h = signup(c, "sara@example.com")
        cid = c.post("/api/conversations", headers=h).json()["id"]
        r = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "My IELTS is 6 and CGPA 3.2. Can I get Erasmus?"}).json()
        assert "IELTS 6.5" in r["reply"]
        assert [t["tool"] for t in r["tool_calls"]] == ["update_profile", "check_eligibility", "draft_email"]
        assert '"not_eligible"' in r["tool_calls"][1]["result"]

        # memory: the profile was saved
        assert c.get("/api/profile", headers=h).json()["ielts"] == 6.0

        # the tool results were sent back to the model with matching ids
        second_call = fake.beta.messages.calls[1]["messages"]
        result_block = second_call[-1]["content"][0]
        assert second_call[-1]["role"] == "user"
        assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == "t1"

        # the draft waits for approval and is not sent by the agent
        drafts = c.get("/api/drafts", headers=h).json()["drafts"]
        assert drafts[0]["status"] == "pending"
        approved = c.post(f"/api/drafts/{drafts[0]['id']}/approve", headers=h).json()
        assert approved["status"] == "approved" and approved["mailto"].startswith("mailto:")

        history = c.get(f"/api/conversations/{cid}", headers=h).json()["messages"]
        assert history[0]["role"] == "user" and history[-1]["role"] == "assistant"


def test_tool_errors_are_returned_to_model(monkeypatch):
    script = [
        ([tool_use("t1", "get_scholarship", {"scholarship_id": "does-not-exist"})], "tool_use"),
        ([{"type": "text", "text": "That scholarship isn't in my catalog."}], "end_turn"),
    ]
    fake = FakeClient(script)
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        h = signup(c, "omar@example.com")
        cid = c.post("/api/conversations", headers=h).json()["id"]
        r = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "Tell me about Fulbright"}).json()
        assert r["tool_calls"][0]["is_error"] is True
        sent = fake.beta.messages.calls[1]["messages"][-1]["content"][0]
        assert sent["is_error"] is True


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
