# Lumen Memory Engine — Build Plan to State of the Art (v3, "ultimate" merge)

**For:** an IDE coding agent (Cursor or Claude Code) working in the Lumen monorepo.
**Written against:** commit `94370ca` (21 Nov 2026). If `HEAD` is newer, re-run the checks in §2 first and fix any line numbers that moved.
**v3 (23 Nov 2026):** this version merges the best parts of the earlier `nirbhaysagar/memory-engine` repo (commit `56a43f5`) into the Lumen engine. See §2.5 for the merge map. The Lumen monorepo stays the only codebase.
**Scope:** `packages/memory-engine` (the product), `evals/memory`, plus the small parts of `apps/lumen-core` that call it (`src/memory_client/client.py`, `src/persona/jarvis.py`, `src/vault/*`).

**Contents:** 0 Instructions · 1 Full vision (1.1 capabilities · 1.2 Obsidian experience · 1.3 launch criteria · **1.4 the 5-stage save pipeline and 8 aspects**) · 2 Ground truth · **2.5 Merge map (old repo + Lumen)** · 3 Checklist · 4 Milestones (Phase 0 M0.0–M0.8 → **S1** → M0.9 · 0.5 Evals · 0.6 Rerank + **K1 context pack** · 1 Viewer · 2 Forget/Correct + **D1 directives** · 2.5 MCP + **X1 export/import** + **I1 ingest** · 2.7 Obsidian/Graph · 3 Lanes/Sessions/Consolidation/Query) · 5 Out of scope · 6 Definition of done · 7 Appendix

---

## 0. Instructions for the IDE agent (read first, follow every time)

1. **Work one milestone at a time, in the order of §4.** Do not start a milestone until the one before it is green. **Never skip Phase 0.** Every later score is fake until it is done.
2. **Before each milestone:** read the files it names, and confirm the line references still match. If they don't, find the code by its function name.
3. **After each milestone:**
   - Run the milestone's tests, then the whole suite:
     `PYTHONPATH=packages/memory-engine .venv/bin/pytest packages/memory-engine/tests -q`
   - From Phase 0.5 on, also run the eval gate: `.venv/bin/python evals/memory/run.py --gate`.
   - Paste the real output into the commit message body. Never type in numbers you did not see.
4. **Commit once per milestone** on a branch `memory/<milestone-id>`, for example `memory/M0.3-extraction`. Commit message format: `feat(memory): M0.3 extraction reliability + dead-letter`.
5. **Never commit secrets or personal data.** That covers `.env`, tokens, DB dumps, vault notes with real content, eval runs on real memories, and `*.sql` data files. Run `git diff --cached --stat` before each commit and look.
6. **Memory rows are immutable.** A trigger (`memories_closeout_only`, `migrations/0001_core.sql`) only lets `status, superseded_at, superseded_by, valid_to, embedding, tsv` change, and `superseded_at`/`valid_to` only once. Corrections must be a **new version** in the same `lineage_id`. Never relax this trigger, except to add new mutable *metadata* columns to its free-list in the same migration that creates them.
7. **`events` is append-only** (trigger `events_append_only`). Record state changes as a new event (`kind='SYSTEM'`), never as an UPDATE.
8. **Dependency rule:** core runtime dependencies must be permissive (MIT, Apache-2.0, or BSD). **No AGPL or GPL, and no cloud APIs in the core path.** Every new dependency goes in `pyproject.toml` with its license in a comment, and must pass the license check (M0.4).
9. **Fully local.** The models are Ollama only: `qwen2.5:7b-instruct` (extraction and answering), `nomic-embed-text` (768-d) for embeddings, and `BAAI/bge-reranker-v2-m3` for reranking. A cloud model may appear only in `evals/` as an optional judge for publishing comparable scores, and only behind a flag.
10. **Schema changes go through Alembic only**, as `packages/memory-engine/migrations/versions/000N_*.py`. Each one must be reversible (a real `downgrade()`) and idempotent (`IF NOT EXISTS`).
11. **Destructive git operations** (history rewrite, force-push) are done by the human, not the agent. Print the commands and stop.
12. When something in this plan contradicts the code, **stop and write down the contradiction** in `packages/memory-engine/PLAN_NOTES.md`. Don't guess.

---

## 1. Full vision: what the finished engine is

**Lumen's memory engine is a memory you can see and trust.** It is memory-first, fully local and open-source (MIT). You tell it things once, and it never makes you re-explain them. It never silently overwrites what it knew. Every answer comes with the exact words you said and the date you said them.

> Thesis: **Memory → Context → Intelligence.** LLMs give transient intelligence; Lumen gives permanent, bi-temporal continuity.
> Contract: **Dump once → never re-explain → never silently overwrite.**

### 1.1 End-state capabilities

| # | Capability | What "done" looks like |
|---|---|---|
| 1 | **Capture anything** (save pipeline stage 1, §1.4) | Chat, voice transcripts, files, web clips, notes and the CLI all land in an append-only, hashed event log in under 20 ms, with no LLM in the path. |
| 2 | **Verifiable extraction** | A local model turns text into typed memories (10 types, 4 epistemic classes). Each one cites an exact character span, and anything unverified is quarantined, never trusted. |
| 3 | **Bi-temporal truth** | Every memory knows when it was true in the world (`valid_from`/`valid_to`) and when Lumen learned it (`recorded_at`). "What did I prefer in March?" and "What did you believe on 1 May?" both work. |
| 4 | **Never silently overwrite** | Updates create a new version in the same lineage with a `SUPERSEDES` edge. Another person's claim never overwrites yours. Real conflicts go to review. |
| 5 | **Hybrid recall that knows when it doesn't know** | Five channels, fused with RRF, then a cross-encoder rerank. Lumen abstains with "I have no record of that" when the reranker is not confident, and the threshold is tuned on evals. |
| 6 | **Glass-box viewer** | Browse, search, correct and forget memories. Each one shows its quote, its date said, its version history, its retrieval trace, a graph of entities and relations, and a timeline. |
| 7 | **Obsidian-like vault** | A human-readable mirror: entity, memory, digest and research notes with `[[wikilinks]]`, backlinks, graph view and daily notes. **Two-way:** editing a note becomes a lineage correction, with conflict handling. |
| 8 | **Conversational forget and correct** | "Forget my old phone number." "Actually it's Postgres 17." Both act through lineage and leave an audit trail, with undo. |
| 9 | **MCP server** | `remember / recall / forget / correct / timeline` over stdio and streamable HTTP, so Cursor, Claude Code and any agent can share one memory. |
| 10 | **Lanes, sessions, consolidation** | A `lane` column with a policy per lane, session synopses that carry context forward, and a nightly job that dedupes, builds entity profiles and finds contradictions. |
| 11 | **Measured** | Public LongMemEval and LoCoMo scores, plus an internal ~100-case suite run in CI on every change. |
| 12 | **Procedural memory (directives)** | Standing preferences and rules ("short answers", "no AGPL") are pinned and applied to every answer, each with its source. New proposals go to review. |
| 13 | **Context pack, not top-k** | The answering model gets directives, entities, cited memories grouped by type (decisions with their *why*), contradictions and a timeline, under a fixed grounded-answer contract. |
| 14 | **Ingest anything** | PDFs, Markdown, web pages, YouTube transcripts, spreadsheets and notes, with citations to page, heading or timestamp. |
| 15 | **Portable and restorable** | Export → import is byte-identical. Encrypted backups. A replay can rebuild everything from the event log with a newer model. |
| 16 | **Gets better with use** | Retrieval reinforcement (memories you use rank higher), reaffirmation instead of duplicates, and nightly synthesis of related fragments into cited summaries. |

### 1.2 The Obsidian-like experience

- **The vault is a projection, not the source of truth.** Postgres is the truth. The vault (`vault/`) is regenerated from the outbox and is always rebuildable with `lumen-memory vault rebuild`.
- **Note types:**
  - `entities/<Name>.md`: an entity profile with its aliases, current facts, typed relations, and backlinks to every memory that mentions it.
  - `memories/<yyyy>/<slug>-<lineage8>.md`: one note per lineage. It holds the current statement, the quote, the date said, and its version history.
  - `daily/<yyyy-mm-dd>.md`: what was captured, changed and forgotten that day, plus the morning brief.
  - `digests/`, `research/`: the existing lumen-core exports, re-based on the new schema and linking to entity notes.
- **Links come from data, not a hard-coded list.** Today `vault_sync.py:53-67 _wikilinkify` links 23 fixed words. The finished version links every entity name or alias to `[[Entity Name]]`, writes `related:` and `supersedes:` into frontmatter, and gives Obsidian's own graph view real edges.
- **Two-way sync.** Each note carries `lumen_id`, `lineage_id`, `version` and `content_hash` in its frontmatter. When you edit the statement in Obsidian, a watcher sees the change and calls `correct(lineage_id, new_statement, base_version)`. If the base version is stale, the note gets a conflict file and a review item. Nothing is lost.
- **Built-in graph and timeline.** The viewer has its own graph (entities as nodes, predicates and memory relations as edges, with an `as_of` slider) and a timeline (`valid_from` on the x-axis, versions stacked), so the demo works without Obsidian installed.

### 1.3 Launch criteria (the bar for "state of the art")

1. **Public scores, reported with the same reader and judge setup as the baselines.** These were verified on the web on 23 Nov 2026; re-check before publishing, because vendors keep self-reporting higher numbers:
   - **LongMemEval_S.** Zep scores 71.2% with gpt-4o as reader and judge (Zep paper, arXiv:2501.13956). Full-context gpt-4o scores about 60.6% (LongMemEval paper).
   - **LoCoMo, categories 1–4.** Mem0 scores 66.9% LLM-as-judge with gpt-4o-mini as reader and judge (Mem0 paper, arXiv:2504.19413). Zep disputes Mem0's run of Zep (66.0%) and reports 75.1% itself.
   - Scores depend heavily on the reader and judge models. Publish **two** rows:
     - (a) fully local: Qwen reader, local judge.
     - (b) the same reader and judge as the baseline, for an apples-to-apples comparison.
2. **Internal suite:** at least 90% answer accuracy, at least 0.9 abstention precision, at least 85% temporal accuracy, at least 90% update accuracy, and p95 recall latency under 300 ms on the server (not counting answer generation).
3. **Demo under 60 seconds:** say a fact, change it, then ask days later. The answer is correct and quotes the source and the date said. The viewer shows the lineage, and the vault note shows the same thing.
4. **Zero personal data** in the repo or its history. The dependency license check is green.

### 1.4 The 5-stage save pipeline and the 8 aspects of every memory

"Dump once" works only if every save goes through the same five stages, and every memory is stored along the same eight aspects. Milestone **S1** (Phase 0, after M0.8) builds this as one explicit pipeline, using the fixes from M0.3–M0.8.

```
capture ──► [1] L0 RAW CAPTURE ──► jobs(extract) ──► [2] EXTRACT ──► [3] VERIFY ──► [4] RESOLVE ──► [5] COMMIT ──► outbox ─► vault / viewer / MCP
            sync, <20 ms           async worker      local LLM       char spans      entities+tiers    versions+lineage
```

| Stage | Does | Implemented by | p95 budget (3090 server) |
|---|---|---|---|
| 1. L0 raw capture | An append-only event with a content hash, a hash chain, an idempotency key and chunk character coordinates. No LLM. | `pg_events.py` (exists), hardened in S1 | **< 20 ms** for ≤ 10 KB, excluding network |
| 2. Structured extraction | A local Ollama model with a JSON schema in `format`, one repair retry, and a dead-letter on failure. | M0.3, M0.4, S1 | **< 4 s** for ≤ 1 KB text (async, never blocks capture) |
| 3. Evidence verification | RapidFuzz finds the exact character span for each quote. Unsupported memories are **dropped**, never cited to the whole text. | `evidence.py` (exists), S1 | **< 5 ms** per memory |
| 4. Entity and contradiction resolution | Alias-aware entity matching. Conflicts are scoped by speaker. **Tier A** auto-commits; **Tier B** goes to review. | M0.5, S1 (Tier logic ported from legacy `resolver.py:107-200`) | **< 15 ms** per memory |
| 5. Canonical versioning and outbox | Create, supersede or reaffirm in a lineage, with citations, relations, a DERIVED event and an outbox row in **one transaction**. Embedding happens in its own job. | M0.6, M0.8, S1 | **< 50 ms** per batch of ≤ 10 memories |

**The 8 aspects every saved memory carries:**

| # | Aspect | Stored in | Set by stage | Status at `94370ca` |
|---|---|---|---|---|
| 1 | **What kind**: the memory type (10 values), plus the epistemic status fact, belief, inference or decision | `memories.type`, `memories.epistemic` | 2, checked in 4 | Exists. The DB enum has 10 types (it includes `entity`), but `schemas/extract.json` allows only 9. Pick one list and record the decision in `PLAN_NOTES.md`. |
| 2 | **Who said it, and about whom**: the speaker, and the subject and object | `memories.asserted_by`, `subject_id`, `object_id`; `events.actor` | 1 (speaker), 2, 4 | Partial. `asserted_by` is a guess by the LLM and is never checked against the capture speaker. |
| 3 | **When**: when it was said, when it was true, and when it was recorded | `events.occurred_at` (said), `valid_from/valid_to` (world), `recorded_at/superseded_at` (Lumen) | 1, 2, 5 | Broken. `valid_from` is never set, and the clock is hard-coded (M0.6). |
| 4 | **Lane**: personal, work, code or chat | `memories.lane` (**the column is added in S1**; its policies come in L1) | 2 | Missing |
| 5 | **Entities and relations**, the graph | `entities`, `entity_aliases`, `memory_relations` (MENTIONS, SUPERSEDES, CONTRADICTS …) | 2, 4, 5 | Exists, but alias lookup ignores kind |
| 6 | **Source and exact citation** | `sources`, `citations(char_start, char_end, quote, verified, chunk_id)` | 1, 3 | Partial. `chunk_id` is never filled, and unverified memories get the **whole event text** as a fake quote (`apply.py:142-162`). |
| 7 | **Confidence and salience** | `memories.confidence`, `memories.importance`, `memory_signals` (reaffirm and access counts) | 2, 3 (halves confidence if unverified), 5 | Exists. Reaffirming today creates duplicates instead of bumping `memory_signals`. |
| 8 | **Sensitivity and privacy** | `memories.sensitivity` (`normal / private / secret`) (**added in S1**) | 1 (regex hint), 2 (LLM), 5 | Missing |

**Sensitivity policy:**
- `secret` covers passwords, OTPs, card, Aadhaar and PAN numbers, bank details and API keys. A secret memory:
  - is never embedded and never put into a prompt;
  - is excluded from MCP and from the vault, which shows only a placeholder note;
  - is masked in the viewer until you click "reveal";
  - is retrieved only by an exact lexical or entity match from an authenticated owner.
- `private` covers health, finances, relationships, phone numbers and addresses. A private memory:
  - is excluded from MCP unless `MCP_INCLUDE_PRIVATE=1`;
  - is projected to the vault;
  - is used normally inside Lumen.

---

## 2. Ground truth: what the code actually is at `94370ca`

The status doc overstates several things. The agent must know these facts before changing anything.

