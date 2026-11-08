# ScholarPilot: agentic design at a glance

An LLM agent that helps Pakistani students find funded bachelor's and master's scholarships abroad. It uses official sources only and warns about scams.

## 1. Agent type

| Aspect | ScholarPilot |
|---|---|
| Pattern | One agent, ReAct-style loop (reason → call tool → observe → repeat) |
| Model | `openai/gpt-oss-120b` on Groq (any OpenAI-compatible API can be swapped in) |
| Autonomy | The LLM chooses which tools to call, in what order, and when to stop (up to 15 steps per message) |
| Safety | Human approves anything that leaves the system or changes facts |
| Framework | None. A framework-free loop in plain Python (see section 8) |

## 2. Agent loop (`agent/runner.py`)

1. Load memory, then build the request (static system prompt + student context + recent chat).
2. A router loads only the tool groups this message needs.
3. Call the model, then run the tool calls it makes and send the results back.
4. Repeat until the model gives a final answer or reaches the step limit.
5. If guides were used, run the grounding self-check.
6. Save the chat and tool events (episodic memory).

Error handling covers malformed or misspelled tool calls (retried), rate limits, missing API key and network errors.

## 3. Memory

| Type | What it holds | Where |
|---|---|---|
| Working | Current chat: last 3 user turns kept word for word, older turns folded into a rolling summary | `agent/context.py` |
| Semantic | Long-term facts: student profile and notes, plus the scholarship catalog | `profiles.py`, `catalog.py` |
| Episodic | What happened before: a tool-event log, an LLM summary of each past chat, and the application tracker | `memory.py` |
| Procedural | How to act: system prompt, rules, tool schemas, tool map | `agent/prompts.py`, `agent/tools.py` |

## 4. Tools

11 tools in 6 groups, plus one meta-tool.

| Group | Tools |
|---|---|
| student | `update_profile`, `update_application`, `recall_past_sessions` |
| scholarships | `search_scholarships`, `get_scholarship` |
| planning | `check_eligibility`, `plan_application` |
| guides | `browse_guides`, `search_guides` |
| outreach | `draft_email` |
| safety | `check_scam` |
| meta | `open_tools`: the model loads extra groups when it needs them |

- **Tool hierarchy:** a keyword router loads only the relevant groups. A short tool map in the prompt lets the model navigate to the rest.
- **Deterministic tools for critical decisions:** eligibility (rule engine), timelines (planner) and scam signals are plain code. **The LLM never decides eligibility.** It explains the engine's verdict.

## 5. RAG (`guides.py`)

- **Knowledge base:** 20 process guides written from official sources, covering identity documents, education/attestation, language tests, proof of funds and visas.
- **Vectorless tree RAG:** the model browses area → guide → section like a book's table of contents. No embeddings or vector database.
- **Fallback:** BM25 keyword search across all sections, with the guide title weighted ×2.
- **Result:** 12/12 test questions find the right section in the top 3, and 11/12 rank it first.
- **Why vectorless:** the knowledge base is small and structured, and every answer must trace back to one section, one source and a "checked" date.

## 6. Grounding and accuracy

| Layer | What it does |
|---|---|
| Verified data | 21 scholarships and 20 guides, each checked against official pages, with `sources` and a `checked` date |
| Full-section retrieval | The best match is returned in full, with its source links |
| Prompt rules | Facts come only from tool results; the official link is always given |
| Grounding self-check | A second model call checks each claim against the retrieved text and removes unsupported claims |
| Honest fallback | "Check the official page" instead of guessing |

## 7. Human-in-the-loop

- **Emails:** the agent only drafts them. The student edits and approves, and nothing is sent automatically.
- **Catalog changes:** a daily monitor checks official pages and puts changes in a review queue. An admin approves or rejects each one.

## 8. Framework-free architecture

No LangChain, LlamaIndex or LangGraph: the loop, tools, memory and RAG are our own Python.

- **Full control of tokens:** this was needed to fit Groq's free tier (8K tokens per minute).
- **No hidden prompts:** every request is visible, which makes it easier to debug and explain.
- **Fully testable:** 77 tests run against a scripted fake model, using no API tokens.
- **Simple stack:** one agent doesn't need multi-agent orchestration such as LangGraph.

## 9. Token efficiency

- The system prompt is static, so the provider can cache it.
- A rolling chat summary keeps only the last 3 turns word for word.
- Old tool outputs are shortened to 400 characters.
- Tool outputs are compact, and search results are capped at 10.
- Each request is checked against the per-minute token budget before it is sent.
- Tool groups are loaded on demand.

## 10. Background agent (proactive part)

- **Daily monitor:** fetches official pages, compares them with the last copy, has the LLM summarise any change, and sends it to human review.

## 11. Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, SQLAlchemy (SQLite), APScheduler |
| LLM | Groq API (OpenAI SDK) |
| Frontend | Plain HTML/CSS/JS (Dashboard, Assistant, Matches, Plan, Tracker, Drafts, Scam check, Profile, Review) |
| Tests | pytest with a fake LLM |

## 12. Limits (honest)

- It mostly reacts to messages. Apart from the monitor, it doesn't act on its own.
- It is one agent on short tasks, not long-running goals.
- The free model can still hallucinate. The grounding layers reduce this but don't remove it.
