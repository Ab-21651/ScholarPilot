# Memory in ScholarPilot

The agent uses four kinds of memory.

| Type | What it holds | Where it lives | How the agent uses it |
|---|---|---|---|
| Working | The current conversation, including tool calls and results | `conversations.messages` | Sent with every request in that chat |
| Semantic (student) | Facts about the student: grades, tests, age, experience, preferences, free-text notes | `profiles` table | `get_profile` / `update_profile` tools |
| Semantic (world) | Scholarship rules, documents, deadlines, official links | `scholarships` table (seeded from `data/scholarships.json`) | Catalog tools |
| Episodic | What happened in past chats: actions taken, summary, decisions, next steps | `episodes` table | Latest 3 are loaded into the system prompt; `recall_past_sessions` searches older ones |
| Episodic (structured) | The student's applications: status, history, next action | `applications` table | `update_application` / `list_applications`, also loaded into the system prompt |
| Procedural | How to do the job | System prompt and the tools' code | Always present |

## Extracting facts from text

There is no separate extraction model. The LLM reads what the student wrote and calls
`update_profile` with structured values, e.g. `{"ielts": 6.5, "cgpa": 3.4}`.
`profiles.py` validates every value (type, range, allowed field) before saving and
returns errors to the model so it can correct itself or ask the student.

## How an episode is built

1. **Events (automatic, no LLM).** After every tool call, the runner records a short line,
   e.g. `2026-11-07 checked eligibility: chevening=not_eligible`, plus the scholarship ids
   involved. This always works, even if summarization fails.
2. **Summary (LLM, lazy).** The next time the student sends a message in *another* chat,
   any chat that changed since it was last summarized gets a summary, decisions and next
   steps (at most two per turn, to limit cost). If no model is available, a summary is
   built from the events instead. Students can also press "Update summaries" on the Profile page.

## What the model sees at the start of a chat

```
System prompt (rules)
You are talking with <name>, ... master's scholarships ...

Earlier chats with this student (most recent first):
- 2026-11-07: Checked Stipendium Hungaricum; eligible. Decided: apply. To do: HEC attestation.

Applications the student is tracking:
- Stipendium Hungaricum [stipendium-hungaricum]: preparing; next: HEC attestation by 2026-12-01
```

## Privacy

Deleting a chat also deletes its episode. Profile, notes, tracker and past sessions
are all visible to the student on the Profile and Tracker pages.

## Known limits

- A very long single chat keeps its full history; nothing compacts it yet.
- Episode search is keyword-based, not semantic. That will be revisited in the RAG step.
