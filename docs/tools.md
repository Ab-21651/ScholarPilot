# Tool hierarchy

The agent's tools are grouped into a small tree. The model sees the whole tree as a
short map in the system prompt, but only the groups a request needs are sent with full
schemas. This makes the right tool easier to pick and keeps requests small as more
tools are added.

```
ScholarPilot tools
├── student        remember facts, track applications, recall earlier chats
│   ├── update_profile
│   ├── update_application
│   └── recall_past_sessions
├── scholarships   find scholarships with the student's verdict, read one in full
│   ├── search_scholarships
│   └── get_scholarship
├── planning       check eligibility in detail, gap lists and timelines
│   ├── check_eligibility
│   └── plan_application
├── outreach       draft emails, sent only after the student approves
│   └── draft_email
└── safety         scam checks for links and messages
    └── check_scam
```

## How a request is built (`agent/toolkit.py`)

1. **Route.** A keyword router reads the student's message and picks groups. It's free,
   with no extra model call. For example, a link or "pay a fee" loads `safety`, test scores
   or "my CGPA" load `student`, and "what am I missing" loads `planning`. If nothing matches,
   `scholarships` and `planning` are loaded.
2. **Send.** Only those groups' tool schemas are sent, plus `open_tools`.
3. **Navigate.** If the model needs a tool from another group, it calls
   `open_tools(groups=[...])`, and the group is available from the next step on.
4. **Stay loaded.** A group the model has used stays loaded for the rest of the turn.
5. **Fallback.** If the provider rejects a call to a tool that isn't loaded
   (`tool_use_failed`), the last retry sends every tool.

## Size

| Request | Tool tokens sent |
|---|---|
| All tools (flat list) | ~1,235 |
| Default (scholarships + planning) | ~600 |
| Scam check (safety) | ~206 |
| Map in the system prompt | ~195, always sent and cacheable |

On the 6-turn live benchmark, total tokens were about the same as with the flat list
(34.6K vs 34.2K). That chat touches almost every group, and changing tool sets get fewer
cache hits. The savings show up in focused requests, and they grow as tools are added.
