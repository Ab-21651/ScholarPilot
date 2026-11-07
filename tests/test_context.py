"""Context window management: rolling summary, recent turns, token budget."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from scholarpilot.agent import context as ctxmod
from scholarpilot.agent import runner
from scholarpilot.agent.tools import openai_tools
from scholarpilot.main import app

from .fakes import call, fake_client, text


def convo(summary=None, upto=0):
    return SimpleNamespace(summary=summary, summary_upto=upto)


def turn(i, tool_output="x" * 50):
    return [
        {"role": "user", "content": f"question {i}"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "check_eligibility", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": tool_output},
        {"role": "assistant", "content": f"answer {i}"},
    ]


def test_compaction_waits_for_margin_then_keeps_recent_turns(monkeypatch):
    seen = {}

    def fake_json(system, prompt, **kw):
        seen["prompt"] = prompt
        return {"summary": "Student asked questions 1-3."}

    monkeypatch.setattr(ctxmod, "complete_json", fake_json)
    c = convo()
    history = sum((turn(i) for i in range(1, 6)), [])  # 5 turns: under keep(3) + margin(2), no compaction
    ctxmod.maybe_compact(c, history)
    assert c.summary is None and c.summary_upto == 0

    history += turn(6)  # 6 turns: fold the oldest 3
    ctxmod.maybe_compact(c, history)
    assert c.summary == "Student asked questions 1-3."
    assert history[c.summary_upto]["content"] == "question 4"
    assert "question 1" in seen["prompt"] and "question 4" not in seen["prompt"]


def test_compaction_falls_back_without_llm(monkeypatch):
    monkeypatch.setattr(ctxmod, "complete_json", lambda *a, **k: None)
    c = convo()
    history = sum((turn(i) for i in range(1, 7)), [])
    ctxmod.maybe_compact(c, history)
    assert c.summary.startswith("Student said: question 1")


def test_old_tool_outputs_are_shortened_but_current_turn_is_not():
    history = turn(1, "A" * 2000) + turn(2, "B" * 2000)[:3]
    msgs = ctxmod.recent_messages(history, 0)
    old, current = msgs[2]["content"], msgs[-1]["content"]
    assert len(old) < 500 and "shortened" in old
    assert current == "B" * 2000


def test_fit_to_budget_shrinks_big_requests(monkeypatch):
    tools = openai_tools()
    head = [{"role": "system", "content": "s"}, {"role": "system", "content": "c"}]
    huge = head + turn(1, "Z" * 60000) + turn(2, "Y" * 30000)[:3]
    msgs, max_out = ctxmod.fit_to_budget(huge, tools)
    total = ctxmod.estimate_tokens(msgs) + ctxmod.estimate_tokens(tools) + max_out
    assert total <= 8000
    assert max_out >= ctxmod.MIN_OUTPUT_TOKENS
    assert msgs[-1]["role"] == "tool"  # current turn kept

    small = head + turn(1)
    msgs2, max_out2 = ctxmod.fit_to_budget(small, tools)
    assert msgs2 == small and max_out2 == ctxmod.settings.max_output_tokens


def test_request_layout_and_budget_in_agent(monkeypatch):
    fake = fake_client([call("t1", "update_profile", {"updates": {"ielts": 7}}), text("Saved.")])
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    with TestClient(app) as c:
        r = c.post("/api/auth/signup", json={"email": "ctx@example.com", "password": "password123", "full_name": "Zara Ali", "target_level": "master"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        cid = c.post("/api/conversations", headers=h).json()["id"]
        c.post(f"/api/conversations/{cid}/messages", headers=h, json={"text": "My IELTS is 7"})

    first, second = fake.chat.completions.calls
    # static system prompt first (cacheable), then the per-student context
    assert first["messages"][0]["content"] == second["messages"][0]["content"]
    assert "ielts" not in first["messages"][1]["content"]
    # the context is rebuilt after the profile was saved mid-turn
    assert '"ielts":7.0' in second["messages"][1]["content"]
    assert first["max_completion_tokens"] <= ctxmod.settings.max_output_tokens
    assert first["reasoning_effort"] == "low"
