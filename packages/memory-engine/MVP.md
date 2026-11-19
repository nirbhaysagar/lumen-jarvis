# MVP — Lumen + Sheldon (first shippable, 2–3 weeks)

> Goal: prove **dump once → never re-explain**. 1 user, 3 workspaces, 50 memories, Ask answers with proof.

## In scope (must work day 1)

1. **Dump box** — paste text into workspace (Orbit / Research / Personal). File upload (.md/.txt) if trivial.
2. **Auto-cards** — each dump becomes labeled cards (fact/decision/goal/…) + template + confidence. Rules engine now, LLM later if key available.
3. **Workspaces** — switcher; asking in one never polluted by others.
4. **Search + Home** — list cards per workspace, search box, pin 1–2 important.
5. **Ask Brain** — 1 box: question + workspace → answer pack (Recent/Trusted/Related/Conflicts with source labels). No streaming needed; render `RELEVANT CONTEXT` + LLM answer if key present, else context only.
6. **Review list** — ambiguous updates ("Oct vs Sept?") → Confirm / Keep both. Closes the trust loop.
7. **Edit-star** — edit card text → ★, ranks higher next ask.
8. **Source + delete** — each card shows origin; delete card works.

## Out of scope (explicitly NOT in MVP)

Chrome extension, Slack/Notion auto-sync, audio/images, team sharing, billing, graph visualization (list of links is enough), vector embeddings (keyword search ok), mobile app.

## User flows

- **Dump:** open Sheldon → pick workspace → paste → see 2–4 cards appear with labels.
- **Ask:** pick workspace → Ask "Should we change pricing?" → see pack: current $29, goal 500, 3 OK / 2 want free, rule cited → decide → new decision card saved.
- **Conflict:** dump "launch Nov" → Review shows "Nov vs Oct — Keep Nov?" → Confirm → Oct becomes history.
- **Teach:** fix a card typo → ★ → next Ask ranks it Trusted.

## Acceptance (ship only if all pass)

- 50 dumps across 3 workspaces, zero crashes.
- Sept→Oct→Nov chain: only Nov active, history shows all 3.
- Cross-workspace test: Research note never appears as authoritative in Orbit Ask.
- Re-paste same text → `duplicate`, no clone.
- Ask "pricing?" contains: $29 + 500-goal + feedback + constraint.
- 5 conflicts resolved via Review in <2 min each.
- `python3 -m unittest discover tests -v` green + new eval (4 checks above) green.

## Build tasks (in this repo)

1. `server.py` — stdlib `http.server` JSON API over `lumen/api.py`: `POST /ingest /enhance /chains`, `GET /context /view /history /search`. No FastAPI dep.
2. `sheldon/index.html` — single file, no build: workspace switcher, dump box, cards list, search, Ask box, Review list, edit-star, delete.
3. `tests/eval.py` — 4 acceptance checks as script (Sept→Oct→Nov, isolation, dedupe, pricing relevance).
4. ChatGPT export import — `import_chatgpt.py`: `conversations.json` → per-conversation `ingest(..., chain_id)`.
5. Polish demo: seed script with Orbit story so first open isn't empty.

## Metrics (week 1–2)

Dumps/day >5, saves kept (not deleted) >80%, Asks/day >3, Review cleared <48h, "had to re-explain?" = no.

## After MVP → Phase 2

Only then: Notion/Slack pull, copy-context button, Custom GPT Action, then extension (needs save-rate>30%, 7-day return>25%).

