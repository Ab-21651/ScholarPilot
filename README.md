# ScholarPilot

An AI agent that guides Pakistani students to funded scholarships abroad for **bachelor's** and **master's** study. It uses official sources only and warns students about scams.

Students sign up as either a bachelor's or a master's applicant. The agent remembers their profile, checks eligibility rule by rule, explains every verdict, finds gaps ("you still need IELTS 6.5"), builds a deadline timeline, drafts emails that the student must approve, and checks pasted links or messages for scam signs. A daily monitor watches the official pages and queues every change for human review before the catalog is updated.

**Countries covered:** Germany, United Kingdom, Hungary, Italy, Erasmus Mundus (Europe-wide), Japan and Australia. PhD is out of scope for now.

## How the agent works

```
Student message
     │
     ▼
┌───────────────┐   tool calls    ┌──────────────────────────────────────────┐
│  LLM planner  │ ──────────────▶ │ get_profile / update_profile  (memory)   │
│  (agent loop) │                 │ search_scholarships / get_scholarship    │
│               │ ◀────────────── │ check_eligibility   (rule engine)        │
└───────────────┘   tool results  │ plan_application    (gaps + timeline)    │
     │                            │ draft_email         (needs approval)     │
     ▼                            │ check_scam          (scam signals)       │
 Answer with reasons              └──────────────────────────────────────────┘
 and official links                         │
                                            ▼
                                 Catalog (official sources only)
                                            ▲
                    Daily monitor → change review queue → human reviewer
```

Design choices:

- **The LLM never decides eligibility.** A deterministic rules engine ([scholarpilot/tools/eligibility.py](scholarpilot/tools/eligibility.py)) marks every rule pass, fail or unknown, and the agent explains the result. This makes verdicts testable and stops the model from inventing criteria.
- **Human in the loop twice.** Emails are only drafted; the student approves them in the Drafts tab. Catalog changes found by the monitor are only applied after a reviewer approves them.
- **Grounded answers.** All scholarship facts come from the catalog, which links to official pages only. Entries stay `needs_review` until a reviewer verifies them.
- **Tool hierarchy.** Tools are grouped (student, scholarships, planning, outreach, safety). A router loads only the groups a message needs, and the model can open others with `open_tools`. See [docs/tools.md](docs/tools.md).
- **Fits a free tier.** A rolling chat summary, compact tool outputs and a per-minute token budget keep requests small. See [docs/token-efficiency.md](docs/token-efficiency.md).
- **Four kinds of memory.** Working (the chat), semantic (profile and catalog), episodic (summaries of past chats plus the application tracker), and procedural (prompt and tools). See [docs/memory.md](docs/memory.md).

## Project layout

```
scholarpilot/
  agent/          agent loop, tool definitions, system prompt
  tools/          eligibility engine, gap/timeline planner, scam checker
  catalog.py      catalog loading and search
  memory.py       episodic memory (past-chat summaries) and application tracker
  monitor.py      daily official-page monitor and review queue
  profiles.py     student profile (long-term facts about the student)
  llm.py          one-off JSON calls to the model (e.g. chat summaries)
  main.py         FastAPI app (REST API and frontend)
  models.py       database tables
data/
  scholarships.json   seed catalog (15 scholarships, 6 countries + Erasmus Mundus)
web/              frontend (HTML/CSS/JS)
tests/            unit and end-to-end tests
```

## Setup (Windows)

```bash
py -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
py -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then set `GROQ_API_KEY`, a long random `JWT_SECRET`, and your email in `ADMIN_EMAILS` so your account gets reviewer access.

**Model:** the agent runs on `openai/gpt-oss-120b` through Groq's OpenAI-compatible API. Get a free key at https://console.groq.com/keys. Free-tier limits are per organization; check yours at https://console.groq.com/settings/limits. Any other OpenAI-compatible provider works by changing `LLM_BASE_URL`, `GROQ_API_KEY`/`LLM_API_KEY` and `LLM_MODEL`.

## Run

```bash
py -m uvicorn scholarpilot.main:app --reload
```

Open http://localhost:8000, sign up, and pick Bachelor's or Master's.

Run the official-page monitor once by hand. The first run stores baselines; later runs queue any changes in the Review tab:

```bash
py -m scholarpilot.monitor
```

To run it daily inside the server, set `MONITOR_ENABLED=true` and `MONITOR_HOUR` in `.env`.

## Tests

```bash
py -m pytest -q
```

The tests cover the catalog schema, the eligibility rules, the timeline, the scam checker, the API, the review flow, and the agent loop. The agent-loop tests use a scripted fake model, so they run without an API key.

## Catalog status

The seed catalog is a first draft of each scholarship's rules, documents and typical deadlines, with links to the official pages. It has not yet been checked line by line against those pages, and rules and dates change every year, so every entry starts as `needs_review` and the app tells students to confirm on the official page. Use the monitor and the Review tab, or `POST /api/admin/scholarships/{id}/verify`, to mark entries verified after checking them.

## Roadmap

- Train a scam classifier (TF-IDF baseline, then a fine-tuned multilingual transformer for English/Roman Urdu) and combine it with the rule-based checker
- Evaluation suite: eligibility accuracy on labelled profiles, extraction accuracy on official pages, end-to-end agent task success and hallucination rate, compared against an LLM-only baseline
- Ingest scholarship leads from WhatsApp channels as unverified leads that go through the review queue