| Fact | Where | Consequence |
|---|---|---|
| There are **two storage stacks.** The new one (`0001_core.sql`: `memories`, `citations`, `outbox`, `jobs` …) serves `/v1/capture`, `/v1/extract` and `/v1/recall`. The legacy one (`postgres_adapter.py`, 1,313 lines: `facts`, `triples`, `memory_outbox`) serves every `/api/v1/*` route in `endpoints.py`. | `app/adapters/storage/postgres_adapter.py`, `app/api/v1/endpoints.py:58` | The legacy tables are **not** created by `0001_core`. `/api/v1/*` either breaks on a fresh DB or hits old tables. Unify the stacks in M0.8. |
| **The outbox is not wired** to the new pipeline. `apply.py` never inserts into `outbox`. The vault worker reads the `memory_outbox` table and listens on `memory_outbox_channel`, while the new trigger notifies `lumen_outbox`. | `apply.py`, `apps/lumen-core/src/vault/outbox_worker.py:3,21`, `0001_core.sql:314-329` | "Outbox and vault sync done" is true only for the legacy stack. |
| **Nothing consumes `jobs`.** Capture enqueues `embed` and `extract` jobs, but there is no worker. | `pg_events.py:153-163` | Extraction only happens on an explicit `POST /v1/extract`. |
| **`valid_from` is never set** on insert. **Recall returns only `recorded_at`.** | `apply.py:194-281`, `pg_recall.py:182,229` | This is the citation-date bug, and it breaks bi-temporal recall. |
| **Hard-coded clock.** `captured_at` defaults to `"2026-11-21T10:00:00+05:30"` in production code. | `api/v1/extract.py:79`, `domain/extraction.py:197` | Every extraction without a date is stamped 21 Nov. |
| **`time_travel_at` is accepted but never used.** | `pg_recall.py:35` | Time travel is silently a no-op. |
| **The temporal channel nominates every memory.** It ranks all of `current_memories` by recency and importance, so any query returns results. | `pg_recall.py:140-155` | This is the root cause of "never abstains". |
| **`current_memories` includes `pending_review`**, which covers unverified extractions. | `0001_core.sql:185-186` | Unverified claims are recalled as if they were true. |
| **The client falls back to the legacy hybrid search** when `/v1/recall` returns nothing. | `apps/lumen-core/src/memory_client/client.py:201-215` | Even a correct abstention gets overridden by legacy results. |
| **Silent hash-embedding fallback.** If Ollama doesn't answer `/api/tags` within 0.5 s at startup, the app quietly uses `DeterministicHashEmbeddingAdapter`. The committed DB dump has one-hot vectors, so this already happened. | `app/main.py:39-55` | Vector recall is garbage with no warning. |
| **Cloud fallback in extraction.** When `OPENAI_API_KEY` or `GROQ_API_KEY` is set, extraction goes to the cloud. | `domain/extraction.py:181-184` | This breaks the fully-local promise. |
| **The dependency `unclecode-litellm>=1.81.0`** is an unofficial fork published by "unclecode", not BerriAI's `litellm`. | `pyproject.toml` | Supply-chain risk. Replace it (M0.4). |
| **`sheldon/index.html` is a 1 KB fragment** (31 lines of JS, with no HTML and no fetch calls). | `packages/memory-engine/sheldon/index.html` | There is no viewer yet. Build it from scratch (V1–V2). |
| **The `review_queue.kind` CHECK** has no `failed_extraction` value, and **`events` has no `status` column** and can't be updated. | `0001_core.sql:273-285, 62-79` | The status doc's "set `events.status='FAILED'`" can't work. Use `jobs` plus a SYSTEM event (M0.3). |
| **Nothing writes to `retrieval_traces`** (it is only truncated in tests and evals). | grep | Traces need to be built (M0.7, V1). |
| **Wikilinks come from a hard-coded list** of 23 terms. | `apps/lumen-core/src/vault/vault_sync.py:53-67` | Replace with entity-driven links (O1). |
| **A personal DB dump is committed:** `infra/docker/postgres/backup_lumen_memory_pre_migration.sql`, 2,911 lines, added in commit `3180693`. It contains real names. | git | Purge it from history (M0.1). |
| **Hard-coded token `test-token-secret`** | `evals/memory/run.py:25,37`, `tests/conftest.py`, `tests/test_recall_api.py` | Generate it per run (M0.2). |
| **`HOST="0.0.0.0"`**, and auth is skipped entirely when no token is set. | `core/config.py:21`, `api/middleware/auth.py:17` | Anyone on the LAN can read your memory. |
| **There is no `.github/` CI.** | repo root | Add it in E4. |
| **Unverified memories get the whole event text as their "citation"** (span `0..len`). | `apply.py:142-162` | Remove this fallback in S1 stage 3: unsupported memories are dropped. |
| **Tier A/B gating exists only in the legacy `resolver.py:107-200`** (triples). The new `apply.py` has no tiers. | `app/domain/resolver.py` | Port it as the pure function `decide()` in S1 stage 4. |
| **Sources are deduped globally by `content_hash`,** so the same text from two people collapses into one source with the first author. | `0001_core.sql:55`, `pg_events.py:76-80` | Switch to `source_key` (S1 stage 1). |
| **There is no hash chain.** Only a per-event content hash is stored, so "tamper-evident" in the status doc is not yet true. | `events` table | Add `prev_hash`/`chain_hash` (S1 stage 1). |
| **There is no lane or sensitivity column,** and entity alias lookup ignores kind. | `0001_core.sql`, `apply.py:42-47` | S1 |

The last re-check on the box got 5 of 21 memory-engine tests, 2 of 3 evals, and lumen-core at 118 passed / 21 failed / 1 skipped. The status doc claims 21/21 and 3/3. **M0.0 establishes the real baseline.**

## 2.5 Merge map: the `memory-engine` repo and the Lumen engine

**Inputs compared:**
- **Old repo:** `github.com/nirbhaysagar/memory-engine`, commit `56a43f5` (17 Aug 2026, the only commit; `git pull` on 23 Nov 2026 found nothing new). It is a FastAPI + SQLAlchemy + Celery/Redis + Next.js app with about 5.8k lines of backend code.
- **Lumen engine:** `packages/memory-engine` at `94370ca`.
- **Plan v2:** this document before the merge (backed up in `lumen-plan/plan_v2.md`).

**Rule: the Lumen monorepo is the only target.** The old repo is a parts donor. Port *logic, prompts and UX* into `packages/memory-engine`, rewritten against asyncpg, the immutable `memories` and `events` schema, and the Postgres `jobs` queue. Do **not** bring in its SQLAlchemy models, its Celery or Redis, or its separate Next.js app. Port no data, `.env` or uploads.

### 2.5.1 Component table

