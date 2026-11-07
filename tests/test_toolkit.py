"""Tool hierarchy: groups, routing, open_tools."""

from fastapi.testclient import TestClient

from scholarpilot.agent import runner, toolkit
from scholarpilot.agent.context import estimate_tokens
from scholarpilot.agent.prompts import SYSTEM_PROMPT
from scholarpilot.agent.tools import TOOLS
from scholarpilot.main import app

from .fakes import call, fake_client, text


def names(tools):
    return [t["function"]["name"] for t in tools]


def test_every_tool_is_in_exactly_one_group():
    grouped = [t for g in toolkit.GROUPS.values() for t in g["tools"]]
    assert sorted(grouped) == sorted(t["name"] for t in TOOLS)
    assert len(grouped) == len(set(grouped))


def test_tool_map_is_in_system_prompt():
    for group, g in toolkit.GROUPS.items():
        assert f"- {group} (" in SYSTEM_PROMPT
        for t in g["tools"]:
            assert t in SYSTEM_PROMPT


def test_routing():
    assert toolkit.route("Is this real? https://daad-pk.xyz pay Rs 5000") >= {"safety"}
    assert toolkit.route("My IELTS is 6.5 and CGPA 3.4") == {"student", "scholarships", "planning"}
    assert "outreach" in toolkit.route("Can you write an email to DAAD?")
    assert toolkit.route("hello") == set(toolkit.DEFAULT_GROUPS)
    assert "planning" in toolkit.route("What am I missing for Chevening?")


def test_subset_is_smaller_and_has_open_tools():
    sub = toolkit.tools_for({"safety"})
    assert names(sub) == ["check_scam", "open_tools"]
    full = toolkit.tools_for(set(toolkit.ALL_GROUPS))
    assert "open_tools" not in names(full)
    assert estimate_tokens(toolkit.tools_for(set(toolkit.DEFAULT_GROUPS))) < estimate_tokens(full) * 0.7


def test_open_groups_handles_unknown():
    loaded = {"safety"}
    r = toolkit.open_groups({"groups": ["planning", "nope"]}, loaded)
    assert loaded == {"safety", "planning"}
    assert r["loaded"] == {"planning": ["check_eligibility", "plan_application"]}
    assert r["unknown_groups"] == ["nope"]


def test_agent_opens_a_group_and_uses_it(monkeypatch):
    fake = fake_client([
        call("o1", "open_tools", {"groups": ["outreach"]}),
        call("d1", "draft_email", {"to_office": "DAAD Islamabad", "subject": "Question", "body": "Dear DAAD, ..."}),
        text("Draft saved for your approval."),
    ])
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={"email": "tk@example.com", "password": "password123", "full_name": "Sana Mir", "target_level": "master"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        cid = c.post("/api/conversations", headers=h).json()["id"]
        out = c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "hello"}).json()

    calls = fake.chat.completions.calls
    assert "draft_email" not in names(calls[0]["tools"]) and "open_tools" in names(calls[0]["tools"])
    assert "draft_email" in names(calls[1]["tools"])
    assert [t["tool"] for t in out["tool_calls"]] == ["open_tools", "draft_email"]
