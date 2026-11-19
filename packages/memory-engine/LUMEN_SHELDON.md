# Lumen + Sheldon — Vision, Problem, Product, Build Plan

> **Tagline:** LLMs generate intelligence. Lumen supplies continuity.
> **Thesis:** Memory → Context → Intelligence.

- **Lumen** = memory/intelligence engine (backend, API-first, no UI).
- **Sheldon** = user-facing workspace built on Lumen (memory-first, chat as accessory).

---

## 1. What problem does this solve?

Today's AI is powerful but amnesiac.

Your brain lives in 10 places — ChatGPT chats, Claude chats, Slack, Notion, Google Docs, PDFs, browser tabs, meeting notes, GitHub, email, your own head — but every AI tool sees only a fraction.

So every new chat starts with:

> "Here is my brain again. Let me explain everything from scratch."

Result: brilliant models giving generic answers because they lack your context.

**Core equation:** `Output quality = Model capability × Context quality`

We don't compete on "smarter model." We compete on **"right context at the right time, for any model."** Memory becomes the durable asset; the LLM becomes replaceable.

---

## 2. The pain — are we solving real user pain?

| User pain today | How it feels | What Lumen+Sheldon does |
|---|---|---|
| Re-explaining project every chat | "Orbit is a dev tool, launching Oct, $29/mo…" pasted 20 times | Dump once → `current_view(Orbit)` always fresh |
| ChatGPT remembers, Claude doesn't | Two half-brains, neither complete | One cross-tool brain via Context API |
| Old decisions haunt you | Sept launch still cited after Oct pivot | Temporal state: Sept `superseded`, Oct `active`, history kept |
| Contradictions unresolved | 3 memories say Sept / Oct / indefinite | Authority policy + Review Queue: explicit winner, never silent overwrite |
| RAG returns 10 chunks | Keyword soup, no judgment | Decision context: goal + current + feedback + constraints + conflicts, assembled |
| No delete/trust | "Where did this come from? Can I delete it?" | Source link + confidence + cascade delete on every memory |
| Chat-first apps bury knowledge | Great answer lost in scrollback | Sheldon home = Search + Collections + Knowledge; chat is just `[Ask Brain]` |

If you have ever re-pasted the same background into a new LLM chat, this is your pain.

---

## 3. What does this tool do?

**In one line:** Dump anything → it becomes typed, versioned memory → any model queries it for grounded answers.

**Dump side (holder):**
Paste text, upload file, later connect Slack/Notion/GitHub/Gmail. Lumen preserves raw (immutable), extracts primitives, dedupes, versions, links.

**Recall side (user):**
Ask in Sheldon (`Ask Brain`), or from Slack/IDE/Custom GPT via API. Lumen builds `RELEVANT CONTEXT` (Recent / Related / Trusted / Conflicts) and the LLM answers from that.

**Concrete example:**
- Ingest: "Launch Orbit Sept" → later "actually Oct" → Sept auto-superseded.
- Ask: "Should we change pricing?" → context with $29 current, 500-user goal, 3 users OK, 2 want free tier, constraint "no enterprise until PMF" → grounded recommendation with citations.

---

## 4. Features (full list)

### Lumen (engine)
1. **Capture API** — `ingest(text, origin)` → raw immutable + extract + canonicalize + link.
2. **Primitives** — fact, decision, goal, assumption, question, insight, constraint, action, relationship, preference + confidence + entities.
3. **Dedupe** — `ingest_key` hash, re-ingest → `duplicate`.
4. **Temporal core** — `active | superseded | archived`, `valid_from/to`, `supersedes_id`. `get_active()` vs `get_history()`.
5. **Authority policy** — score = 0.5*recency + 0.3*source_trust + 0.2*confidence; margin >0.15 auto-supersede else `needs_review`.
6. **Capped graph** — edges `supersedes/contradicts/supports/derived_from/related_to`, conf ≥0.6, max 8/memory.
7. **Context Engine** — `assemble_context(query)` → Trusted/Recent/Related/Conflicts → formatted block for LLM.
8. **Trust + privacy** — source link, confidence, `delete_memory` cascades edges.
9. **Context API** — `ingest / ask_context / current_view / history` (FastAPI wrapper in Phase 1, keys + tenants in Phase 2).

