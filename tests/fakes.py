"""A scripted stand-in for the OpenAI-compatible (Groq) chat client."""

import copy
import json
from types import SimpleNamespace


def text(content):
    """A final answer."""
    return (content, [], "stop")


def call(id_, name, args, content=""):
    """A single tool call (GPT-OSS on Groq makes one call per step)."""
    return (content, [(id_, name, args)], "tool_calls")


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # snapshot: the runner keeps appending to the same list
        content, tool_calls, finish = self.script.pop(0)
        calls = [
            SimpleNamespace(id=i, type="function", function=SimpleNamespace(name=n, arguments=a if isinstance(a, str) else json.dumps(a)))
            for i, n, a in tool_calls
        ]
        message = SimpleNamespace(content=content, tool_calls=calls or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def fake_client(script):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(script)))