| Component | Best source | Why | Port action (from → to) |
|---|---|---|---|
| Event log, immutability, bi-temporal model, citations with char spans | **Lumen** | It enforces immutability with triggers and keeps an append-only provenance DAG. The old repo *mutates* rows: it flips `is_active` and runs `UPDATE concepts SET aliases, description, reference_count`. | Keep `0001_core.sql`. Nothing to port. |
| Versioning (version, root, supersedes) | **Lumen** (lineage) + old repo's API shape | The old `versioning.py:18-91` has a clean `GET /memory/{id}/versions` and PATCH-creates-a-version contract, the same idea as V1. | Use the old `api/memory.py:191-262` as the reference for the V1 `PATCH /memories/{id}` and `GET /lineages/{id}/history` response shapes. |
| Single-pass extraction with JSON schema | **Lumen** | One call per chunk. The old repo makes **9 LLM calls per chunk** (`extraction.py:147-155`). | Keep. |
| **Type-specific attributes** (decision who/why/alternatives/date, goal owner/deadline, constraint category, action owner/due, open question) | **Old repo** | Lumen's schema has only `statement` and `object_value`. The old prompts (`extraction.py:59-140`) capture the *why* behind decisions, and that is what makes recall useful. | Fold these into `schemas/extract.json` v2 as a per-type `attributes` object, plus prompt lines in `prompts/extract.md`. Still one pass (S1 stage 2). |
| **Salience from type weights** | **Old repo** | `compute_memory_importance` (`extraction.py:357-383`) gives importance by type (decision 0.95 … question 0.6) instead of trusting the LLM's guess. | `app/domain/salience.py`; final importance = 0.5·LLM + 0.5·type weight (S1 stage 2). |
| Evidence verification | **Lumen** | The old repo asks for a verbatim `evidence` field but **never checks it** (`extraction.py:204-205`). | Keep `evidence.py` and harden it in S1 stage 3. |
| **Paragraph and sentence-aware chunking** | **Old repo** | `ingestion.py:163-246` splits on paragraphs, then sentences, and merges up to a token budget. Lumen slices fixed 1,000-character windows (`pg_events.py:120-135`), which cut sentences in half. | `app/pipeline/chunker.py`. Rewrite it to return **character offsets into the original text**, because the old version re-joins text and loses coordinates (S1 stage 1). |
| **Concept and entity canonicalization**: two thresholds (≥ 0.92 auto, 0.85–0.92 proposal), `reference_count`, merge proposals with accept, reject and split | **Old repo** | `canonicalization.py:30-503` plus `api/merge_proposals.py` is a full human-in-the-loop merge flow. Lumen has only exact name and alias matching. | Port it into `app/domain/entity_resolution.py` (S1 stage 4) and `/v1/review/merge/*` (V1). Merges and splits are **events plus new alias rows**, never in-place updates. Never auto-merge people. |
| Rolling concept description | **Old repo idea, Lumen storage** | It is useful, but the old code overwrites `concepts.description` in place (`canonicalization.py:197-234`). | Store it as a versioned `abstractions(kind='profile')` with `abstraction_sources` (C1). |
| **Relationship typing at ingest** (supports, contradicts, contains, references, depends_on, inspired_by, follows_up) | **Old repo prompt, Lumen enum** | `linking.py:24-50` judges the new memory against its k nearest neighbours. Plan v2 only judged contradictions. | Extend the C1 judge into one call that returns a relation type. Map old → Lumen: contains → `PART_OF`, references → `RELATED_TO`, inspired_by → `DERIVED_FROM`. Add `FOLLOWS_UP` (migration 0010). |
| 5-channel hybrid recall + RRF | **Lumen** | It has more channels; the old repo uses vector + FTS + concepts. | Keep. |
| **Retrieval reinforcement** (spacing effect: `access_count`, `last_accessed_at`, log1p boost) | **Old repo** | `retrieval.py:336-380` models Ebbinghaus decay and use-based refresh. Lumen's `memory_signals` table exists but nothing writes to it. | Port the formula into `lifecycle_boost` (R1). It is bounded to [0.85, 1.15], **ranking only**, and ages from `said_at`. Recall writes the access counts through a job (R1). |
| **Structured context pack**: directives, concepts, cited memories, primitives grouped by type, contradictions, timeline, query intent | **Old repo** | `retrieval.build_context` (`retrieval.py:50-150`) gives the LLM organised context instead of a flat top-k, which plan v2 lacked. | New **K1**: `POST /v1/context` in `app/services/context_pack.py`, used by `jarvis.py`, MCP and `evals/answer.py`. |
| **Answer-prompt rules** (cite, mark low confidence, surface contradictions, say when you don't know, directives are absolute) | **Old repo** | `chat.py:58-92` is a good grounded-answer contract. | Port it into `app/prompts/answer.md`, used by K1 and the evals reader. |
| **User directives** (procedural memory: preference, constraint, goal, style, context; priority 1–10; injected into every context) | **Old repo** | Plan v2 had no procedural tier. Lumen's `abstractions` table already has `kind='directive'` and a `pinned` column, but nothing uses them. | New **D1**: directives = `abstractions(kind='directive', pinned)`, with priority and type columns (migration 0008). Port `api/directives.py` to `/v1/directives`. Mining (`consolidation.py:66-87, 330-410`) produces **proposals**, never auto-active ones. |
| **Sleep-worker synthesis**: clusters of recent memories → a cited summary, plus an audit record per run | **Old repo** | `consolidation.py:88-330` turns many fragments into one summary that keeps its provenance. | Add it to C1 as a pass that writes `abstractions(kind='summary')` + `abstraction_sources`, plus the SYSTEM event `consolidation.completed` (instead of a `consolidation_jobs` table). |
| Query-intent parser (intent, key_terms, time_filter, focus) | **Old repo** | `retrieval.py:38-47, 179-200` is a compact prompt that fits Q1. | Use it as Q1's LLM branch; `focus` → `type_hints`. |
| **Multi-format ingestion**: PDF, Markdown, HTML page, YouTube transcript, plus lumen-core's spreadsheet and image parsing | **Old repo + lumen-core** | The engine accepts only raw text today. The old `ingestion.py:17-160` and lumen-core's `src/vault/document_parser.py` together cover the common formats. | New **I1**: `app/ingest/` with parsers. PyPDF2 → `pypdf` (BSD). `markdown-it-py` (MIT), `beautifulsoup4` (MIT), `youtube-transcript-api` (MIT, optional `[web]` extra). |
| **Export and import round-trip** | **Old repo idea** | `api/memory.py:617-960` gives export → import → export. Plan v2 had **no backup or portability story**. | New **X1**: `lumen-memory export/import` as a JSONL bundle of all tables (events are the source of truth), plus a `replay` mode. Use a byte-identical round-trip test. |
| Collections (scoping) | **Lumen `contexts`** | They are the same concept, and `contexts` already has `parent_id`. | Nothing to port. The viewer gets a context switcher (V2). |
| Viewer UX: memory card, memory detail, concept page, merge proposals, import modal, force graph, streaming chat | **Old repo (design), Lumen (code)** | The old Next.js components are the most finished UI either project has. | Re-implement them as screens in the no-build viewer (V2/O3). Use `force-graph` (MIT, the vanilla build of the `react-force-graph-2d` the old app used) instead of Cytoscape. |
| Research-synthesis wedge ("what do these sources agree or disagree on?") | **Old repo idea** | It maps onto Lumen's `claims` table and CONTRADICTS edges. | Add a `consensus` intent to K1/Q1, 6 `synthesis-*` eval cases (E2), and an optional second launch demo. |
| Job queue | **Lumen** (Postgres `jobs`) | No Redis or Celery to run. | Drop Celery. |
| LLM gateway | **Neither**: a direct Ollama client (M0.4) | The old repo pins `litellm==1.49.0` and has cloud keys in its config. | Drop it. |
| Default model | **Lumen** (`qwen2.5:7b-instruct`, Apache-2.0) | The old default is `llama3.1:8b`, which is under the Llama Community License, not a permissive one. | Drop. |

### 2.5.2 Where the old repo is better than plan v2 (now adopted)
1. **Decision and goal attributes** (why, alternatives, owner, deadline) → S1 stage 2.
2. **Two-threshold canonicalization with a merge, reject and split review flow** → S1 stage 4, V1, V2.
3. **The structured context pack and grounded-answer rules** → K1.
4. **The procedural tier (directives)** → D1.
5. **Retrieval reinforcement** (the spacing effect) → R1.
6. **Paragraph-aware chunking** → S1 stage 1.
7. **Export/import round-trip** → X1.
8. **Multi-format ingestion** → I1.
9. **Relation typing beyond contradictions and the sleep-worker synthesis** → C1.
10. **A mature viewer design** → V2, O3.

### 2.5.3 Dropped from the old repo (do not port)
- **Mutable rows:** the `is_active` flips and in-place `UPDATE`s of concept descriptions, aliases and counts.
- **SQLAlchemy ORM models and `session.py`.** Lumen uses asyncpg with raw SQL; don't mix the two.
- **Celery, Redis, `celery_app.py` and `tasks.py`.** Use the Postgres `jobs` worker.
- **The 9-pass extraction.** Keep the single schema pass.
- **Its "evidence" field without verification.**
- **`litellm`, the OpenAI, Anthropic and Groq settings, and the `llama3.1:8b` default.**
- **Auto-activating directives at confidence ≥ 0.8.** They become proposals for review.
- **Decay from `created_at`.** Use `said_at` and the floors.
- **The separate Next.js app and its `package-lock.json`.** Port the designs only.
- **`PyPDF2`** (deprecated) → `pypdf`. Drop the duplicate `psycopg2-binary` and `requests`.
- **`uploads/` and any data in it.**

---

## 3. Checklist summary

Estimates are focused days for one developer working with an IDE agent.

| ✓ | ID | Milestone | Est. |
|---|---|---|---|
| | **Phase 0** | **Hardening gate + save pipeline** | **~7.5 d** |
| ☐ | M0.0 | Baseline: real test and eval numbers, `PLAN_NOTES.md` | 0.25 |
| ☐ | M0.1 | Purge the personal DB dump from git history and `.gitignore` it | 0.25 |
| ☐ | M0.2 | Security: bind to loopback or Tailscale, mandatory token, no hard-coded secrets | 0.5 |
| ☐ | M0.3 | Extraction reliability: Ollama JSON schema, `num_predict`, dead-letter, HTTP 422 | 0.75 |
| ☐ | M0.4 | Local-only and dependencies: drop the litellm fork and cloud fallback, fail loudly on embeddings, license CI | 0.5 |
| ☐ | M0.5 | Supersede scoped by speaker and polarity; third-party claims → `claims` | 0.5 |
| ☐ | M0.6 | Bi-temporal correctness: `valid_from`, `said_at` in citations, working time-travel, no hard-coded clock | 0.75 |
| ☐ | M0.7 | Recall honesty: only content channels nominate, exclude `pending_review`, abstain shape, no legacy fallback, traces | 0.75 |
| ☐ | M0.8 | One storage stack: retire legacy `/api/v1`, wire `outbox` in `apply.py`, add a `jobs` worker | 1.0 |
| ☐ | S1 | **Save pipeline end-to-end**: 5 stages (capture, extract, verify, resolve with Tier A/B, commit with lineage and outbox), 8 aspects per memory, `save-report` API. **v3:** paragraph-aware chunker, typed attributes and salience, two-threshold entity canonicalization (from the old repo) | 2.0 |
| ☐ | M0.9 | Client timeouts and retries (`client.py:40`) | 0.25 |
| | **Phase 0.5** | **Eval harness** | **~4 d** |
| ☐ | E1 | Harness v2: runner, metrics, results JSON, baseline file | 1.0 |
| ☐ | E2 | Grow the suite to about 100 cases in 8 categories | 1.5 |
| ☐ | E3 | LongMemEval (oracle and S subset) and LoCoMo adapters | 1.0 |
| ☐ | E4 | CI gate (GitHub Actions with a pgvector service, plus a nightly GPU run) | 0.5 |
| | **Phase 0.6** | **Rerank, calibrated abstention, context pack** | **~3.5 d** |
| ☐ | R1 | Local cross-encoder reranker after RRF, plus retrieval reinforcement (from the old repo) | 1.0 |
| ☐ | R2 | Abstention calibrated on reranker scores; τ tuned on evals | 1.0 |
| ☐ | R3 | Optional evidence-sufficiency check by the LLM | 0.5 |
| ☐ | **K1** | **Structured context pack plus the grounded-answer contract (from the old repo)** | 1.0 |
| | **Phase 1** | **Glass-box viewer** | **~3.5 d** |
| ☐ | V1 | Browse, history, trace, PATCH and DELETE API | 1.0 |
| ☐ | V2 | Viewer UI (cards, citations, lineage, forget and correct, review inbox). **v3:** the old repo's screens (entity page, merge proposals, import drop zone, streaming Ask, context switcher) | 2.5 |
| | **Phase 2** | **Conversational forget and correct, plus directives** | **~3 d** |
| ☑ | F1 | Forget through lineage (retract, redact, undo) | 1.0 |
| ☑ | F2 | "Actually it's X" correction through lineage | 1.0 |
| ☑ | **D1** | **Directives: the procedural memory tier (from the old repo)** | 1.0 |
| | **Phase 2.5** | **MCP, portability, ingestion** | **~3.5 d** |
| ☑ | P1 | MCP over stdio and streamable HTTP: remember, recall, context, forget, correct, timeline, directives | 1.0 |
| ☑ | **X1** | **Export, import and replay (round-trip, encrypted backup)** | 1.0 |
| ☑ | **I1** | **Ingest anything: PDF, Markdown, web, YouTube, spreadsheet** | 1.5 |
| | **Phase 2.7** | **Obsidian two-way vault, graph and timeline** | **~5 d** |
| ☑ | O1 | Vault projection v2 from the new schema (entity, memory and daily notes, real wikilinks and backlinks) | 1.5 |
| ☑ | O2 | Vault → engine sync: edits become lineage corrections, with conflict handling | 1.5 |
| ☑ | O3 | Graph and timeline view in the viewer | 1.5 |
| ☑ | O4 | Daily notes and morning brief | 0.5 |
| | **Phase 3** | **Lanes, sessions, consolidation, query understanding** | **~6 d** |
| ☑ | L1 | `lane` column with a per-lane policy (decay per day, affects ranking only) | 1.0 |
| ☑ | L2 | Session synopses and continuity | 1.0 |
| ☑ | C1 | Nightly consolidation (dedupe, entity profiles, contradiction judge). **v3:** relation typing, sleep-worker synthesis, directive mining | 2.5 |
| ☑ | Q1 | Query understanding (time ranges, decomposition, coreference). **v3:** the old repo's intent parser and a consensus intent | 1.5 |
| | **Launch** | Public LongMemEval and LoCoMo runs, `BENCHMARKS.md`, demo | 1.5 |
| | **Total** | | **≈ 37.5 focused days** |

Phases 4 and 5 (the hands and the durable runtime) are outside the memory engine and move to `apps/lumen-core` (§5).

---

## 4. Milestones

### Phase 0 — Hardening gate

#### M0.0 Baseline (0.25 d)
- **Goal:** know the real state before changing anything.
- **Why:** the status doc's numbers (21/21, 3/3) don't match the last re-check (5/21, 2/3).
- **Steps:**
  1. `git checkout -b memory/M0.0-baseline`
  2. Create a disposable DB and migrate it:
     ```bash
     createdb lumen_memory_test
     LUMEN_ENV=test alembic -c packages/memory-engine/alembic.ini upgrade head
     ```
  3. Run all three suites and save the raw output to `packages/memory-engine/PLAN_NOTES.md` under "Baseline":
     ```bash
     PYTHONPATH=packages/memory-engine .venv/bin/pytest packages/memory-engine/tests -q 2>&1 | tail -30
     .venv/bin/python evals/memory/run.py 2>&1 | tail -30
     PYTHONPATH=apps/lumen-core .venv/bin/pytest apps/lumen-core/tests -q 2>&1 | tail -30
     ```
  4. Record which tests fail and why (missing legacy tables, Ollama not running, and so on).
- **Acceptance:** `PLAN_NOTES.md` has the real pass and fail counts and the reasons for each failure.

#### M0.1 Purge the personal DB dump (0.25 d)
- **Goal:** no personal data anywhere in git history.
- **Why:** real names and memories are in history, and the launch is open-source.
- **Files:**
  - Delete `infra/docker/postgres/backup_lumen_memory_pre_migration.sql`.
  - Edit `.gitignore`.
- **Steps:**
  1. Add the following to `.gitignore`:
     ```
     # personal data — never commit
     *.dump
     *backup*.sql
     infra/docker/postgres/backup_*.sql
     evals/memory/results/*
     !evals/memory/results/.gitkeep
     vault/**/*.md
     !vault/README.md
     ```
  2. `git rm --cached infra/docker/postgres/backup_lumen_memory_pre_migration.sql && git commit -m "chore: remove personal DB dump"`
  3. Purge the history. This is destructive, so **the human runs it** after backing up the repo:
     ```bash
     git clone --mirror <repo-path-or-url> ../lumen-backup.git     # backup first
     pipx install git-filter-repo                                  # a tool, not a project dependency
     git filter-repo --path infra/docker/postgres/backup_lumen_memory_pre_migration.sql --invert-paths
     # only if a remote exists; this rewrites every branch:
     git push --force --all && git push --force --tags
     ```
  4. If the repo was ever pushed publicly, treat its contents as leaked.
- **Test:** `git log --all --oneline -- infra/docker/postgres/backup_lumen_memory_pre_migration.sql` prints nothing.
- **Acceptance:** the file is gone from every ref, and `git grep -I "<a real name from the dump>" $(git rev-list --all) | wc -l` returns 0.

#### M0.2 Security: bind, token, no hard-coded secrets (0.5 d)
- **Goal:** memory can't be read by anyone on the network without a token.
- **Files:**
  - `app/core/config.py:21-23`
  - `app/api/middleware/auth.py:13-30`
  - `app/main.py` (startup check)
  - `tests/conftest.py`, `tests/test_recall_api.py`, `evals/memory/run.py:25,37`
  - `infra/docker/*` compose files that publish ports
- **Steps:**
  1. In `config.py`, set `HOST: str = "127.0.0.1"`. When serving over Tailscale, the user sets `HOST=<tailscale ip>` explicitly. Never default to `0.0.0.0`.
  2. Make `LUMEN_MEMORY_TOKEN` **mandatory** whenever `LUMEN_ENV != "test"`. In `lifespan`, raise `RuntimeError("LUMEN_MEMORY_TOKEN must be set (>=32 chars)")` if it is missing or shorter than 32 characters.
  3. In `auth.py`:
     - Use `hmac.compare_digest(token, expected)`.
     - If no token is configured, return **401** (fail closed), not pass-through.
     - Only `/health` stays public. `/`, `/docs`, `/openapi.json` and `/redoc` are public only when `DEBUG=true`.
  4. Tests and evals: generate the token per run, `os.environ["LUMEN_MEMORY_TOKEN"] = secrets.token_urlsafe(32)` in `conftest.py` and `run.py`. Delete every literal `test-token-secret`.
  5. Remove the default `POSTGRES_PASSWORD="lumen_secure_pass"` from `config.py` (`config.py:28`) and from `evals/memory/run.py:23-24,36`, and require it from the environment. The test fixture sets its own.
  6. Docker: publish Postgres as `127.0.0.1:5432:5432`, not `5432:5432`.
- **Tests:** `tests/test_auth.py`
  - No token → 401.
  - Wrong token → 401.
  - Correct token → 200.
  - `/health` is open.
  - The app refuses to start without a token outside test.
- **Acceptance:** `rg -n "test-token-secret|0\.0\.0\.0|lumen_secure_pass" packages evals apps` returns nothing.

#### M0.3 Extraction reliability and dead-letter (0.75 d)
- **Goal:** extraction either produces valid memories or fails loudly into review.
- **Why:** today `max_tokens=450` cuts off the JSON (`extraction.py:232`). No schema is sent to the model (`extraction.py:233` only sets `json_object`). Every failure returns `{"entities": [], "memories": []}` (`extraction.py:260, 277, 307, 319`), which looks the same as "nothing to remember". Non-rate-limit LLM errors re-raise as a bare 500 (`extraction.py:257`).
- **Files:**
  - `app/domain/extraction.py`
  - `app/api/v1/extract.py`
  - New: `app/domain/errors.py`
  - New migration: `migrations/versions/0002_dead_letter.py`
- **Migration 0002:**
  ```sql
  ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_kind_check;
  ALTER TABLE review_queue ADD CONSTRAINT review_queue_kind_check
    CHECK (kind IN ('contradiction','entity_merge','low_confidence','belief_promotion',
                    'failed_extraction','vault_conflict'));
  CREATE TABLE IF NOT EXISTS extraction_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id uuid NOT NULL REFERENCES events(id),
    model text NOT NULL,
    attempt int NOT NULL,
    ok boolean NOT NULL,
    error_kind text,          -- 'llm_error' | 'json_decode' | 'schema' | 'truncated' | 'empty_valid'
    error_detail text,
    raw_output text,          -- truncated to 8 KB
    latency_ms int NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS extraction_attempts_event_idx ON extraction_attempts(event_id);
  ```
- **Steps:**
  1. Call Ollama's `/api/chat` through the new `OllamaClient` (M0.4, so do M0.4 step 1 first). Pass:
     - `format=_SCHEMA`. Ollama accepts a full JSON schema as `format`.
     - `options={"temperature": 0, "num_predict": 2048, "num_ctx": 8192}`.
  2. Add `class ExtractionError(Exception)` with `kind`, `detail` and `raw`. `extract()` either returns a valid dict or **raises**. It never returns empty dicts on failure. A valid empty extraction (`memories: []`) is fine and is logged as `empty_valid`.
  3. Detect truncation: if `done_reason == "length"`, raise `ExtractionError("truncated")`.
  4. Keep one repair retry with the validator message, as `extraction.py:283-319` does now.
  5. In `extract.py`, on `ExtractionError`, in one transaction:
     - Insert an `extraction_attempts` row.
     - Insert a `review_queue` row `(kind='failed_extraction', reason=kind, proposal={"event_id": …, "error": …})`.
     - Append a SYSTEM event `type='extract.failed', target_id=event_id, caused_by={event_id}`.
     - Set `jobs.status='dead', last_error=…` for `(kind='extract', event_id)`.
     - Then return **HTTP 422** `{"error": "extraction_failed", "kind": …, "event_id": …, "review_id": …}`.
  6. Add `POST /v1/extract/{event_id}/retry`, which re-queues the event.
- **Tests:** `tests/test_extraction_failures.py`. Mock the Ollama transport with `httpx.MockTransport` and cover:
  - Truncated output → 422 plus a review row.
  - Invalid JSON twice → 422.
  - A schema violation that is fixed on retry → 200.
  - Valid empty output → 200 with `memories_created=0` and no review row.
- **Acceptance:** no code path in `extraction.py` returns an empty extraction as an error value (`rg -n '"memories": \[\]' app/domain/extraction.py` is empty), and every failure is visible in `review_queue`.

#### M0.4 Local-only and dependency hygiene (0.5 d)
- **Goal:** nothing in the core can call a cloud API or quietly degrade.
- **Files:**
  - `packages/memory-engine/pyproject.toml`, `requirements.txt` (currently a stale stdlib-only note)
  - `app/main.py:33-55`
  - `app/domain/extraction.py:175-188`
  - New: `app/adapters/llm/ollama_client.py`
  - New: `packages/memory-engine/scripts/check_licenses.sh`
- **Steps:**
  1. Remove `unclecode-litellm`. Add `app/adapters/llm/ollama_client.py`:
     ```python
     class OllamaClient:
         def __init__(self, base_url: str, timeout: float = 120.0): ...
         async def chat(self, model: str, messages: list[dict], *, format: dict | str | None = None,
                        options: dict | None = None) -> dict: ...   # {"content", "done_reason", "eval_count"}
         async def embed(self, model: str, inputs: list[str]) -> list[list[float]]: ...  # POST /api/embed
     ```
  2. Delete the OpenAI and Groq branches in `extraction.py:181-184`, and `OPENAI_API_KEY` from `config.py`. Remove `OpenAIEmbeddingAdapter` from the core startup path. Keep it only under `evals/` if you need it at all.
  3. In `main.py`, keep the hash embedding **only** when `LUMEN_ENV == "test"`. Otherwise:
     - Probe Ollama with a 5 s timeout and retry 3 times.
     - Check that the embedding model is pulled.
     - Embed `"ping"` and assert `len == EMBEDDING_DIMENSION`.
     - On failure, refuse to start with a clear message.
     - Expose `GET /health` → `{"embedding_model": ..., "dim": 768, "ollama": "ok"}`.
  4. Record the embedding model per memory so you can migrate models later. Migration `0003_embedding_meta.py`:
     - `ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_model text;`
     - Add `embedding_model` to `free` in `memories_closeout_only()`.
  5. Make `scripts/check_licenses.sh` run `pip-licenses --from=mixed --fail-on="GNU General Public License v2 (GPLv2);GNU General Public License v3 (GPLv3);GNU Affero General Public License v3"` against the runtime environment. Also print the full table as `--format=markdown` into `LICENSES.md`.
  6. Fix `requirements.txt` to `-e .` (or delete it) so there is one source of dependencies. Also note that `pyproject.toml` points `lumen-memory = "app.main:cli"`, but `main.py` only has `uvicorn.run` at line 283. Add a real `cli()` with the subcommands `serve`, `worker`, `vault rebuild` and `mcp`.
- **Tests:** `tests/test_startup.py`
  - Outside test, startup fails when Ollama is unreachable (mocked).
  - In test, it uses the hash adapter.
- **Acceptance:** `rg -n -i "openai|groq|litellm" packages/memory-engine/app` returns nothing, and the license script passes.

#### M0.5 Supersede scoped by speaker and polarity (0.5 d)
- **Goal:** another person's statement can never overwrite yours.
- **Why:** `apply.py:169-187` looks up the existing memory by `(subject_id, predicate, context_id)` only. "Rahul says Lumen uses Mongo" then supersedes your own "Lumen uses Postgres".
- **Files:** `app/domain/apply.py`, `prompts/extract.md` (rule 6 already asks for `asserted_by`).
- **Steps:**
  1. Add `asserted_by` to the existing-row query:
     ```sql
     SELECT ... FROM memories
     WHERE subject_id=$1 AND predicate=$2 AND context_id=$3
       AND asserted_by=$4 AND superseded_at IS NULL AND status='active'
     ORDER BY version DESC LIMIT 1
     ```
  2. If there is no own-speaker row but **another** speaker's active row on the same `(subject, predicate)` disagrees:
     - Insert the new memory as a new lineage.
     - Add a `CONTRADICTS` relation.
     - Add a `review_queue(kind='contradiction')` row.
     - Never supersede across speakers.
  3. If `asserted_by != 'user'` and `epistemic='fact'`, write a `claims` row (`claimant=asserted_by, stance='supports'`) and store the memory with `epistemic='belief'`. A third party's word is a belief until you confirm it.
  4. Polarity: a `polarity=false` memory with the same `(subject, predicate, object)` as an active `polarity=true` one, from the same speaker, **supersedes** it ("I don't use X anymore").
  5. Only supersede when `cardinality='one'` **or** `change_cue=true`. This is the current behaviour; keep it.
- **Tests:** `tests/test_supersede_scoping.py`
  - Same speaker, `cardinality=one` → v2.
  - A different speaker → two lineages, a `CONTRADICTS` edge and a review row.
  - Negation supersedes.
  - Many-valued preferences never supersede. `evals/memory/cases/preference-02-not-overwritten.yaml` must still pass.
- **Acceptance:** all four tests pass.

#### M0.6 Bi-temporal correctness (0.75 d)
- **Goal:** every memory knows when it was said and when it was true.
- **Files:**
  - `app/domain/apply.py`
  - `app/api/v1/extract.py:79`
  - `app/domain/extraction.py:197`
  - `app/adapters/storage/pg_recall.py`
  - `app/api/v1/recall.py:128`
  - `schemas/extract.json`, `prompts/extract.md`
  - `apps/lumen-core/src/persona/jarvis.py` (`format_context_with_memories`)
- **Steps:**
  1. Remove the hard-coded `"2026-11-21T10:00:00+05:30"` from both places. `captured_at` defaults to the event's `occurred_at`, read from `events` when the request omits it.
  2. Add an optional `valid_from_text` to the extraction schema ("since March", "last week"). In `apply.py`, resolve it with `dateparser.parse(valid_from_text, settings={"RELATIVE_BASE": occurred_at, "RETURN_AS_TIMEZONE_AWARE": True})`. If it is missing, use `valid_from = events.occurred_at`. Always insert `valid_from`.
  3. On supersede, close the old row with `valid_to = new.valid_from` (world time) and `superseded_at = now()` (transaction time). Today both use `now` (`apply.py:229`).
  4. In recall, join the citation's event and return both dates:
     ```sql
     SELECT c.memory_id, c.quote, c.verified, c.char_start, c.char_end,
            e.occurred_at AS said_at, e.recorded_at AS captured_at, s.title, s.kind
     FROM citations c JOIN events e ON e.id = c.event_id LEFT JOIN sources s ON s.id = e.source_id
     WHERE c.memory_id = ANY($1::uuid[])
     ```
     Each result gets `said_at`, `valid_from`, `valid_to` and `recorded_at`. `jarvis.py` prints **said_at** ("you said on 12 Mar 2026").
  5. Implement `time_travel_at` (`pg_recall.py:35`). When it is set, replace `current_memories` with this predicate:
     ```sql
     -- what Lumen believed at T (transaction time)
     recorded_at <= $T AND (superseded_at IS NULL OR superseded_at > $T)
     ```
     Add a separate `valid_at` param for world time: `valid_from <= $V AND (valid_to IS NULL OR valid_to > $V)`. Wire both into `RecallRequest`.
- **Tests:** `tests/test_bitemporal.py`
  - A capture with `occurred_at=-30d` → the citation's `said_at` is 30 days ago.
  - "since March" resolves `valid_from`.
  - Time travel to before an update returns v1, and after it returns v2.
  - `valid_at` filters correctly.
- **Acceptance:** `rg -n "2026-11-21T10:00" packages/memory-engine/app` is empty, and the tests pass.

#### M0.7 Recall honesty, first pass (0.75 d)
- **Goal:** make abstention possible and stop showing unverified claims. Calibrating abstention is R2.
- **Files:**
  - `app/adapters/storage/pg_recall.py`
  - `app/api/v1/recall.py`
  - `apps/lumen-core/src/memory_client/client.py:192-215`
  - `apps/lumen-core/src/persona/jarvis.py`
  - Migration `0004_current_view.py`
- **Steps:**
  1. Migration 0004 splits the view so unverified claims stop being recalled as truth:
     ```sql
     CREATE OR REPLACE VIEW current_memories AS
       SELECT * FROM memories WHERE superseded_at IS NULL AND status = 'active';
     CREATE OR REPLACE VIEW pending_memories AS
       SELECT * FROM memories WHERE superseded_at IS NULL AND status = 'pending_review';
     ```
  2. **Only content channels nominate candidates.** The candidate set is vector ∪ lexical ∪ entity. Graph expands only from those. Temporal (`pg_recall.py:140-155`) becomes a **boost on existing candidates**, not a channel that adds rows: restrict its SQL with `AND id = ANY($candidates)`.
  3. Tighten the gates:
     - The vector channel gets a minimum cosine similarity, provisionally 0.35 for nomic-embed-text. R2 replaces it. Today the gate is `threshold=0.01` (`pg_recall.py:34,68`).
     - Lexical requires `tsv @@ query` or trigram similarity ≥ 0.3. Today it is 0.08 (`pg_recall.py:82`).
  4. Change the response shape:
     ```python
     class RecallResponse(BaseModel):
         status: Literal["ok", "abstain"]
         results: list[RecallItem]
         abstain_reason: str | None = None   # 'no_candidates' | 'low_confidence' | 'insufficient_evidence'
         confidence: float | None = None
         trace_id: uuid.UUID
     ```
  5. Write a `retrieval_traces` row on every recall, with the per-channel ids, ranks and scores, the fused scores, the returned ids and `latency_ms`.
  6. In the client (`client.py:192-215`), **delete the legacy `/api/v1/search/hybrid` fallback**. On `status="abstain"`, return `[]` with an `abstained` flag.
  7. In `jarvis.py`, when the client reports `abstained`, the system prompt tells the model: "Memory has no record relevant to this. Say so plainly ('I have no record of that, Sir') and do not invent one."
- **Tests:**
  - `tests/test_recall_abstain.py`:
    - An empty DB → `abstain/no_candidates`.
    - Unrelated memories ("I use Neovim"), then the query "what's my blood group?" → abstain.
    - The temporal channel never adds an id that no content channel found.
    - `pending_review` rows are not returned.
  - `apps/lumen-core/tests/test_memory_client_abstain.py`
- **Acceptance:** add `evals/memory/cases/abstention-01.yaml` (setup: three unrelated facts; a query with no answer; `expect: {abstain: true}`), and it passes.

#### M0.8 One storage stack, outbox wired, jobs worker (1.0 d)
- **Goal:** one schema, and every memory change reaches the vault.
- **Why:** two schemas and a dead outbox. The vault (O1–O2) depends on this.
- **Files:**
  - `app/api/v1/endpoints.py`
  - `app/adapters/storage/postgres_adapter.py`
  - `app/main.py`
  - `app/domain/apply.py`
  - New: `app/services/extract_service.py`
  - New: `app/workers/jobs_worker.py`
  - `apps/lumen-core/src/vault/outbox_worker.py`
- **Steps:**
  1. Move `endpoints.py` and `postgres_adapter.py` to `app/legacy/`, and stop mounting `v1_router` in `main.py` by default (`LUMEN_LEGACY_API=1` re-enables it). Port anything the viewer needs (trace, review queue, outbox stats) to the new schema in V1. Delete the legacy tests, or mark them `@pytest.mark.legacy` and exclude them by default in `pytest.ini`.
  2. In `apply.py`, inside the **same transaction** as each insert or supersede, add:
     ```sql
     INSERT INTO outbox (event_id, topic, payload)
     VALUES ($event_id, 'memory.created' | 'memory.superseded' | 'memory.retracted' | 'entity.upserted',
             jsonb_build_object('memory_id', ..., 'lineage_id', ..., 'version', ...));
     ```
     Also append a DERIVED event (`type='memory.created'`, `target_id=memory_id`, `caused_by={event_id}`) so the event log stays the complete provenance DAG.
  3. Point `outbox_worker.py` at the table `outbox` and the channel `lumen_outbox`. Add `processed_at timestamptz` and `last_error text` to `outbox` (migration `0005_outbox_cols.py`).
  4. Refactor: move the body of `extract_and_apply` (`api/v1/extract.py:46-113`) into `app/services/extract_service.py: async def extract_event(pool, embedding, event_id, *, speaker, captured_at=None) -> ApplyResult`, so the API, the worker and MCP share one path.
  5. `jobs_worker.py` is a loop of `SELECT … FROM jobs WHERE status='queued' AND run_after <= now() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1`. It handles:
     - `extract` (calls `extract_event`)
     - `embed` (the chunk embeddings)
     - `consolidate` (used in C1)

     Back off exponentially (`run_after = now() + 2^attempts min`) and mark the job `dead` after 5 attempts. Start it in `lifespan` when `LUMEN_WORKERS=1`, or run it alone with `lumen-memory worker`.
- **Tests:**
  - `tests/test_outbox_wiring.py`: an extract writes outbox rows in the same transaction (a forced rollback leaves no outbox row), and a supersede emits `memory.superseded`.
  - `tests/test_jobs_worker.py`: capture → the worker processes extract → memories exist; a failing extraction → dead after 5 attempts plus a review row.
- **Acceptance:** `POST /v1/capture` alone, with the worker running, produces memories within 30 s, and `/v1/outbox/stats` (re-exposed in V1) shows them.

#### S1 Save pipeline end-to-end: 5 stages, 8 aspects (2.0 d)
- **Goal:** one explicit, testable pipeline (§1.4). Every capture becomes 0..n memories that carry all 8 aspects, or a visible reason why it didn't.
- **Why:** today the stages are spread over `pg_events.py`, `extraction.py`, `apply.py` (one 373-line function) and the legacy `resolver.py`. The Tier A/B logic lives only in the legacy stack. Unsupported memories are stored with the whole text as their "citation". No sensitivity or lane is recorded. Embedding errors are swallowed (`extract.py:92-107`).
- **Depends on:** M0.3–M0.8. S1 assembles those fixes into stage contracts and adds what is still missing.
- **Files:**
  - New: `app/pipeline/__init__.py` with `SavePipeline`, plus `capture.py`, `extract.py`, `verify.py`, `resolve.py`, `commit.py` and `contracts.py` (the Pydantic models below)
  - Edit: `app/adapters/storage/pg_events.py`, `app/domain/evidence.py`, `app/domain/apply.py` (shrinks to helpers used by `commit.py`), `app/services/extract_service.py` (calls `SavePipeline`), `app/workers/jobs_worker.py`
  - `schemas/extract.json` v2 and `prompts/extract.md`
  - New: `app/domain/sensitivity.py` and `app/domain/decide.py`
  - New migration: `migrations/versions/0006_save_aspects.py`
  - New API: `GET /v1/events/{event_id}/save-report`
- **Migration 0006_save_aspects:**
  ```sql
  DO $$ BEGIN CREATE TYPE memory_lane AS ENUM ('personal','work','code','chat');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
  DO $$ BEGIN CREATE TYPE sensitivity AS ENUM ('normal','private','secret');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
  ALTER TABLE memories ADD COLUMN IF NOT EXISTS lane memory_lane NOT NULL DEFAULT 'personal';
  ALTER TABLE memories ADD COLUMN IF NOT EXISTS sensitivity sensitivity NOT NULL DEFAULT 'normal';
  CREATE INDEX IF NOT EXISTS memories_lane_idx ON memories (context_id, lane) WHERE superseded_at IS NULL;
  -- lane and sensitivity may be re-classified without a new version:
  --   add 'lane','sensitivity' to the free-list in memories_closeout_only()
  ALTER TABLE events ADD COLUMN IF NOT EXISTS speaker text;      -- who uttered it (capture input)
  ALTER TABLE chunks ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';  -- page / heading / timestamp (I1)
  ALTER TABLE entities ADD COLUMN IF NOT EXISTS description text;                  -- canonical description (S1 stage 4, from the old repo)
  ALTER TABLE events ADD COLUMN IF NOT EXISTS prev_hash text;    -- hash chain (tamper evidence)
  ALTER TABLE events ADD COLUMN IF NOT EXISTS chain_hash text;
  -- sources: today UNIQUE(content_hash) merges the same text said by different people
  ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_content_hash_key;
  ALTER TABLE sources ADD COLUMN IF NOT EXISTS source_key text;  -- sha256(kind|uri|author|content_hash)
  CREATE UNIQUE INDEX IF NOT EXISTS sources_source_key_uq ON sources(source_key);
  CREATE TABLE IF NOT EXISTS save_runs (                         -- one row per pipeline run
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id uuid NOT NULL REFERENCES events(id),
    stage_ms jsonb NOT NULL,        -- {"extract": 2140, "verify": 3, "resolve": 9, "commit": 21}
    counts jsonb NOT NULL,          -- {"extracted":4,"verified":3,"dropped_unsupported":1,"tier_a":2,"tier_b":1,"reaffirmed":0,"superseded":1}
    dropped jsonb NOT NULL DEFAULT '[]',   -- [{statement, reason}]
    model text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now());
  CREATE INDEX IF NOT EXISTS save_runs_event_idx ON save_runs(event_id);
  ```
  The `events_append_only` trigger blocks UPDATE, so `speaker`, `prev_hash` and `chain_hash` are written **at insert**. Old rows keep NULL, and the chain starts at the first new event.

##### Stage 1: L0 raw capture
- **Contract:**
  ```python
  class CaptureRequest(BaseModel):       # app/domain/events.py (extend)
      content: str; source: SourceInfo; context: str = "default"
      occurred_at: datetime | None = None; idempotency_key: str | None = None
      speaker: str = "user"              # NEW: who said it (aspect 2)
  class CaptureResult(BaseModel):        # unchanged + sensitivity_hint
      event_id: UUID; job_ids: list[int]; idempotent_hit: bool; context_id: UUID; source_id: UUID
      sensitivity_hint: Literal["normal","private","secret"]
  ```
- **Steps:**
  1. Keep the single transaction and the idempotency check (`pg_events.py:47-70`). Store exactly the normalised text that was hashed, so chunk coordinates index into `events.content`.
  2. Replace the global source dedupe (`ON CONFLICT (content_hash)`, `pg_events.py:76-80`) with `source_key`. That way "yes" said by two people stays as two sources with the right authors.
  3. **Hash chain**, per context:
     - `SELECT pg_advisory_xact_lock(hashtext('chain:'||context_id))`.
     - Read the last `chain_hash` for the context.
     - Set `chain_hash = sha256(prev_hash || content_hash || occurred_at || speaker || seq)`.
     - Add `lumen-memory verify-chain` to re-check it.
  4. Run `sensitivity.scan(content)`, using regexes for OTPs, cards (Luhn), Aadhaar, PAN, IFSC, API-key shapes, passwords ("my password is"), phones and emails. Put the result in `events.payload.sensitivity_hint`. It is cheap: < 1 ms.
  5. Write `events.speaker` and enqueue `extract` (and `embed` for chunks). The LLM never runs here.
  6. **Paragraph-aware chunker (v3, from the old repo).** Port `memory-engine/backend/app/services/ingestion.py:163-246` (`chunk_text`, `_split_paragraphs`, `_merge_paragraphs`) to `app/pipeline/chunker.py`:
     - Split on blank lines, then on sentence boundaries for long paragraphs, then merge up to a budget (default 1,000 chars, with a 1–2 sentence overlap).
     - **Change:** return `(char_start, char_end)` into the stored text instead of re-joined strings, so citations stay exact.
     - It replaces the fixed 1,000/200 window at `pg_events.py:120-135`.
     - Test: no chunk boundary falls inside a sentence when a paragraph is shorter than the budget.
- **Failure behaviour:** all or nothing. If the DB is down, return 503 and write nothing. A duplicate `idempotency_key` returns the existing event with `idempotent_hit=true`.
- **Tests:** `tests/test_capture_contract.py`
  - For every chunk, `content[c_start:c_end] == chunk.text`, including 1,000-character chunks with 200-character overlap and Unicode or Hindi text.
  - Idempotent replay creates no new rows.
  - The same text from two authors gives two sources.
  - The chain verifies, and editing a row in a copied DB breaks the check.
  - UPDATE and DELETE on `events` raise.
  - Perf (`@pytest.mark.perf`): p95 under 20 ms over 200 captures of 2 KB on a local DB.

##### Stage 2: Local structured extraction
- **Contract:** `extract(event: EventRow, context_memories: list[MemoryBrief]) -> ExtractionResult | raises ExtractionError`. Schema v2 adds these fields per memory:
  ```json
  "lane": {"enum": ["personal","work","code","chat"]},
  "sensitivity": {"enum": ["normal","private","secret"]},
  "valid_from_text": {"type": ["string","null"]},
  "about_speaker": {"type": "boolean"}
  ```
  `about_speaker` is true when the speaker states something about themselves. It feeds Tier A.
- **Steps:** M0.3 and M0.4 (Ollama `format=schema`, `num_predict=2048`, truncation detection, one repair retry, dead-letter). In addition:
  1. Pass `SPEAKER` from `events.speaker`, not a default.
  2. Pass the top 5 existing memories about the same entities as `CONTEXT`, using a lexical and entity lookup. The prompt already reserves that slot at `extraction.py:214`.
  3. Long events: extract per chunk, and give the model the chunk's `char_start` so evidence is searched only in that chunk.
  4. **Typed attributes (v3, from the old repo).** Add an optional per-type `attributes` object to schema v2, porting the fields from `memory-engine/backend/app/services/extraction.py:59-140`:
     - decision: `{who, why, alternatives[], decided_at}`
     - goal: `{owner, deadline}`
     - constraint: `{category: technical|budget|time|security|legal}`
     - action: `{owner, due}`
     - question: `{status: open|answered}`
     - assumption: `{context}`

     Store it in `memories.object_value` under the key `attributes`, so no column is needed. Still **one** extraction pass, not the old repo's nine.
  5. **Salience (v3, from the old repo).** Add `app/domain/salience.py`, porting `compute_memory_importance` (`extraction.py:357-383`). The type weights are decision 0.95, constraint 0.90, goal 0.85, action 0.80, insight 0.75, fact 0.70, assumption 0.65, question 0.60. Set `importance = round(0.5*llm_importance + 0.5*type_weight*confidence, 3)`.
  6. Test additions: `tests/test_extract_contract.py` checks that decisions carry their `why` and `alternatives`, and that salience falls in [0.1, 1].
- **Failure behaviour:** `ExtractionError` → an `extraction_attempts` row, `review_queue(failed_extraction)`, a SYSTEM event `extract.failed`, `jobs.status='dead'` and HTTP 422 (M0.3). A valid empty result is fine and is logged as `empty_valid`.
- **Tests:** `tests/test_extraction_failures.py` (M0.3), plus `tests/test_extract_contract.py`: the v2 fields are present, and the speaker is passed through.

##### Stage 3: Verbatim evidence verification
- **Contract:** `verify(event_text: str, chunk_offset: int, mem: ExtractedMemory) -> VerifiedMemory | Dropped`, where `VerifiedMemory` carries `citation=(char_start, char_end, quote, chunk_id)`.
- **Steps:**
  1. Keep `find_evidence_span` (`evidence.py:13`: exact, then case-insensitive, then fuzzy at ≥ 90). Replace the O(n·w) sliding window with `rapidfuzz.fuzz.partial_ratio_alignment`, which returns `dest_start/dest_end` directly. Search the chunk first, then the whole event.
  2. **Drop unsupported memories.** If no quote verifies, the memory is **not stored**. Add `{statement, reason: "no_verbatim_evidence"}` to `save_runs.dropped`.
     - Delete the fallback at `apply.py:142-143` that cites `(0, len(raw_text))` with `primary_quote = raw_text`.
     - Debugging escape hatch: `KEEP_UNSUPPORTED=1` stores such memories as `pending_review` only.
  3. **Grounding check:** the memory's key value (the `object_value` or object entity name) must fuzzy-appear in the quote, with `partial_ratio ≥ 80`. If it doesn't, downgrade to Tier B with `reason='value_not_in_quote'`. This catches "quote is real, statement is invented".
  4. Reject quotes shorter than 8 characters unless they equal the whole utterance. Fill `citations.chunk_id`.
  5. The round-trip invariant must always hold: `event_text[char_start:char_end]` equals the stored `quote`. Store the **source text slice**, not the model's quote.
- **Failure behaviour:** never raises. Each memory either passes or is listed in `dropped` with a reason.
- **Tests:** `tests/test_evidence.py`
  - Exact, case-insensitive and one-typo fuzzy matches.
  - A hallucinated quote → dropped.
  - A real quote with an invented value → Tier B.
  - A quote longer than the text.
  - Hindi or Devanagari text.
  - The round-trip invariant.
  - Perf: < 5 ms per memory on a 10 KB event.

##### Stage 4: Entity and contradiction resolution (Tier A / Tier B)
- **Contract:** `resolve(conn, vm: VerifiedMemory, event: EventRow, policy: LanePolicy) -> Decision`, where `decide()` is a **pure function** in `app/domain/decide.py`:
  ```python
  class Decision(BaseModel):
      action: Literal["create", "supersede", "reaffirm", "stage"]
      tier: Literal["A", "B"]
      reason: str                      # e.g. 'new', 'same_speaker_update', 'cross_speaker_conflict', 'low_confidence', 'inference'
      target_lineage: UUID | None = None
      contradicts: list[UUID] = []
  def decide(cand: Candidate, own_existing: MemoryRow | None, others_existing: list[MemoryRow], policy: LanePolicy) -> Decision
  ```
- **Steps:**
  1. **Entities:** normalise names (NFKC, casefold, trim punctuation). Then match in this order:
     - The exact canonical name **with the same kind**.
     - An alias **with the same kind**. Today `apply.py:42-47` ignores kind, so "Apple" the company and "apple" the fruit merge.
     - Trigram similarity ≥ 0.9 with the same kind → reuse it, but add an `entity_merge` proposal if it isn't exact.
     - Otherwise, create a new entity.

     People never auto-merge on fuzzy matches.

     **v3, from the old repo (`canonicalization.py:30-145, 286-503`, two thresholds):** for non-person entities, also compare name embeddings (`entities.embedding`).
     - Cosine **≥ 0.92**: auto-link as an alias. This writes a new `entity_aliases` row and a SYSTEM event `entity.alias_added`.
     - **0.85–0.92**: `review_queue(kind='entity_merge')`, with both descriptions.
     - Below 0.85: a new entity.
     - Keep the thresholds in `config.py` (`ENTITY_AUTO_MERGE=0.92`, `ENTITY_REVIEW=0.85`).
     - **Accept, reject and split** are V1 endpoints.
       - An accepted merge repoints nothing in place. It writes `entity_aliases` plus the event `entity.merged {from, into}`, and readers resolve merged ids through the alias table.
       - A split writes `entity.split` and restores the original id.
     - `reference_count` is a view (`count(DISTINCT memory_id)` over MENTIONS), not a mutable column.
  2. **Speaker scoping** (M0.5): find the own-speaker active row by `(subject, predicate, context, asserted_by)`, and other speakers' rows separately. `asserted_by` must equal `events.speaker` unless the memory reports someone else ("Rahul said …"). In that case `asserted_by` = that person, a `claims` row is written, and `epistemic='belief'`.
  3. **Tier A (auto-commit)**, only if **all** of these hold:
     - The evidence verified and the grounding check passed.
     - `confidence ≥ policy.auto_commit_min_conf` (0.7 until L1 adds per-lane values).
     - `epistemic != 'inference'`.
     - There is no conflicting active memory from another speaker.
     - If it supersedes, it is the same speaker and (`cardinality='one'` or `change_cue`).
  4. **Tier B (stage for review):** everything else. That includes cross-speaker conflicts, low confidence, inferences, a non-user updating a `cardinality='one'` fact, and `value_not_in_quote`. Staged memories are inserted with `status='pending_review'` and a `review_queue` row (`contradiction` or `low_confidence`), so they appear in the viewer inbox but are never recalled (M0.7).
  5. **Reaffirm, which is the "never re-explain" part:** the same speaker, the same subject and predicate, and a statement with cosine > 0.95 or trigram > 0.9 against the active row. The action is `reaffirm`: add a `citations(role='reaffirm')` row and bump `memory_signals.reaffirm_count` and `last_reaffirmed_at`. **No new memory and no new version.**
- **Failure behaviour:** a DB error rolls back the whole event's commit, and the job retries with backoff (M0.8). The `decide()` function cannot fail.
- **Tests:**
  - `tests/test_decide.py`, a table of at least 20 rows. For example:
    - new → A/create
    - same-speaker `cardinality=one` update → A/supersede
    - cross-speaker → B/stage + contradicts
    - inference → B
    - low confidence → B
    - restated fact → reaffirm
    - negation → A/supersede
  - `tests/test_entity_resolution.py`: kind-aware aliases, no fuzzy auto-merge of people, a merge proposal created.

##### Stage 5: Canonical versioning, lineage and outbox
- **Contract:**
  ```python
  commit(conn, event, decisions: list[tuple[VerifiedMemory, Decision]]) -> CommitResult
  # CommitResult: memories[{id, lineage_id, version, action, tier, lane, sensitivity}], review_ids[], outbox_ids[]
  ```
- **Steps:** one transaction per event, covering all of the following:
  1. Insert or supersede rows through the immutability trigger's rules. Supersede sets `superseded_at=now()`, `valid_to = new.valid_from` and `superseded_by` (M0.6).
  2. Write each memory's citation from stage 3.
  3. Write the `MENTIONS`, `SUPERSEDES` and `CONTRADICTS` relations, and the inter-memory relations from extraction (`apply.py:337-360`, kept).
  4. Insert or update `memory_signals` per lineage.
  5. Append a DERIVED event per memory (`memory.created`, `memory.superseded`, `memory.reaffirmed` or `memory.staged`) with `caused_by={event_id}`.
  6. Write the outbox rows (M0.8). Secret memories get a `secret: true` payload, so the projector writes a placeholder.
  7. Write a `save_runs` row with the per-stage milliseconds, counts and dropped list.
  8. **Embedding moves out of the request:** enqueue a `jobs(kind='embed_memory')` per new memory. Today a failed embed only logs a warning (`extract.py:106-107`).
     - Vector recall skips rows with NULL embeddings. Lexical and entity recall still find them.
     - `secret` memories are never embedded.
- **Failure behaviour:** all or nothing per event. If the outbox insert fails, the memories are not saved either, which is the transactional outbox guarantee. The job retries and is dead-lettered after 5 attempts.
- **Tests:** `tests/test_commit.py`
  - A forced exception after the memory insert → no memory, citation, outbox or `save_runs` rows.
  - A supersede creates v2 and closes v1 correctly.
  - A reaffirm adds no rows to `memories`.
  - Secret memories are not embedded, and their outbox payload is masked.

##### End-to-end
- **`SavePipeline.run(event_id) -> SaveReport`** runs stages 2 to 5. Stage 1 is the synchronous `/v1/capture`. The jobs worker, `/v1/extract` and MCP `remember` all call this one function.
- **`GET /v1/events/{event_id}/save-report`** returns the `save_runs` row, the memories with all 8 aspects, the dropped items with reasons, and the review items. The viewer (V2) shows it as "Why was / wasn't this saved?".
- **Golden test** `tests/test_save_pipeline_e2e.py`, using a mocked LLM that returns fixed JSON. Capture as speaker `user`, 3 days ago:
  > "Since March I've been using Postgres 17 for Lumen at work. Rahul thinks we should use Mongo. My OTP is 482913."

  Assert:
  - (1) The type and epistemic status are set.
  - (2) The Postgres memory has `asserted_by='user'`, and the Mongo memory has `asserted_by='Rahul'`, is a `belief`, has a `claims` row and is Tier B with `CONTRADICTS`.
  - (3) `said_at` is 3 days ago, `valid_from` is 1 March, and `recorded_at` is now.
  - (4) `lane='work'`.
  - (5) The entities Lumen, PostgreSQL and Rahul, with `MENTIONS` edges.
  - (6) The citation round-trips exactly, and `chunk_id` is set.
  - (7) The confidence and importance are in range, and `memory_signals` exists.
  - (8) The OTP memory is `secret`, is not embedded, and its vault payload is masked.

  Then replay the same sentence and assert that it is reaffirmed with no new memories.
- **Eval hook:** add a `save:` expectation block to the E2 case schema (`expect.saved: [{statement_contains, lane, epistemic, asserted_by, sensitivity}]`) and 10 `save-*.yaml` cases. `metrics.py` reports `save_aspect_accuracy`.
- **Acceptance:**
  - The golden test passes.
  - `apply.py` no longer has the whole-text citation fallback.
  - No memory row has a citation where `quote != events.content[char_start:char_end]`. This query must return 0:
    ```sql
    SELECT count(*) FROM citations c JOIN events e ON e.id=c.event_id
    WHERE substring(e.content from c.char_start+1 for c.char_end-c.char_start) <> c.quote;
    ```
  - The stage p95 budgets in §1.4 are met and recorded in `PLAN_NOTES.md`.
  - `save_aspect_accuracy` is at least 0.9 on the `save-*` cases.

#### M0.9 Client timeouts (0.25 d)
- **Goal:** lumen-core stops dropping memory calls that are merely slow.
- **File:** `apps/lumen-core/src/memory_client/client.py:40`
- **Steps:**
  1. Replace the client with:
     ```python
     httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0,
         read=float(os.getenv("LUMEN_MEMORY_READ_TIMEOUT", "15")), write=5.0, pool=2.0))
     ```
     Extract calls pass their own `timeout=120`.
  2. Retry idempotent calls (recall) once on `ConnectError` or `ReadTimeout`. Capture sends an `idempotency_key` (already supported by `CaptureRequest.idempotency_key`) so retries are safe.
  3. The circuit breaker opens only on connection errors and timeouts, not on 4xx responses.
- **Tests:** `apps/lumen-core/tests/test_memory_client_timeouts.py`, with a `MockTransport` that sleeps.
- **Acceptance:** a 6 s recall succeeds, and repeated connect failures trip the breaker.

**Phase 0 exit gate:**
- The S1 golden save test passes, and the citation round-trip query returns 0.
- All memory-engine tests are green, with no `xfail` except `@legacy`.
- The existing 3 evals and `abstention-01` pass.
- lumen-core tests that touch memory are green.
- The `rg` checks from M0.2, M0.3, M0.4 and M0.6 are clean.

---

### Phase 0.5 — Eval harness

#### E1 Harness v2 with metrics (1.0 d)
- **Goal:** every change shows its score, broken down by category.
- **Files:**
  - Rewrite `evals/memory/run.py`, keeping today's YAML format working.
  - New: `evals/memory/metrics.py`, `evals/memory/answer.py`, `evals/memory/judge.py`
  - `evals/memory/baseline.json`, `evals/memory/results/` (git-ignored except `.gitkeep`)
- **Case schema** (extends today's):
  ```yaml
  id: temporal-03
  category: temporal   # single_fact|multi_session|temporal|knowledge_update|abstention|multi_hop|preference|contradiction
  why: "valid_from must come from 'since March', not capture time"
  setup:
    - {content: "...", source: {kind: chat}, occurred_at: "-40d", speaker: user}
  query: {text: "...", as_of: null, valid_at: null}
  expect:
    abstain: false
    answer_contains_any: ["..."]
    answer_not_contains: ["..."]        # the stale value must NOT appear
    top_k_contains_setup: {k: 5, index: 0}
    cited_said_at: "-40d"               # citation date check (±1 day)
  ```
- **Steps:**
  1. Run each case against a clean DB. Keep `TRUNCATE … CASCADE` as now, or clone a migrated template DB (`CREATE DATABASE x TEMPLATE lumen_eval_tmpl`) for speed.
  2. `answer.py` builds the answer with the local reader (`qwen2.5:7b-instruct`) through the **same** prompt builder `jarvis.format_context_with_memories` uses, so evals test the real path.
  3. `judge.py`:
     - Use string matching first.
     - When that's ambiguous, ask a local LLM judge (`qwen2.5:14b-instruct` if it is pulled, else 7b) with a fixed rubric.
     - `--judge openai:gpt-4o-mini` is optional and **eval-only**, for publishing comparable numbers.
  4. `metrics.py` computes:
     - `answer_accuracy`, overall and per category.
     - `abstention_precision` = correct abstains / all abstains.
     - `abstention_recall` = correct abstains / cases that should abstain.
     - `temporal_accuracy`.
     - `update_accuracy`: the new value is present and the stale value absent.
     - `recall@1` and `recall@5`.
     - `citation_accuracy`: the quote is verified and `said_at` matches.
     - `latency_ms p50/p95`, for recall only and end to end.
  5. Write the output to `evals/memory/results/<git-sha>-<timestamp>.json`, including the model names, τ and the commit. `--update-baseline` writes `baseline.json`. This file is committed, and contains synthetic-case scores only.
  6. `--gate` exits non-zero when any metric drops more than 2 points below `baseline.json`, or when abstention precision falls below 0.85.
- **Tests:** `evals/memory/tests/test_metrics.py`, unit-testing the metric math on fixtures.
- **Acceptance:** `python evals/memory/run.py --json` prints a per-category table and writes a results file.

#### E2 Grow the suite to about 100 cases (1.5 d)
- **Goal:** cover every way memory can fail, not just the happy path.
- **Files:** `evals/memory/cases/<category>-NN.yaml`
- **Target mix:**

  | Category | Cases |
  |---|---|
  | single_fact | 15 |
  | multi_session | 12 |
  | temporal | 15 |
  | knowledge_update | 15 |
  | abstention (including "near-miss" cases where something related exists) | 15 |
  | multi_hop | 10 |
  | preference | 10 |
  | contradiction | 8 |
  | synthesis (v3: multi-source agree or disagree, from the old repo's research wedge) | 6 |
  | save (S1 aspects) | 10 |

- **Rules:**
  - Synthetic people and projects only. **No real personal data.**
  - Each case says what failure it catches, in its `why:` field.
  - Knowledge-update cases always include `answer_not_contains` for the stale value.
  - Temporal cases use relative `occurred_at` plus `cited_said_at`.
  - Contradiction cases have two speakers, which tests M0.5.
  - Include Hinglish and code-switching phrasing in at least 10 cases.
- **Seed:** start from the 30 cases in the earlier "Memory Engine Core" document, section "eval cases". The human can paste them in.
- **Acceptance:** 100 or more cases, every category has at least 8, and `run.py` runs them all in under 15 minutes on the 3090 box.

#### E3 LongMemEval and LoCoMo adapters (1.0 d)
- **Goal:** scores that can be compared with Mem0 and Zep.
- **Files:** `evals/memory/bench/longmemeval.py`, `evals/memory/bench/locomo.py`, `evals/memory/bench/README.md`
- **Data:**
  - Download at runtime into `~/.cache/lumen-evals/`. **Never commit the datasets.** Check each dataset's license before redistributing anything.
  - LongMemEval: github.com/xiaowu0162/LongMemEval, with the `oracle` and `S` variants (500 questions each).
  - LoCoMo: github.com/snap-research/locomo (10 long conversations). Report categories 1–4 (about 1,540 questions) and leave out the adversarial category, the same as the published Mem0 and Zep numbers.
- **Steps:**
  1. For each conversation or haystack:
     - `capture` every turn, with its session timestamp as `occurred_at` and the speaker name.
     - Drain extraction with the jobs worker.
     - Ask every question through `recall` and the answer prompt.
     - Score with `judge.py`.
  2. `--subset N` gives fast runs, for example 50 LongMemEval_S questions stratified by type. Run the full set before publishing.
  3. Report the per-type breakdown, plus tokens per query and latency. The LongMemEval types are single-session-user, single-session-assistant, single-session-preference, multi-session, knowledge-update, temporal-reasoning and abstention.
- **Acceptance:** `python -m evals.memory.bench.longmemeval --variant oracle --subset 50` produces a results JSON.
  - Oracle tests reading, not retrieval, so it should score high.
  - If it doesn't, the answer prompt is the bottleneck. Fix that before tuning retrieval.

#### E4 CI gate (0.5 d)
- **Goal:** a change that makes memory worse can't be merged.
- **Files:** `.github/workflows/memory.yml` (there is no `.github/` today)
- **Steps:**
  1. Create a job on `ubuntu-latest` with the service `pgvector/pgvector:pg17` and Python 3.12. It runs:
     - The migrations, up then down then up.
     - The unit tests.
     - `check_licenses.sh`.
     - `evals/memory/run.py --gate --fast`, where `--fast` uses hash embeddings and a stub reader, so only the deterministic retrieval and lineage metrics are checked.
  2. Add a self-hosted runner labelled `lumen-gpu` on the home server for the full evals with Ollama. Trigger it nightly and on `workflow_dispatch`.
- **Acceptance:** a PR that breaks supersede scoping (M0.5) fails CI.

---

### Phase 0.6 — Reranker, calibrated abstention and context pack

> **Why not an RRF threshold:** an RRF score is Σ wᵢ/(60+rankᵢ). With today's weights (`pg_recall.py:159-165`), even a memory ranked first in every channel scores at most (1+1+0.8+0.6+0.4)/61 ≈ **0.062**, or 5/61 ≈ 0.082 if all weights are 1. The status doc's τ = 0.18 can never be reached, so it would abstain on everything. RRF also only measures whether channels agree on the ranking, not whether anything is relevant. Abstention must use a calibrated relevance score.

#### R1 Local cross-encoder reranker (1.0 d)
- **Goal:** the biggest cheap jump in accuracy, plus a score abstention can trust.
- **Files:**
  - New: `app/adapters/rerank/reranker.py`
  - `app/adapters/storage/pg_recall.py`
  - `app/core/config.py`
  - `app/main.py` (load the model once in `lifespan`)
- **Dependency:**
  - `sentence-transformers` (Apache-2.0) with the model `BAAI/bge-reranker-v2-m3` (Apache-2.0). It is multilingual, so Hindi and Hinglish work.
  - Expect tens of milliseconds for about 30 pairs on a 3090. Measure it and record the number.
  - If you'd rather run it as a sidecar, TEI and infinity also serve `/rerank`. Check their licenses first.
- **Interface:**
  ```python
  class RerankerPort(Protocol):
      async def score(self, query: str, passages: list[str]) -> list[float]: ...  # sigmoid-normalised 0..1

  class CrossEncoderReranker(RerankerPort):
      def __init__(self, model: str = "BAAI/bge-reranker-v2-m3", device: str = "cuda",
                   max_length: int = 512, batch_size: int = 32): ...
      # CrossEncoder.predict runs in asyncio.to_thread
  ```
- **Steps:**
  1. Fuse with RRF to get the top N = 30 candidates.
  2. Build each passage: `statement` + " — said: " + the first citation quote (≤ 300 chars) + the `said_at` date.
  3. Rerank, then compute `final = rerank * lifecycle_boost`, where `lifecycle_boost ∈ [0.85, 1.0]` comes from recency and importance (the old temporal channel) and, after L1, the lane policy. Return the top-k by `final`.
  4. Store the rerank scores in `retrieval_traces.fused`.
  5. **Retrieval reinforcement (v3, from the old repo).** Port `_compute_memory_score` (`memory-engine/backend/app/services/retrieval.py:336-380`) into `app/domain/lifecycle.py`:
     ```python
     retention   = max(exp(-λ·age_said_days), exp(-λ·days_since_last_access))   # λ per lane after L1; 0.015/day before
     reinforce   = 1 + min(0.15, 0.05·log1p(access_count + reaffirm_count))
     lifecycle_boost = clamp(min_boost + (1-min_boost)·retention, min_boost, 1.0) · reinforce   # ∈ [0.85, 1.15]
     ```
     - After each recall, enqueue `jobs(kind='touch', payload={lineage_ids})`, which bumps `memory_signals.access_count` and `last_accessed_at`. Never write during the request.
     - The old repo's version ages from `created_at` and can scale a score down to 0.4×. Here it ages from `said_at` and has a floor, because **ranking never implies truth**.
  6. Test addition: `tests/test_lifecycle.py` checks that an accessed memory outranks an otherwise equal unaccessed one, that the boost stays inside [0.85, 1.15], and that recall makes no synchronous writes.
- **Tests:** `tests/test_reranker.py`, with a fake reranker for unit tests plus one `@pytest.mark.gpu` test using the real model.
- **Acceptance:** recall@1 on the E2 suite improves over the M0.7 baseline. Record the delta in `PLAN_NOTES.md`.

#### R2 Calibrated abstention (1.0 d)
- **Goal:** Lumen says "I don't know" when, and only when, it doesn't know.
- **Files:**
  - `app/adapters/storage/pg_recall.py`
  - `app/core/config.py` (`ABSTAIN_TAU`, loaded from `calibration.json`)
  - New: `evals/memory/calibrate.py`
  - New: `packages/memory-engine/calibration.json`
- **Steps:**
  1. Abstain when there are no candidates, or when `max(rerank) < ABSTAIN_TAU`. Return `confidence = max(rerank)`.
  2. In `calibrate.py`:
     - Run the E2 suite plus a LongMemEval_S dev slice, and collect `(max_rerank, should_answer)` pairs.
     - Sweep τ from 0.05 to 0.95. Pick the τ that maximises answer accuracy while keeping abstention precision ≥ 0.9.
     - Write τ, the model name and the date to `calibration.json`, and print the precision/recall table.
  3. Tune on one split and report on another (`--holdout 0.3`). **Never tune τ on the cases you publish.**
  4. Remove the provisional vector floor from M0.7, or keep it only as a cheap pre-filter well below τ.
- **Tests:** `tests/test_abstain_threshold.py`, with a fake reranker: scores below τ → abstain with `low_confidence`.
- **Acceptance:** on the held-out set, abstention precision is at least 0.9 and recall at least 0.7, and accuracy on answerable questions drops less than 2 points.

#### R3 Evidence-sufficiency check, optional (0.5 d)
- **Goal:** catch the case where the top memory is on-topic but doesn't actually answer the question.
- **File:** new `app/domain/sufficiency.py`
- **Steps:**
  1. For the top 3 results, ask the local LLM `"Does this quote answer the question? yes|partial|no"` with JSON format.
  2. If all three are "no", abstain with `abstain_reason='insufficient_evidence'`.
  3. Put it behind `RECALL_SUFFICIENCY=1`, because it adds a few hundred milliseconds.
- **Acceptance:** run the evals with it on and off. Keep it on only if abstention F1 improves by at least 3 points.

#### K1 Structured context pack and grounded-answer contract (1.0 d)
- **Goal:** the LLM gets organised, cited context instead of a flat top-k, and answers by a fixed contract. Ported from the old repo's `retrieval.build_context` and `chat.py` rules.
- **Files:**
  - New: `app/services/context_pack.py`
  - New: `app/api/v1/context.py`
  - New: `app/prompts/answer.md`
  - Edit: `apps/lumen-core/src/persona/jarvis.py` (`format_context_with_memories` → render the pack)
  - Edit: `evals/memory/answer.py` (use the same pack and prompt)
- **Contract:**
  ```python
  class ContextPack(BaseModel):
      status: Literal["ok", "abstain"]; abstain_reason: str | None; confidence: float | None
      query_plan: QueryPlan | None              # filled once Q1 exists
      directives: list[DirectiveItem]           # D1: pinned, by priority (empty until D1)
      entities: list[EntityBrief]               # name, aliases, profile summary (C1)
      memories: list[RecallItem]                # cited: quote, said_at, version, lane, epistemic
      by_type: dict[str, list[RecallItem]]      # decisions (with why/alternatives), facts, goals (owner/deadline),
                                                # constraints, actions, open questions
      contradictions: list[ContradictionItem]   # CONTRADICTS edges among returned memories, with both quotes
      timeline: list[TimelineItem]              # dated decisions, goals and events, ordered by said_at / deadline
      trace_id: UUID
  ```
  `POST /v1/context {query, context, limit, as_of, valid_at, include_private}` → `ContextPack`.
- **Steps:**
  1. Run recall (M0.7, R1, R2). On abstain, return a pack with `status='abstain'` and only the directives.
  2. Group the results by `type`. Pull the `attributes` from the S1 schema v2, so decisions show why and the alternatives.
  3. Pull `CONTRADICTS` edges where either side is in the result set, and attach both quotes.
  4. Build the timeline from the results' `said_at`, `valid_from` and `attributes.deadline`.
  5. Cap the token budget at 2,500 tokens by default, measured with a cheap length estimate. Drop the lowest-ranked memories first, never the directives or contradictions.
  6. Write `app/prompts/answer.md`, porting the old `chat.py` rules:
     - Cite every claim as `[M#]` with the date said.
     - Mark low-confidence items (< 0.7).
     - Name contradictions instead of choosing between them silently.
     - Directives are hard constraints.
     - If the pack has abstained or doesn't cover the question, say "I have no record of that".
     - Never use general knowledge as if it were memory.
  7. **Consensus mode:** when `QueryPlan.type_hints` contains `consensus` ("what do my sources agree or disagree on?"), group `claims` by memory and stance, and return `agree`, `disagree` and `only_one_source` lists.
- **Tests:**
  - `tests/test_context_pack.py`:
    - Grouping by type works, and decisions carry their why.
    - Contradictions are included with both quotes.
    - The token cap never drops directives.
    - Abstain still returns directives.
  - `evals`: the answer accuracy of the pack is at least the accuracy of the flat list from M0.7.
- **Acceptance:** `jarvis.py` and the evals reader both use `ContextPack`, and E2 answer accuracy does not regress. Record the delta.

---

### Phase 1 — Glass-box viewer

#### V1 Browse, history, trace and mutation API (1.0 d)
- **Goal:** everything the viewer, the vault and MCP need, on the new schema.
- **Files:**
  - New: `app/api/v1/memories.py`
  - New: `app/services/lineage_service.py`
  - Mount both in `main.py`
- **Endpoints**, all under `/v1` and all requiring the token:
  - `GET /memories/browse?q=&lane=&type=&epistemic=&status=active|pending|superseded|retracted&entity=&from=&to=&limit=50&offset=0`
    - Returns cards: statement, type, epistemic, confidence, `said_at`, `valid_from/to`, version, the primary citation (quote, `char_start/end`, source title) and the entities.
  - `GET /memories/{id}` returns the full card plus **all citations**, each with the raw event text window (±200 chars, so the UI can highlight the span).
  - `GET /lineages/{lineage_id}/history` returns every version in order, with `superseded_at`, `superseded_by`, the cause event and its actor.
  - `GET /recall/traces/{trace_id}` returns the per-channel ranks and the fused and rerank scores (from M0.7 and R1).
  - `PATCH /memories/{id}` with body `{statement, reason, base_version}` does a **correction** through `lineage_service.correct()` (see F2). It returns 409 if `base_version` isn't the current version.
  - `DELETE /memories/{id}?reason=&mode=retract|purge_text` does a **forget** through `lineage_service.forget()` (see F1). It returns 204.
  - `POST /memories/{id}/confirm` turns `pending_review` into a new active version with a `reaffirm` citation.
  - `GET /review` and `POST /review/{id}/resolve`, ported to the new `review_queue`.
  - `GET /outbox/stats`, ported.
- **Service signatures:**
  ```python
  async def correct(pool, memory_id: UUID, new_statement: str, *, actor: str, reason: str,
                    base_version: int, evidence_event_id: UUID | None = None,
                    new_object_value: Any | None = None) -> MemoryRow
  async def forget(pool, memory_id_or_lineage: UUID, *, actor: str, reason: str,
                   mode: Literal["retract", "purge_text"] = "retract") -> ForgetResult
  async def undo(pool, lineage_id: UUID, *, actor: str) -> MemoryRow
  ```
- **Tests:** `tests/test_memories_api.py`
  - Browse filters work.
  - The history order is right.
  - PATCH creates v2, and v1 gets `superseded_by`.
  - A stale `base_version` returns 409.
  - DELETE hides the memory from recall and browse (unless `status=retracted`).
  - A trace exists for each recall.
- **Acceptance:** the OpenAPI page lists every endpoint, and all the tests pass.

#### V2 Viewer UI (2.5 d)
- **Goal:** the launch demo, "memory you can see and trust".
- **Files:**
  - Replace `packages/memory-engine/sheldon/index.html` (today a 1 KB fragment) with `packages/memory-engine/viewer/`, containing `index.html`, `app.js` and `styles.css`.
  - Serve it from FastAPI at `/viewer` with `StaticFiles`.
- **Tech:**
  - No build step: vanilla JS with `preact` and `htm` (both MIT), vendored as ESM files. That keeps it offline and dependency-free.
  - The token is entered once and kept in `sessionStorage`. Never put it in the URL.
- **Screens:**
  1. **Feed and search:**
     - Cards with an epistemic badge (fact, belief, inference or decision), a confidence bar, "said on <date>", and the quote with the span highlighted.
     - Filters by lane, type and status.
  2. **Memory detail:** the full quote in its source window, all citations, a version timeline (v1 → v2 → … with reasons), and the related memories.
  3. **Actions:**
     - **Correct:** an inline edit that sends PATCH with `base_version`.
     - **Forget:** a confirm dialog that sends DELETE.
     - **Confirm:** for pending items.
     - **Undo:** for the last action.
  4. **Ask:** a query box that shows the answer *or* the abstain message. A "why" drawer shows the trace: channels, ranks and rerank scores.
  5. **Review inbox:** contradictions, failed extractions (M0.3) and vault conflicts (O2), with accept and reject.
  6. **v3 screens, re-implemented from the old repo's Next.js components** (`frontend/src/components/*.tsx`). Port the layout and interactions, not the code:
     - **Entity page** (from `ConceptPage.tsx`): name, aliases, description, mention count, current facts, linked memories and related entities.
     - **Merge proposals** (from `MergeProposals.tsx`): side-by-side cards with accept, reject and split.
     - **Import drop zone** (from `ImportModal.tsx` and `InlineImport.tsx`): files and URLs → I1, with live job status from `save-report`.
     - **Ask with streaming** (from `Chat.tsx`): SSE from `POST /v1/context` plus the local answer model, with `[M#]` citations linked to cards.
     - **Context switcher** (from `Sidebar.tsx` and `CollectionView.tsx`): the old "collections" map onto Lumen `contexts`.
     - **Directives** (D1): list, pin, edit and retract.
- **Safety:** render every model-produced string with `textContent` or `escapeHtml`. The old fragment already has `escapeHtml`; keep that pattern.
- **Tests:**
  - `tests/test_viewer_served.py`: `/viewer` returns 200 and the JS loads.
  - Optionally, a Playwright smoke test (`evals/ui/viewer.spec.ts`, Apache-2.0) if Node is available: capture, open, correct, check that v2 appears.
- **Acceptance:** the 60-second demo script (§1.3) works end to end in the viewer.

---

### Phase 2 — Conversational forget and correct

#### F1 Forget through lineage (1.0 d)
- **Goal:** "Forget that" works, leaves an audit trail and can be undone.
- **Files:**
  - `app/services/lineage_service.py`
  - New: `app/domain/intents.py`
  - `apps/lumen-core/src/runtime/session.py` (route intents)
  - `apps/lumen-core/src/persona/jarvis.py`
  - Migration `0007_redactions.py`
- **Semantics:**
  - `retract` (the default):
    - Set `status='retracted', valid_to=now()` on the current version. The trigger allows both.
    - Append the event `type='memory.tombstoned', tombstone=true, target_id=lineage_id`.
    - Write an outbox row `memory.retracted`.
    - The memory stays in history ("you told me to forget this on …") but never appears in recall.
  - `purge_text`, for secrets such as a phone number, also writes a `redactions` row:
    ```sql
    CREATE TABLE IF NOT EXISTS redactions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      event_id uuid NOT NULL REFERENCES events(id),
      char_start int NOT NULL, char_end int NOT NULL,
      reason text NOT NULL,
      created_event uuid NOT NULL REFERENCES events(id),
      created_at timestamptz NOT NULL DEFAULT now());
    ```
    - Every read of `events.content`, `chunks.text` or `citations.quote` goes through `redact(text, spans)`, so the quote shows `[redacted]`.
    - The raw bytes stay in the append-only log. Write the limitation in `PLAN_NOTES.md`: true erasure needs per-event encryption keys (crypto-shredding), which is a later item.
- **Intent detection** (`intents.py`):
  1. Rules first: "forget …", "delete that", "don't remember …", "remove my …".
  2. Then a JSON-format local-LLM classifier: `{intent: forget|correct|none, target_query, new_value}`.
  3. Resolve the target: first the memories cited in the previous answer (kept in session state), then `recall(target_query, limit=3)`. If the top rerank score is below τ, or the top two are close, **ask the user which one**. Never forget blindly.
  4. Confirm in the reply: "Forgotten: '<statement>' (said 12 Mar). Undo?"
  5. Undo creates a new version restoring the statement. It never un-retracts a row in place.
- **Tests:**
  - `tests/test_forget.py`:
    - Retract hides the memory from recall, and history still shows it.
    - Purge redacts quotes in browse and recall.
    - Ambiguity → a clarifying question instead of an action.
    - Undo creates v(n+1).
  - `apps/lumen-core/tests/test_forget_intent.py`
- **Acceptance:** "Forget my old phone number" removes it from recall. The viewer shows it as retracted with a reason, and after O1 the vault note moves to `_retracted/`.

#### F2 "Actually it's X" correction (1.0 d)
- **Goal:** corrections update memory in place through lineage, with no duplicates.
- **Files:** `lineage_service.correct()`, `intents.py`, `session.py`
- **Steps:**
  1. Detect the cue: "actually", "no, it's", "I meant", "correction:", "not X but Y". Hinglish forms like "nahi, X hai" are handled by the LLM classifier.
  2. Target the memory: use the memories cited in the previous answer first, then fall back to recall.
  3. `correct()` does the following in one transaction:
     - Insert v(n+1), copying the subject, predicate and type, with the new statement and `object_value`.
     - Close vn (`superseded_at`, `superseded_by`, `valid_to`).
     - Add a `SUPERSEDES` relation.
     - Add a citation to the **correction utterance's** event (`role='evidence'`).
     - Append a `memory.superseded` event and an outbox row.
     - Re-embed.
  4. Reply with a diff: "Updated: Postgres 16 → **Postgres 17** (v2). The previous value is kept in history."
- **Tests:** `tests/test_correct.py`
  - A correction → v2 is current, v1 is in history, and the citation points at the correction utterance.
  - Recall returns only v2, and the answer doesn't contain the old value.
  - Two concurrent corrections with the same `base_version` → one succeeds and the other gets 409. Use `SELECT … FOR UPDATE` on the current row.
- **Acceptance:** the knowledge_update category in E2 is at least 90%.

#### D1 Directives: the procedural memory tier (1.0 d)
- **Goal:** "I prefer short answers", "never suggest AGPL code" and "I'm building Lumen" are always applied without being asked for. Ported from the old repo's `UserDirective` and `api/directives.py`, stored in Lumen's existing `abstractions` table.
- **Files:**
  - New: `app/services/directives.py`
  - New: `app/api/v1/directives.py`
  - Migration `0008_directives.py`
  - Edits: K1 (`context_pack.py`), P1 (MCP), V2 (Directives screen), C1 (mining)
- **Migration 0008:**
  ```sql
  ALTER TABLE abstractions ADD COLUMN IF NOT EXISTS directive_type text
    CHECK (directive_type IS NULL OR directive_type IN ('preference','constraint','goal','style','context'));
  ALTER TABLE abstractions ADD COLUMN IF NOT EXISTS priority int CHECK (priority BETWEEN 1 AND 10);
  CREATE INDEX IF NOT EXISTS abstractions_directives_idx
    ON abstractions (context_id, priority DESC) WHERE kind = 'directive' AND status = 'active';
  ```
- **Rules:**
  - A directive is `abstractions(kind='directive', pinned=true, status='active')`, with `abstraction_sources` pointing at the memories that justify it.
  - Editing a directive makes a new row with `superseded_by` set on the old one. Never update in place.
  - **Sources:**
    - (a) Explicit: "remember that I always want…" → an intent in `intents.py` (F1) → an active directive.
    - (b) Implicit: S1 sees `type in (preference, constraint)` with `about_speaker=true` and confidence ≥ 0.8 → a `review_queue(kind='belief_promotion', proposal={directive…})` row.
    - (c) Mined: C1 finds patterns across many memories (the old `consolidation.py:66-87` prompt) → proposals.

    Only (a) becomes active without review.
  - Keep **at most 15 active directives** per context in the pack, ordered by priority. The viewer warns when the list would go over.
- **API:**
  - `GET/POST /v1/directives`
  - `PATCH /v1/directives/{id}` (creates a new version)
  - `DELETE /v1/directives/{id}` (retracts)
  - `POST /v1/directives/{id}/pin`
- **MCP:** the read-only resource `lumen://directives`, and the tool `directives()`.
- **Tests:** `tests/test_directives.py`
  - An explicit directive is active and appears first in the pack.
  - An implicit one becomes a proposal only.
  - An edit creates a new version.
  - The cap of 15 holds.
  - Directives with `sensitivity=secret` are never created.
- **Acceptance:** after "I prefer answers in short bullet points", the next answer through K1 follows it, and the viewer shows where the directive came from.

---

### Phase 2.5 — MCP, portability and ingestion

#### P1 MCP server (1.0 d)
- **Goal:** Cursor, Claude Code and any other agent can use Lumen's memory. It is also a strong open-source selling point.
- **Files:**
  - New: `packages/memory-engine/app/mcp/server.py`
  - New: `packages/memory-engine/docs/MCP.md`
  - `pyproject.toml`: add `mcp` (the official Python SDK, MIT) and the script `lumen-memory-mcp = "app.mcp.server:main"`
- **Design:**
  - Use the SDK's `FastMCP` helper.
  - The default `LUMEN_MCP_MODE=http` calls the HTTP API with the token, so permissions live in one place. `inproc` calls the service layer directly.
- **Tools:**
  ```python
  @mcp.tool()
  async def remember(text: str, source: str = "mcp", occurred_at: str | None = None,
                     lane: str | None = None) -> dict: ...      # capture + enqueue extract → {event_id}
  @mcp.tool()
  async def recall(query: str, limit: int = 5, as_of: str | None = None,
                   valid_at: str | None = None) -> dict: ...    # {status, results[quote, said_at, version], trace_id}
  @mcp.tool()
  async def forget(memory_id: str, reason: str, purge_text: bool = False) -> dict: ...
  @mcp.tool()
  async def correct(memory_id: str, new_statement: str, base_version: int, reason: str) -> dict: ...
  @mcp.tool()
  async def timeline(entity: str | None = None, lineage_id: str | None = None,
                     start: str | None = None, end: str | None = None) -> dict: ...
  @mcp.tool()
  async def context(query: str, limit: int = 8) -> dict: ...    # K1 ContextPack (private/secret excluded by default)
  @mcp.tool()
  async def directives() -> dict: ...                           # D1 active directives (read-only)
  ```
  Also expose the resources `lumen://memory/{id}`, `lumen://entity/{name}` and `lumen://directives`.
- **Transports:**
  - `lumen-memory-mcp --stdio`, for Cursor and Claude Code.
  - `lumen-memory-mcp --http --host 127.0.0.1 --port 8765`, using the SDK's streamable HTTP transport. It requires the bearer token.
- **Safety:**
  - The server is read-only by default (`MCP_ALLOW_WRITES=0`), so `remember`, `forget` and `correct` are refused until you turn writes on.
  - Writes are tagged `actor='mcp:<client name>'`.
  - Memories from external agents are stored as `epistemic='belief'` and `asserted_by='mcp:<client>'`, never as your own facts.
- **Docs:** `docs/MCP.md` has copy-paste config for Cursor (`.cursor/mcp.json`) and Claude Code (`claude mcp add lumen -- lumen-memory-mcp --stdio`).
- **Tests:** `tests/test_mcp_server.py`. Use the SDK's in-memory client session to call each tool, and check that the write tools are refused when `MCP_ALLOW_WRITES=0`.
- **Acceptance:** from Claude Code, "what editor do I use?" returns Neovim with its quote and date.

#### X1 Export, import and replay (1.0 d)
- **Goal:** your memory is portable and can be restored. This is required before any open-source user trusts it with their life. The idea comes from the old repo's `/export` and `/import` (`api/memory.py:617-960`).
- **Files:**
  - New: `app/portability/export.py`, `app/portability/import_.py`, `app/portability/replay.py`
  - CLI: `lumen-memory export|import|replay`
- **Format:** a directory, or `.tar.zst`, with:
  - `manifest.json`: schema revision, engine version, counts, sha256 per file, `created_at`
  - one `*.jsonl` file per table: `contexts, sources, events, chunks, entities, entity_aliases, memories (all versions), citations, claims, memory_relations, abstractions, abstraction_sources, memory_signals, review_queue, redactions`
  - Optionally `--with-embeddings`. Otherwise embeddings are re-computed on import through the `embed` jobs.
- **Steps:**
  1. Export with `COPY (SELECT … ORDER BY primary key) TO STDOUT` per table in one `REPEATABLE READ` transaction. Write rows as canonical JSON (sorted keys, ISO times in UTC).
  2. Import only into an **empty** DB at the same Alembic revision. Disable no triggers. Load in FK order, and verify the hash chain (S1) after the load.
  3. **Replay** (`lumen-memory replay --from-events`) rebuilds L1/L2 from `events` alone by re-running the save pipeline. It is used to migrate to a new extraction model or schema, and the diff against the old state is written to a report.
  4. `--encrypt` uses `cryptography` (Apache-2.0/BSD) Fernet with a passphrase. `--exclude-secret` drops `sensitivity='secret'` memories and redacts their quotes.
- **Tests:** `tests/test_portability.py`
  - export → import into a fresh DB → export gives **byte-identical** files except `manifest.created_at`.
  - The chain verifies after import.
  - Importing into a non-empty DB is refused.
  - `--exclude-secret` leaves no secret text anywhere in the bundle (grep the bundle for a fixture OTP).
- **Acceptance:** the round-trip test passes in CI. A nightly cron example in `docs/BACKUP.md` writes an encrypted export to the server's backup disk.

#### I1 Ingest anything: files, pages and transcripts (1.5 d)
- **Goal:** "dump once" works for documents and links, not just chat lines. Ported from the old repo's `services/ingestion.py` plus lumen-core's `src/vault/document_parser.py`, into the engine.
- **Files:**
  - New: `app/ingest/__init__.py` (`ingest(source) -> CaptureResult`)
  - New parsers: `app/ingest/parsers/{pdf,markdown,html,youtube,spreadsheet,image,text}.py`
  - New: `app/api/v1/ingest.py`
  - Move `apps/lumen-core/src/vault/document_parser.py` into `app/ingest/parsers/`, and make lumen-core's `document_vault.py` call the engine API.
- **Dependencies** (all permissive):
  - `pypdf` (BSD), replacing PyPDF2.
  - `markdown-it-py` (MIT).
  - `beautifulsoup4` (MIT) with `lxml` (BSD).
  - `youtube-transcript-api` (MIT) as an optional `[web]` extra.
  - `openpyxl` (MIT) for spreadsheets.
  - Images use the lumen-core parser's current approach. Check its dependencies' licenses when you move it.
- **API:**
  - `POST /v1/ingest/file` (multipart, ≤ 50 MB)
  - `POST /v1/ingest/url {url}`. YouTube is detected by URL, as in the old `is_youtube_url`.
  - `POST /v1/ingest/text {content, title}`
- **Steps:**
  1. Each parser returns `ParsedDoc{text, title, kind, metadata, published_at}`. The **plain text is what gets stored in `events.content`**, so citations index into exactly what was parsed.
  2. Create one CAPTURE event per document, with `source.kind` set to file, web or import, plus the `uri` and the author when known. Use the paragraph-aware chunker from S1 stage 1. Extraction runs per chunk.
  3. Fetching a URL is the only network call. It happens only when you ask, with a 15 s timeout, a size cap and a robots-agnostic plain GET. It never sends content anywhere.
  4. Write source metadata (page, section heading, timestamp in the video) into `chunks.metadata` jsonb (the column is added in S1 migration 0006), so a citation can show "p. 4" or "12:31".
  5. Dedupe through `sources.source_key` (S1). Re-ingesting the same URL with changed content creates a new event, and the extracted memories reaffirm or supersede as usual.
- **Tests:** `tests/test_ingest.py`, with fixtures in `tests/fixtures/ingest/` (a 2-page PDF, a Markdown file with headings, a saved HTML page, a transcript JSON, a small XLSX)
  - The text is parsed.
  - Chunk offsets index the stored text exactly.
  - Citations carry the page, heading or timestamp.
  - Re-ingesting identical content is idempotent.
- **Acceptance:**
  - Dropping a PDF in the viewer produces cited memories with page numbers.
  - A YouTube link produces memories cited to their timestamps.
  - No ingest path makes a network call except the explicit URL fetch.

---

### Phase 2.7 — Obsidian two-way vault, graph and timeline

#### O1 Vault projection v2 from the new schema (1.5 d)
- **Goal:** the vault is a faithful, human-readable mirror of memory that Obsidian's graph and backlinks understand.
- **Files:**
  - New: `packages/memory-engine/app/vault/projector.py`. This replaces the memory parts of `apps/lumen-core/src/vault/vault_sync.py` (573 lines). The vault is the engine's mirror, so it lives with the engine.
  - New: `app/vault/templates/{memory,entity,daily}.md.j2`, using `jinja2` (BSD-3).
  - `apps/lumen-core/src/vault/outbox_worker.py` keeps only the digest, research and sentinel exports, re-based to link `[[Entity]]` notes.
- **Layout** (`LUMEN_VAULT_PATH`, default `./vault`):
  ```
  vault/
    entities/<Canonical Name>.md
    memories/<yyyy>/<slug>-<lineage8>.md
    memories/inbox/                 # new notes you write by hand (O2)
    daily/<yyyy-mm-dd>.md
    _retracted/<slug>-<lineage8>.md
    _conflicts/<slug>-<lineage8>.conflict.md
    .lumen/state.json               # last projected outbox id, file hashes
  ```
- **Memory note:**
  ```markdown
  ---
  lumen_id: 2f1c…            # current version id
  lineage_id: 9ab3…
  version: 2
  type: fact
  epistemic: fact
  status: active             # editable: set to 'retracted' to forget
  lane: work                 # editable
  said_at: 2026-03-12T09:14:00+05:30
  valid_from: 2026-03-12
  valid_to:
  entities: ["[[Lumen]]", "[[PostgreSQL]]"]
  supersedes: ["[[lumen-uses-postgres-16-9ab3]]"]
  content_hash: sha256:…     # hash of the editable block below
  ---
  <!-- Managed by Lumen. Only the block between the markers, `status` and `lane` are editable. -->
  # Lumen uses PostgreSQL 17
  <!-- lumen:editable:start -->
  Lumen uses PostgreSQL 17.
  <!-- lumen:editable:end -->

  > "actually it's Postgres 17, not 16" — chat, 12 Mar 2026

  ## History
  - v1 (10 Mar 2026): Lumen uses PostgreSQL 16 → superseded 12 Mar
  ```
- **Entity note:**
  - Frontmatter: `entity_id`, `kind` and `aliases`. Obsidian reads `aliases` natively, so links by alias resolve.
  - A "Current facts" list of active memories where the entity is the subject.
  - Typed relations as Dataview-style inline fields, for example `- USES:: [[PostgreSQL]]`. They stay readable without plugins.
  - A "Mentioned in" list of wikilinks to memory notes. Obsidian's backlinks pane and graph view then work natively.
- **Steps:**
  1. Consume the `outbox` topics `memory.created`, `memory.superseded`, `memory.retracted` and `entity.upserted`. For each one, re-render the lineage note, the entity notes for its entities, and that day's daily note.
  2. Write atomically (a temp file, then `os.replace`). Never write outside the vault; keep the existing `_safe_vault_target` check.
  3. Wikilinks come from the **entities table**: build an alias → canonical map once per batch, and link the longest match first. Delete `_wikilinkify`'s hard-coded list (`vault_sync.py:53-67`).
  4. Add the CLI `lumen-memory vault rebuild`, which regenerates everything from the DB deterministically.
- **Tests:** `tests/test_vault_projector.py`, in a tmp dir:
  - A created memory → a note with valid frontmatter, and the entity note links back to it.
  - A supersede → the note's history and `version` update.
  - A retract → the note moves to `_retracted/`.
  - Running `rebuild` twice gives identical bytes.
- **Acceptance:** open the vault in Obsidian. The graph view shows entity ↔ memory links, and the backlinks on `[[Lumen]]` list its memories.

#### O2 Vault → engine sync with lineage and conflicts (1.5 d)
- **Goal:** editing a note in Obsidian is a real, cited correction, and no edit is ever lost.
- **Files:**
  - New: `app/vault/watcher.py`, using `watchdog` (Apache-2.0)
  - New: `app/vault/reconcile.py`
  - Uses `python-frontmatter` (MIT)
- **Rules:**
  - Only three things are editable: the text between `lumen:editable:start` and `end`, and the frontmatter fields `status` and `lane`. Everything else is regenerated and overwritten. Each note says so in its header comment.
  - On a file change:
    1. Parse the note and compute the hash of the editable block.
    2. If it equals the frontmatter `content_hash`, ignore the change. That was our own write.
    3. Otherwise, read the DB's current version for the `lineage_id`:
       - **If `frontmatter.version == db.version`:**
         - Capture the edited text as a new CAPTURE event with `source.kind='note'` and `uri=vault path`.
         - Call `lineage_service.correct(..., actor='vault', reason='edited in Obsidian', base_version=version, evidence_event_id=<that event>)`. The evidence span verifies because it is the whole edited block.
       - **If `frontmatter.version < db.version`** (the engine changed it in the meantime), it is a **conflict**:
         - Write `_conflicts/<…>.conflict.md` with both versions side by side.
         - Add `review_queue(kind='vault_conflict')`.
         - Leave the DB unchanged, and re-render the main note from the DB.
         - The viewer's review inbox resolves it (keep mine, keep theirs, or a merged text), and the resolution is a normal correction.
       - **If `status: retracted` was set in the frontmatter,** call `forget()`.
       - **If `lane` changed,** update the lane column. No new version is needed (L1).
  - **If the note was deleted in Obsidian, do not forget.** Re-create it and log the event. Deleting files is too easy to do by accident. Forgetting needs an explicit status edit or the viewer.
  - **A new note in `memories/inbox/` with no `lumen_id`** is treated as `capture(source.kind='note')` and extracted. The inbox file is then replaced by links to the memories it produced.
  - Debounce for 2 s per file, and ignore temp files, `.obsidian/` and `.trash/`.
- **Tests:** `tests/test_vault_two_way.py`
  - Edit the block → v2 in the DB with `actor='vault'`, cited to the note event.
  - An edit on a stale version → a conflict file plus a review row, and the DB is unchanged.
  - Our own write → no loop (the outbox count is stable after the projection).
  - A deleted file → re-created, not forgotten.
  - An inbox note → captured and extracted.
- **Acceptance:** edit a fact in Obsidian, then ask Lumen. The new value comes back, cited to the note edit, and the viewer history shows "edited in Obsidian".

#### O3 Graph and timeline in the viewer (1.5 d)
- **Goal:** see how memory connects and how it changed over time, without Obsidian.
- **Files:**
  - New API: `app/api/v1/graph.py`
  - Viewer: `viewer/graph.js`, `viewer/timeline.js`
  - Vendored `force-graph` (MIT, the vanilla build of the `react-force-graph-2d` the old repo's `GraphView.tsx` used)
- **API:**
  - `GET /v1/graph?entity=&depth=1..3&lane=&as_of=` returns `{nodes: [{id, label, kind, degree}], edges: [{source, target, type, memory_id, valid_from, valid_to}]}`.
    - Nodes are entities.
    - Edges come from memories that have both `subject_id` and `object_id`, labelled with the predicate. `memory_relations` between memories are folded onto their subjects.
    - `as_of` uses the M0.6 transaction-time predicate.
    - Use a recursive CTE capped at depth 3 and 300 nodes.
  - `GET /v1/timeline?entity=&lineage_id=&from=&to=` returns versions as `{lineage_id, version, statement, valid_from, valid_to, recorded_at, status}`.
- **UI:**
  - **Graph:** a force-directed layout (`force-graph`), styled like the old `GraphView.tsx`.
    - Click a node to open the entity panel (aliases, current facts and the entity profile from C1).
    - Click an edge to open the memory card.
    - An `as_of` slider re-queries, so you can see the graph as it was in March.
  - **Timeline:** one row per lineage, with a bar from `valid_from` to `valid_to` and supersede arrows between versions. Hover shows the quote.
  - Both views follow the viewer's lane and type filters.
- **Tests:**
  - `tests/test_graph_api.py`:
    - Depth and node caps hold.
    - `as_of` hides edges recorded later.
    - A superseded edge is gone now but shows with an `as_of` before the supersede.
  - `tests/test_timeline_api.py`
- **Acceptance:** in the demo, after "actually it's Postgres 17", the timeline shows v1 ending and v2 starting, and the graph edge label changes.

#### O4 Daily notes and morning brief (0.5 d)
- **Goal:** one note per day that shows what Lumen learned, changed and forgot.
- **Files:**
  - New: `app/vault/daily.py`
  - The lumen-core morning briefing (it has a test in `apps/lumen-core/tests/test_morning_briefing.py`) reads from `GET /v1/timeline?from=<yesterday>`.
- **Steps:**
  1. `daily/<date>.md` has the sections Captured, Changed (with diffs), Forgotten, Open reviews and Brief.
  2. The local LLM generates the brief from the day's changes plus open goals and actions (`type in ('goal','action')`). **Every line cites** a `[[memory note]]`. Drop any line that has no citation.
- **Tests:** `tests/test_daily_note.py`
- **Acceptance:** the daily note renders for a fixture day, and every brief bullet has a wikilink.

---

### Phase 3 — Lanes, sessions, consolidation, query understanding

#### L1 Per-lane policy (1.0 d)
- **Note:** the `lane` column and enum already exist from S1 (`0006_save_aspects`). L1 adds the policies and makes ranking use them.
- **Goal:** personal facts, project decisions and code context get different extraction rules and different ranking decay.
- **Why a column, not partitions:** memories move between lanes, such as a work decision said in chat. One table with a `lane` column, an index and a policy table keeps queries and lineage simple.
- **Migration `0009_lane_policies.py`:**
  ```sql
  -- memory_lane enum + memories.lane + index were created in 0006_save_aspects (S1)
  CREATE TABLE IF NOT EXISTS lane_policies (
    lane memory_lane PRIMARY KEY,
    decay_per_day real NOT NULL CHECK (decay_per_day >= 0),  -- λ in 1/day; ranking only
    min_boost real NOT NULL DEFAULT 0.85,                     -- floor: old facts never sink below this
    extraction_prompt text NOT NULL DEFAULT 'default',
    auto_commit_min_conf real NOT NULL DEFAULT 0.7);
  INSERT INTO lane_policies VALUES
    ('chat', 0.05, 0.80, 'chat', 0.8), ('work', 0.01, 0.85, 'work', 0.7),
    ('code', 0.0, 1.0, 'code', 0.7),   ('personal', 0.002, 0.9, 'default', 0.7)
  ON CONFLICT DO NOTHING;
  ```
- **Rules:**
  - **Decay affects ranking only:** `boost = max(min_boost, exp(-λ · age_days))`, applied after the rerank. It **never** changes `status`, `valid_to` or truth. Old doesn't mean false.
  - Age is measured from `max(said_at, memory_signals.last_reaffirmed_at)`.
  - The extractor chooses the lane, so add `lane` to `schemas/extract.json`. The source kind gives a prior; for example, a `file` from a repo suggests `code`.
- **Tests:** `tests/test_lanes.py`
  - The chat-lane boost drops faster than the work lane.
  - Code never decays.
  - A 2-year-old personal fact is still returned when it is the only match, because the floor holds.
  - Changing the lane does not create a version.
- **Acceptance:** no regression on E2, and multi_session improves or holds.

#### L2 Session synopses and continuity (1.0 d)
- **Goal:** a new session starts already knowing what was decided last time.
- **Files:**
  - New: `app/services/session_service.py`
  - API: `POST /v1/sessions/{id}/close` and `GET /v1/sessions/prime?project=&branch=&limit=3`
  - lumen-core `runtime/session.py` hooks: `/exit` and the voice sleep timeout → close; session start → prime.
- **Steps:**
  1. On close:
     - Collect the session's events.
     - The local LLM writes a synopsis of at most 150 words.
     - Store it in `abstractions` (kind `session_synopsis`), with `abstraction_sources` pointing to the memories it touched, plus an embedding.
  2. On prime: embed `project + branch + open tasks`, and return the top 3 synopses by similarity × recency, each citing its session date.
  3. Synopses are summaries, so prompts label them as `inference`, never as facts.
- **Tests:** `tests/test_sessions.py`
- **Acceptance:** a new session about the same project starts with the last decision visible, without being asked.

#### C1 Nightly consolidation (2.5 d)
- **Goal:** memory gets cleaner overnight instead of piling up duplicates and hidden conflicts.
- **Files:**
  - Rewrite `app/domain/consolidator.py` (161 lines today, built on the legacy schema).
  - A jobs kind `consolidate`, scheduled at 03:00 Asia/Kolkata by the jobs worker.
  - New: `app/domain/judge.py`, a small local-model judge.
- **Passes:**
  1. **Dedupe:**
     - Find active memory pairs in the same lane and subject with cosine > 0.92 and trigram > 0.6.
     - Ask the judge `same|different|contradicts`.
     - If `same`: keep the older lineage, add a `reaffirm` citation and bump `memory_signals.reaffirm_count`. Retract the duplicate with `reason='merged into <lineage>'`, which is reversible.
  2. **Entity merge proposals:** entities with alias overlap or name similarity > 0.85 go to `review_queue(kind='entity_merge')`. **Never auto-merge people.**
  3. **Entity profiles:** for each entity that changed today, regenerate its `abstractions` profile, a cited summary of its current facts. The vault entity note shows it.
  4. **Cross-memory contradictions:**
     - For each changed memory, find candidates on the same entity (any predicate, by vector similarity).
     - Ask the judge (`qwen2.5:7b`, JSON format) "do A and B contradict?" with both quotes.
     - If it says "contradicts" with confidence ≥ 0.8, add a `CONTRADICTS` relation and a review item.
     - Never supersede automatically.
  5. **Beliefs → facts:** a belief the user reaffirms 2 or more times becomes a `belief_promotion` proposal.
  6. **Relation typing (v3, from the old repo's `linking.py:24-50`).** For each new memory, take its k = 8 nearest active neighbours in the same context. The judge (one call per new memory) returns `{memory_id, relation: SUPPORTS|CONTRADICTS|PART_OF|RELATED_TO|DEPENDS_ON|DERIVED_FROM|FOLLOWS_UP|none, explanation, confidence}`.
     - Write edges with confidence ≥ 0.75.
     - `CONTRADICTS` edges always also create a review item (pass 4).
     - Migration `0010_relation_follows_up.py`: `ALTER TYPE relation_type ADD VALUE IF NOT EXISTS 'FOLLOWS_UP';`
  7. **Sleep-worker synthesis (v3, from `consolidation.py:88-330`):**
     - Cluster the last 24 hours of memories by shared entities (≥ 3 memories per cluster, at most 10 clusters).
     - Synthesise each cluster into `abstractions(kind='summary')`, with title, summary, key points and the contradictions it noticed. Use `abstraction_sources` for provenance.
     - Summaries are `inference` and are never recalled as facts. K1 shows them under "summaries".
     - Port the old `_SYNTHESIS_SYSTEM` prompt.
  8. **Directive mining (v3, from `consolidation.py:66-87, 330-410`):** this produces D1 **proposals** only, never active directives.
  9. **Report:** write a SYSTEM event `consolidation.completed`, with counts per pass in its payload. This replaces the old repo's `consolidation_jobs` table. Add a section to the daily note.
- **Budget:** cap judge calls per night (500 by default), and process the most recently changed items first.
- **Tests:** `tests/test_consolidation_v2.py`, with a fake judge:
  - Duplicates merge, and the merge can be reversed.
  - People are never auto-merged.
  - A contradiction creates a review row and no supersede.
  - The budget is respected.
- **Acceptance:** the contradiction category in E2 is at least 85%, and the nightly run on 10k memories finishes in under 20 minutes on the server.

#### Q1 Query understanding (1.5 d)
- **Goal:** handle "last month", "he", and questions that need two hops.
- **Files:**
  - New: `app/domain/query_plan.py`
  - Used by `pg_recall.recall()`, and the plan is stored in `retrieval_traces.plan` (the column already exists)
- **Plan:**
  ```python
  class QueryPlan(BaseModel):
      original: str
      rewritten: str                                 # coreference resolved
      sub_queries: list[str]                         # decomposition for multi-hop / "and"
      time_range: tuple[datetime, datetime] | None   # "last month", "in March", "before the move"
      as_of: datetime | None                         # "what did you think on 1 May"
      entities: list[str]
      type_hints: list[str]                          # preference / decision / goal …
  ```
- **Steps:**
  0. **Intent parser (v3, from `memory-engine/backend/app/services/retrieval.py:38-47, 179-200`).** Port `_INTENT_SYSTEM` (intent, key_terms, time_filter, focus) as the LLM branch of `QueryPlan`.
     - `focus` → `type_hints`.
     - Add the intent `consensus` for "what do my sources agree or disagree on?", which K1 handles.
     - Run it only when the rule-based classifier can't decide.
  1. **Time ranges:** rules plus `dateparser`, relative to now in the user's timezone ("last month", "in March", "2 weeks ago").
     - Filter on overlap with `valid_from`/`valid_to`.
     - If the filter empties the candidate set, fall back to boosting instead of filtering.
  2. **Coreference:**
     - Pass the last 3 conversation turns plus the entities the last answer cited.
     - The local LLM rewrites "he", "that project" and "it", with JSON output and a 300 ms budget.
     - Skip it when there are no pronouns or deictic words.
  3. **Decomposition:** for multi-hop questions ("where does the person who recommended Neovim work?"), split on conjunctions or use the LLM. Recall each sub-query, merge with RRF, and rerank against the original query.
  4. **Protect latency:** skip the LLM steps entirely for short factual queries, decided by a cheap rule-based classifier.
- **Tests:**
  - `tests/test_query_plan.py`: a table of 30 phrasings with their expected ranges and entities.
  - `tests/test_multihop_recall.py`
- **Acceptance:**
  - Temporal accuracy ≥ 85% and multi_hop ≥ 75% on E2.
  - p95 recall latency stays under 300 ms when the LLM steps are skipped, and under 900 ms when they run.

---

### Launch (1.5 d)
1. Run the full LongMemEval_S, LongMemEval oracle and LoCoMo (categories 1–4) twice:
   - (a) with the fully local reader and judge.
   - (b) with the same reader and judge as each published baseline, using the eval-only cloud-judge flag.
2. Write `packages/memory-engine/BENCHMARKS.md`:
   - Tables with the exact commands, model names, hardware, dates and τ.
   - The baselines from §1.3, each with its paper citation and its reader and judge. Note that vendor numbers are self-reported and disputed.
   - An honest explanation of the gaps.
3. Record the 60-second demo: capture → correct → ask "days later" (using `occurred_at` offsets) → viewer lineage → the Obsidian note.
4. Write the `README.md` quickstart:
   ```bash
   docker compose up -d postgres
   ollama pull nomic-embed-text qwen2.5:7b-instruct
   pip install -e packages/memory-engine
   lumen-memory serve
   ```
   Add the MCP config, and `docs/ARCHITECTURE.md` with the topology diagram.
5. Final checks:
   - The license check is green.
   - The M0.1 history check is clean.
   - `HOST` defaults to loopback.
   - The token is mandatory.
   - `rg` finds no personal names.

---

## 5. Out of memory-engine scope (moved to `apps/lumen-core`)

Do these only after the memory engine launches. They must use memory only through the HTTP API or MCP, never by importing engine internals.
- **Phase 4, the hands:**
  - macOS automation through the accessibility tree, following the `vercel-labs/agent-desktop` pattern. Check its license before taking any code.
  - A mixed loop that tries the API first, then the shell, then the GUI.
  - The existing Two-Man Rule gate (`apps/lumen-core/src/tools/gatekeeper.py`) guards destructive actions.
- **Phase 5, the runtime:**
  - Durable, checkpointed tasks in lumen-core's own tables. They can reuse the `jobs` and `FOR UPDATE SKIP LOCKED` pattern from M0.8, and resume after a reboot.
  - Per-subagent tool lists, budgets and traces.
- **Reasoning providers:** lumen-core's cloud providers (`src/router/model_router.py`) are a separate decision. The memory engine must not depend on them.

---

## 6. Definition of done (the memory engine)

- [ ] The Phase 0 exit gate passed: no personal data in git history, a loopback bind, a mandatory token, and no cloud calls in core.
- [ ] Every save runs the 5-stage pipeline (§1.4), and every memory carries all 8 aspects, including lane and sensitivity. Unsupported memories are dropped with a reason in `save-report`.
- [ ] Every capture becomes memories through the worker, and every failure is visible in review. Nothing fails silently.
- [ ] Every recalled memory carries a verified quote, `said_at`, `valid_from/to` and its version. Unverified memories are never recalled as truth.
- [ ] Abstention is calibrated on the reranker (τ in `calibration.json`), with ≥ 0.9 precision on held-out data.
- [ ] The viewer has browse, detail, history, trace, correct, forget, undo, review, graph and timeline.
- [ ] Conversational forget and correct work through lineage, with undo, and never silently overwrite.
- [ ] The MCP server works over stdio and HTTP, is read-only by default, and is documented for Cursor and Claude Code.
- [ ] The Obsidian vault is rebuildable and two-way, with entity-driven wikilinks, backlinks, daily notes and conflict files.
- [ ] Lanes with ranking-only decay (per day, with floors), session synopses, nightly consolidation with a contradiction judge, and query understanding are all in place.
- [ ] About 100 internal cases plus the LongMemEval and LoCoMo adapters run, with the CI gate on every PR and a nightly GPU run.
- [ ] **v3 merge:** the context pack is used by Jarvis, the evals and MCP; directives are pinned and cited, and mined ones go to review; retrieval reinforcement is on (ranking only); entity canonicalization has two thresholds with merge, reject and split; decisions carry why and alternatives; export → import is byte-identical and encrypted backup works; PDF, Markdown, web and YouTube ingestion cites page, heading or timestamp; nightly synthesis and relation typing run. Nothing from the old repo's Celery, SQLAlchemy, litellm or Next.js stack is in the tree.
- [ ] `BENCHMARKS.md` is published, with honest local and comparable rows next to the Mem0 and Zep baselines, and the 60-second demo is recorded.

---

## 7. Appendix

### 7.1 Verification commands
```bash
# migrations (up, down one, up)
LUMEN_ENV=test alembic -c packages/memory-engine/alembic.ini upgrade head
LUMEN_ENV=test alembic -c packages/memory-engine/alembic.ini downgrade -1
LUMEN_ENV=test alembic -c packages/memory-engine/alembic.ini upgrade head
# unit tests
PYTHONPATH=packages/memory-engine .venv/bin/pytest packages/memory-engine/tests -q
# lumen-core memory-facing tests
PYTHONPATH=apps/lumen-core .venv/bin/pytest apps/lumen-core/tests -q -k "memory or vault or briefing or session"
# evals
.venv/bin/python evals/memory/run.py --gate
.venv/bin/python -m evals.memory.bench.longmemeval --variant s --subset 50
.venv/bin/python -m evals.memory.bench.locomo --subset 200
.venv/bin/python evals/memory/calibrate.py --holdout 0.3
# hygiene
bash packages/memory-engine/scripts/check_licenses.sh
rg -n -i "test-token-secret|0\.0\.0\.0|lumen_secure_pass|2026-11-21T10:00|openai|groq|litellm" packages/memory-engine/app evals
git log --all --oneline -- '*backup*.sql'
# smoke test
export LUMEN_MEMORY_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
lumen-memory serve &        # binds 127.0.0.1:8000
curl -s -H "Authorization: Bearer $LUMEN_MEMORY_TOKEN" http://127.0.0.1:8000/health
```

### 7.2 Migrations added by this plan
| Rev | Content |
|---|---|
| 0002_dead_letter | `review_queue.kind` gains `failed_extraction` and `vault_conflict`; adds `extraction_attempts` |
| 0003_embedding_meta | `memories.embedding_model`, added to the trigger's free-list |
| 0004_current_view | `current_memories` = active only; adds `pending_memories` |
| 0005_outbox_cols | `outbox.processed_at`, `outbox.last_error` |
| 0006_save_aspects (S1) | the `memory_lane` and `sensitivity` enums; `memories.lane` and `memories.sensitivity` (added to the free-list); `events.speaker`, `prev_hash` and `chain_hash`; `chunks.metadata`; `entities.description`; `sources.source_key`; `save_runs` |
| 0007_redactions (F1) | `redactions` |
| 0008_directives (D1) | `abstractions.directive_type`, `abstractions.priority`, the directives index |
| 0009_lane_policies (L1) | `lane_policies` |
| 0010_relation_follows_up (C1) | `relation_type` gains `FOLLOWS_UP` |

### 7.3 Dependencies added by this plan (all permissive)
| Package | License | Used in |
|---|---|---|
| `sentence-transformers` | Apache-2.0 | R1 |
| `BAAI/bge-reranker-v2-m3` (model) | Apache-2.0 | R1 |
| `mcp` (official Python SDK) | MIT | P1 |
| `watchdog` | Apache-2.0 | O2 |
| `python-frontmatter` | MIT | O1–O2 |
| `jinja2` | BSD-3-Clause | O1 |
| force-graph (vendored) | MIT | O3 |
| `pypdf` | BSD-3-Clause | I1 |
| `markdown-it-py` | MIT | I1 |
| `beautifulsoup4` + `lxml` | MIT / BSD-3 | I1 |
| `youtube-transcript-api` (optional `[web]` extra) | MIT | I1 |
| `openpyxl` | MIT | I1 |
| `cryptography` | Apache-2.0 / BSD | X1 |
| preact + htm (vendored) | MIT / Apache-2.0 | V2 |
| `pip-licenses` (dev only) | MIT | M0.4 |
| `git-filter-repo` (a one-off tool, not a dependency) | MIT | M0.1 |

**Removed:** `unclecode-litellm` (an unofficial fork), and the OpenAI and Groq code paths in core. **Not ported from the old repo:** Celery, Redis, SQLAlchemy, `litellm==1.49.0`, PyPDF2, `psycopg2-binary`, `requests`, Next.js.

> **License-policy note:** `httpx`, `uvicorn`, `dateparser` and `jinja2` are BSD-licensed. A literal "Apache-2.0 or MIT only" rule would exclude them. Adopt **"permissive only (MIT, Apache-2.0, BSD); no GPL or AGPL"** as the written policy in `CONTRIBUTING.md`.
