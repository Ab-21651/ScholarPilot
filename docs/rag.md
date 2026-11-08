# Retrieval (RAG): verified process guides as a tree

## What the agent knows, and where it lives

| Knowledge | Where | How the agent reads it |
|---|---|---|
| Scholarships (rules, deadlines, documents) | `data/scholarships.json` → database | Catalog tools + rule engine |
| Processes: passport, FRC, attestation, apostille, English tests, proof of funds, visas | `data/guides/<area>/<guide>.md` | `browse_guides`, `search_guides` |

The AI never browses the internet while answering. Official websites are used only:
1. by us, when writing and re-checking the guides, and
2. by the daily monitor, which flags changes on official pages for a human reviewer.

## Why vectorless (tree) RAG instead of vector RAG

- **The knowledge is small and structured:** 20 guides and 90 sections in 5 areas (identity,
  education, language, money, visa). We wrote each guide ourselves from official sources, so we
  control the structure.
- **Navigation by reasoning:** the model reads a short table of contents (areas → guides → sections)
  and opens the section it needs, like a person using a book's contents page. No embedding model or
  vector database is needed.
- **Keyword fallback:** `search_guides` ranks all sections with BM25 for when the model doesn't know
  which branch holds the answer. The best match comes back in full with its sources.
- **Traceable:** every answer can point to one section, one official source and a "checked" date.

## Sources

Every fact comes from the official page listed in the guide's front matter (DGI&P, NADRA, HEC, IBCC,
MOFA, HCCH, gov.uk, German Missions in Pakistan, Embassy of Italy, ER.GO, EDISU, OIF Hungary,
Stipendium Hungaricum, Embassy of Japan, Home Affairs Australia, IDP IELTS, Pearson PTE). Sites that
block automated reading were read in a real browser. Where an official page shows a fee only as an
image or loads it by script (passport fast track, IELTS, PTE, NADRA FRC), the guide links to the page
instead of stating a number.

Checking at the source changed several facts that older or second-hand sources get wrong:
- HEC attestation is fully online at Rs 3,000 per document; photocopies are no longer attested.
- MOFA moved to a Digital Attestation System with e-certificates on 12 October 2026.
- The apostille fee for educational documents is Rs 3,000 (revised 8 July 2024); the Hague Conference
  site still shows an older Rs 500 figure.
- Australia does not accept at-home online English tests.

## Retrieval check

`tests/test_guides.py` has 12 real student questions, each with the section that should answer it.

| Setting | Right section ranked 1st | Right section in top 3 |
|---|---|---|
| BM25, guide title ×2, no stemming (used) | 11/12 | 12/12 |
| BM25, guide title ×1, no stemming | 10/12 | 12/12 |
| BM25 with plural or truncation stemming | 8-9/12 | 12/12 |

Stemming made ranking worse on these short, precise texts, so it is off.

## Grounding self-check

A mid-size model at low reasoning effort sometimes adds "helpful" details from memory, such as a photo
size, an extra step or a test name. In live tests this happened in about 2 of 5 guide answers. So when
an answer used the guides, a second model call (`agent/grounding.py`):
1. splits the draft into claims,
2. requires an exact supporting sentence from the retrieved guide text for each claim, and
3. rewrites the answer without unsupported claims, using "check the official page" where needed.

This reduces hallucination a lot but cannot remove it completely. Students are always given the
official link and the date the guide was checked.

## Keeping it current

- `checked:` in each guide is the date it was last verified.
- Sources that allow automated reading are watched by the daily monitor (`monitor: true`). A change
  creates a review in the Review tab pointing to the guide file to re-check.
- HEC, MOFA, NADRA, Home Affairs and the Embassy of Japan block automated reading (`monitor: false`)
  and need a periodic manual check.
