# Fitting the agent into Groq's free tier

Groq's free tier for `openai/gpt-oss-120b` allows about **8,000 tokens per minute** and
**200,000 tokens per day**. Groq counts the reserved output (`max_completion_tokens`)
against the per-minute budget. An agent loop makes several model calls per user message,
and every call resends the system prompt, the tool definitions and the chat history.

## What is sent on each call now

| Part | Size | Notes |
|---|---|---|
| System prompt | ~450 tokens | Identical on every call, so Groq can cache it. Cached tokens don't count toward limits |
| Tool definitions | ~1,200 tokens | Short descriptions; no profile or tracker read tools, because that data is already in context |
| Context message | ~150-400 tokens | Student, compact profile (empty fields removed), recent episodes, tracker, rolling chat summary |
| Chat | last 3 user turns | Older turns are folded into a ≤120-word rolling summary. Tool outputs from earlier turns are cut to 400 characters |
| Output cap | ≤2,048 | Shrinks automatically when the input is large |

`agent/context.py` checks every request against the per-minute budget before sending it.
If a request is too big, it shortens tool outputs first, then drops the oldest turns.
The current turn is always kept.

## Benchmark: the same 6-turn chat, live on Groq

| | Before | After |
|---|---|---|
| Turns completed | 2 of 6 (turn 3 failed: "Requested 8146, limit 8000") | 6 of 6 |
| Model calls | 5 (for 2 turns) | 13 (for 6 turns) |
| Prompt tokens | 16,193 (for 2 turns) | 34,207 (for 6 turns) |
| Prompt tokens per call | ~3,200, growing each turn | ~2,600 on average, flat |
| "What's my IELTS?" (memory recall) | — | 1 call, 2,357 tokens |

Other changes that cut calls:
- `search_scholarships` returns each result's eligibility verdict, so a "what can I get?" question needs one tool call instead of two.
- The profile in the context message replaces a `get_profile` call at the start of every chat.
- Reasoning effort defaults to `low`. Reasoning tokens count as output tokens.

## Remaining limit

At about 8,000 tokens per minute, roughly 3 model calls fit in a minute. A busy turn
therefore waits for quota (the SDK retries 429s automatically), which shows up as
30-60 second replies. Step 4 adds a fallback model host for that case.