### Sheldon (workspace)
10. **Memory-first home** — Search + Collections (Orbit, Personal, Research) + Knowledge graph; pins.
11. **[Ask Brain]** — single chat box, answers with citations from Lumen context.
12. **Review Queue** — confirm ambiguous conflicts; history timeline per entity.
13. **Source view** — origin, confidence bar, superseded banners, export/delete.

---

## 5. How are we going to build it?

**Stack (v0 done, stdlib-only):**
`lumen/{models,store,extract,canonicalize,link,recall,api}.py` + SQLite (`raw, memories, edges`) + `demo.py` + 3 unit tests green. No deps.

**v1 additions:**
- LLM extractor (`extract_with_llm` hook) + embeddings (sqlite-vec → pgvector).
- FastAPI over `api.py`; FTS5 + vector hybrid search.
- Sheldon read-only web UI (search, collections, Ask, Review Queue).
- Eval harness: supersede accuracy, dedupe rate, context relevance.

**Principles:**
- Raw never edited; canonical only transitions via policy.
- Never silent overwrite; ambiguous = user confirm.
- Graph capped; search token→vector; idempotent ingest.
- Lumen never imports Sheldon; Sheldon only calls Lumen API.

Repo map:
```
lumen/         — engine (this is the moat)
  models.py    — Memory(id,type,content,entities,confidence,status,source,trust,valid_*,supersedes_id,ingest_key)
  store.py     — SQLite raw/memories/edges, token-OR search, edge cap
  extract.py   — rules now, LLM later
  canonicalize.py — authority policy + same_topic (entity + word-overlap≥2)
  link.py      — thresholded related edges
  recall.py    — Recent/Related/Trusted/Conflicts from real supersedes edges
  api.py       — ingest/ask_context/current_view/history
demo.py, tests/, BUILD_PLAN.md
sheldon/ (Phase 1) — UI only
```

---

## 6. Phases

**Phase 0 — Core loop (DONE):** schema, store, rules extractor, authority canonicalize, capped link, recall, demo + tests.
Exit: `python3 demo.py` shows Sept→Oct supersede + pricing context.

**Phase 1 — Usable brain (next, 2–3 weeks):**
LLM extraction, embeddings, FastAPI, Sheldon read-only (search/collections/Ask/Review), eval harness.
Exit: user dumps 50 notes, Ask answers with citations, Review Queue handles 5 conflicts.

**Phase 2 — Connected (1–2 months):**
Notion/Slack/GitHub/file connectors, ChatGPT export import, Custom GPT Action on `/context`, multi-user + keys, browser quick-capture.
Exit: daily driver; ChatGPT + Claude both pull from Lumen.

**Phase 3 — Platform / company-brain:**
Team spaces, trust policies per source, IDE + Slack bots, usage-billed Context API.
Exit: team onboarded; external agent ships on Lumen API. Stack becomes `App → Agent → Context → Memory → LLM → Tools`.

---

## 7. Text deck (pitch in 10 slides)

1. **Title:** Lumen — the memory layer AI lacks. Sheldon — your second brain that never forgets what changed.
2. **Problem:** Intelligence is powerful, context is fragmented. Every chat starts from zero.
3. **Insight:** Output = Model × Context. Better context beats bigger model.
4. **Solution:** Dump anything → typed, temporal memory → decision context for any model.
5. **How:** Raw (immutable) → Canonical (scored, versioned) → Graph (related) → Context Engine (what matters now?) → LLM.
6. **Demo:** Sept→Oct supersede; "change pricing?" returns goals + price + feedback + conflicts — not 10 chunks.
7. **Why not RAG/Notion/ChatGPT memory:** RAG = top-K chunks; Notion = no reasoning; ChatGPT memory = locked, flat, opaque. Lumen = typed + temporal + portable + API.
8. **Product:** Sheldon home = knowledge, not chat. Collections, graph, Ask Brain, Review Queue.
9. **Vision:** From personal brain → Context API → company-brain. Memory as infrastructure layer.
10. **Ask:** Phase 1 build → 10 design partners dumping daily → measure: no-re-explain rate, supersede accuracy, Ask citation rate.

---

## 8. Glossary

- **Raw memory:** what was actually said (immutable).
- **Canonical memory:** structured, scored, versioned reuse unit.
- **Abstract knowledge:** entity graph (Orbit → launch → Oct).
- **Context Engine:** picks what the model needs right now.
- **Authority policy:** who wins on conflict (score + user confirm).

