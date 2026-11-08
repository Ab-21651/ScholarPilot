# Process guides (knowledge base)

These guides are the agent's knowledge base for processes around a scholarship
application: identity documents, document attestation, language tests, proof of
money and visas. The agent navigates them as a tree:

```
area (folder)  →  guide (file)  →  section (## heading)  →  subsection (### heading)
```

## Rules for editing

1. **Official sources only.** Every fact must come from the official page listed in
   the guide's `sources`. Do not add facts from memory, blogs or agents.
2. **If an official page doesn't say it, don't write it.** Write "check on the official
   page" and link it instead, e.g. for fees shown only as images or loaded by scripts.
3. **Update `checked`** (YYYY-MM-DD) whenever you re-verify a guide against its sources.
4. **`monitor: true`** only for sources the daily monitor can fetch. Sites that block
   automated reading (HEC, MOFA, NADRA, Home Affairs, the Embassy of Japan) are set to
   `false` and need a manual check.

## Front matter

```
---
id: short-kebab-id
title: Human title
checked: 2026-11-08
countries: all | uk | germany | ...
sources:
  - https://official.page/one
monitor: true
---
```
