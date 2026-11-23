# 🗺️ LUMEN MEMORY ENGINE: ARCHITECTURAL COMPASS, CURRENT REALITY & EXECUTION BLUEPRINT

> **Thesis:** Memory → Context → Intelligence.  
> LLMs generate transient intelligence; Lumen supplies permanent, bi-temporal continuity.  
> *"Dump once → never re-explain → never silently overwrite."*

---

## 📑 Table of Contents
1. [Core Philosophy & Architecture Topology](#1-core-philosophy--architecture-topology)
2. [What Is Done & Verified (The Solid Ground)](#2-what-is-done--verified-the-solid-ground)
   - [2.1 The 17-Table PostgreSQL Schema](#21-the-17-table-postgresql-schema)
   - [2.2 The 5-Stage Ingestion Pipeline](#22-the-5-stage-ingestion-pipeline)
   - [2.3 The 5-Channel Hybrid Recall Pipeline](#23-the-5-channel-hybrid-recall-pipeline)
   - [2.4 Transactional Outbox & Obsidian Vault Sync](#24-transactional-outbox--obsidian-vault-sync)
   - [2.5 Local Offline Hardware Acceleration](#25-local-offline-hardware-acceleration)
3. [The 6 Critical Flaws & Vulnerabilities (Audit Findings)](#3-the-6-critical-flaws--vulnerabilities-audit-findings)
4. [The Phased Build Order & Milestones](#4-the-phased-build-order--milestones)
   - [Phase 0: The Hardening Gate (Critical Bug Fixes)](#phase-0-the-hardening-gate-critical-bug-fixes)
   - [Phase 1: The Glass-Box Live Memory Viewer](#phase-1-the-glass-box-live-memory-viewer)
   - [Phase 2: Conversational "Forget" & In-Place Lineage Correction](#phase-2-conversational-forget--in-place-lineage-correction)
   - [Phase 3: Tri-Lane Partitioning & Session Continuity](#phase-3-tri-lane-partitioning--session-continuity)
   - [Phase 4: The Hands (Desktop Accessibility & Mixed Loop)](#phase-4-the-hands-desktop-accessibility--mixed-loop)
   - [Phase 5: The Runtime (Durable Tasks & Agent MCP Adapter)](#phase-5-the-runtime-durable-tasks--agent-mcp-adapter)
5. [Verification & Test Commands](#5-verification--test-commands)

---

## 1. Core Philosophy & Architecture Topology

Lumen's Memory Engine is a standalone, hexagonal microservice built with FastAPI, PostgreSQL 16 (`pgvector`), and local Ollama inference. It models human epistemic memory: distinguishing between raw sensory input, structured facts, subjective beliefs, and evolving relational graphs.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              LUMEN MEMORY TOPOLOGY                                     │
└────────────────────────────────────────────────────────────────────────────────────────┘

  [Sensory Inputs: Voice / Chat / Files / CLI / Web]
                         │
                         ▼
  ┌────────────────────────────────────────────────────────────────────────────────────┐
  │ 1. L0 RAW CAPTURE LAYER (<20ms, Zero LLM Overhead)                                 │
  │    • Strict Append-Only Event Log (Postgres Trigger: `events_append_only`)          │
  │    • Tamper-Evident SHA-256 Hashing & Chunker Coordinate Slices                     │
  │    • Tables: `sources`, `events`, `chunks`                                         │
  └─────────────────────────────────┬──────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌────────────────────────────────────────────────────────────────────────────────────┐
  │ 2. L1 CANONICAL & EVIDENCE LAYER                                                   │
  │    • Local Model Extraction (Ollama Qwen 2.5 7B via JSON Schema)                   │
  │    • Verbatim Evidence Verification (RapidFuzz Character-Level Coordinates)        │
  │    • Bi-Temporal Validity (`valid_from` to `valid_to`, `status`, `lineage_id`)     │
  │    • Tables: `entities`, `entity_aliases`, `memories`, `citations`, `claims`       │
  └─────────────────────────────────┬──────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌────────────────────────────────────────────────────────────────────────────────────┐
  │ 3. L2 RELATIONAL & KNOWLEDGE GRAPH LAYER                                           │
  │    • Controlled Predicate Ontology (`USES`, `PREFERS`, `DEPENDS_ON`, `SUPPORTS`)   │
  │    • Two-Tier Conflict Gating: Tier-A (Auto-Commit) vs Tier-B (Human Review)       │
  │    • Tables: `predicates`, `triples`, `memory_relations`, `abstractions`           │
  └─────────────────────────────────┬──────────────────────────────────────────────────┘
                                    │
                         ┌──────────┴──────────┐
                         ▼                     ▼
  ┌──────────────────────────────┐     ┌───────────────────────────────────────────────┐
  │ 5-CHANNEL HYBRID RECALL      │     │ TRANSACTIONAL OUTBOX & OBSIDIAN SYNC          │
  │ • Vector (768-d HNSW Cosine) │     │ • `outbox` table with PostgreSQL `pg_notify`  │
  │ • BM25 Lexical Full-Text     │     │ • Decoupled async worker drains to Markdown   │
  │ • Entity & Alias Matching    │     │ • Bidirectional `[[Wikilinks]]` in Obsidian   │
  │ • 1-Hop Graph Expansion      │     │ • Daily Briefings, Episode Synopses, Dossiers │
  │ • Temporal Ebbinghaus Decay  │     │                                               │
  │ ↳ Fused via RRF (k=60)       │     │                                               │
  └──────────────────────────────┘     └───────────────────────────────────────────────┘
```

---

## 2. What Is Done & Verified (The Solid Ground)

### 2.1 The 17-Table PostgreSQL Schema
The database operates on PostgreSQL 16 with `pgvector` enabled, managed by Alembic migrations (`0001_core.sql` and `0002_compat_graph_outbox_facts.sql`):

| Table Name | Architectural Layer | Primary Function |
|---|---|---|
| `sources` | L0 Raw | Origin registry (`chat`, `voice`, `file`, `web`, `api`) with metadata |
| `events` | L0 Raw | Append-only raw ledger with SHA-256 hashes and idempotency keys |
| `chunks` | L0 Raw | Character-coordinate text slices with tsvectors for full-text search |
| `entities` | L1 Canonical | Canonical entity nodes with 768-d embeddings and categories |
| `entity_aliases` | L1 Canonical | Normalized lookup table mapping colloquial names to canonical IDs |
| `memories` | L1 Canonical | Bi-temporal versioned memory assertions with lineage tracking |
| `citations` | L1 Evidence | Exact character-span evidence linking memories to raw events |
| `claims` | L1 Epistemic | Individual assertion claims with confidence weights |
| `predicates` | L2 Relational | Controlled, extensible relationship ontology |
| `triples` | L2 Relational | Directed knowledge graph edges with bi-temporal validity |
| `memory_relations` | L2 Relational | Directed relations between memories (`SUPERSEDES`, `SUPPORTS`) |
| `abstractions` | L2 Abstract | Compressed entity profiles, user summaries, and behavioral directives |
| `abstraction_sources` | L2 Abstract | Lineage references linking memories to higher-level abstractions |
| `review_queue` | Integrity Gate | Two-Man Rule review buffer for detected contradictions |
| `outbox` | Async Outbox | Transactional outbox with `pg_notify` for Obsidian vault sync |
| `retrieval_traces` | Observability | Audit traces recording RRF rank components and query execution |
| `memory_signals` | Reinforcement | Recency and user feedback reinforcement signals |

---

### 2.2 The 5-Stage Ingestion Pipeline
1. **L0 Synchronous Capture (<20ms):**
   - Ingests raw interaction to `events` table immediately.
   - Enforced by database trigger: `events_append_only` (raises exception on `UPDATE` or `DELETE`).
2. **Local Model Structured Extraction:**
   - Evaluated by Ollama `qwen2.5:7b-instruct` running on Apple Silicon Metal GPU.
   - Validated strictly against Draft 2020-12 JSON Schema (`schemas/extract.json`).
   - Normalizes model idiosyncrasies (singular `"memory"`, nested entities, casing).
3. **Verbatim Evidence Verification (RapidFuzz Span Matching):**
   - Every extracted assertion must carry verbatim evidence quotes.
   - RapidFuzz matches character coordinates (`start_char`, `end_char`) against the raw event text.
   - Any hallucinated assertion lacking verbatim source text is dropped.
4. **Entity & Contradiction Resolution:**
   - Entity names are matched against canonical names and `entity_aliases`.
   - **Tier A (Reinforcing/New):** Auto-committed directly into active memory.
   - **Tier B (Contradictory Functional Predicate):** Staged in `review_queue` for operator approval.
5. **Canonical Versioning & Lineage Tracking:**
   - Previous versions receive `valid_to = now()`, `status = superseded`, `superseded_by = new_id`.
   - Both rows retain identical `lineage_id` with incremented `version` (v1 $\rightarrow$ v2 $\rightarrow$ v3).
   - Enqueues `outbox` record for Obsidian vault replication.

---

### 2.3 The 5-Channel Hybrid Recall Pipeline
Retrieval runs across 5 distinct channels in a single round-trip, fused via Reciprocal Rank Fusion ($RRF, k=60$):

$$\text{RRF Score}(d) = \sum_{c \in \text{Channels}} \frac{1}{60 + \text{rank}_c(d)}$$

1. **Dense Vector Channel:** Cosine similarity via `pgvector` HNSW index on 768-d `nomic-embed-text` embeddings.
2. **Lexical / BM25 Channel:** Full-text search with `ts_rank_cd` on English lexemes + trigram similarity (`pg_trgm`).
3. **Entity Match Channel:** Exact matches against canonical entities and registered aliases (`entity_aliases`).
4. **Graph Expansion Channel:** 1-hop traversal across typed relational edges (`SUPPORTS`, `USES`, `DEPENDS_ON`, `PREFERS`).
5. **Temporal Decay Channel:** Validity filtering (`as_of`, `valid_from` to `valid_to`) combined with Ebbinghaus exponential recency decay ($S = e^{-\lambda \Delta t}$).

---

### 2.4 Transactional Outbox & Obsidian Vault Sync
- Completely decouples database write transactions from disk I/O.
- Background worker listens via `pg_notify` on `memory_outbox_channel` with fallback polling.
- Generates clean Obsidian Markdown with bidirectional `[[Wikilinks]]`:
  - `vault/entities/`: Canonical entity cards with inbound and outbound graph connections.
  - `vault/memories/`: Atomic memory cards with character-level citations.
  - `vault/digests/`: Morning intelligence digests and daily briefings.
  - `vault/research/`: Deep research dossiers with claim cards and source citations.
- **Current Live Status:** **809 outbox items processed, 0 failed, 0 pending.**

---

### 2.5 Local Offline Hardware Acceleration
- **LLM Extraction:** Ollama `qwen2.5:7b-instruct` (Metal GPU accelerated, ~35 tokens/sec).
- **Embeddings:** `nomic-embed-text` (768 dimensions, sub-15ms inference).
- **Zero API Rate Limits:** Fully offline; immune to external 429 rate limits.
- **Test Pass Rate:** **21 / 21 Unit Tests passing (100%)**, **3 / 3 Golden Epistemic Evals passing (100%)**.

---

## 3. The 6 Critical Flaws & Vulnerabilities (Audit Findings)

Before advancing to UI and desktop automation, the following 6 flaws must be addressed:

### 🔴 Flaw 1: Recall Abstention Deficit (Never Saying "I Don't Know")
- **The Issue:** When queried about a concept with no matching records in memory, hybrid recall returns either empty results or low-similarity matches. The agent loop fails to recognize that relevance is below the noise floor and attempts to answer anyway, frequently hallucinating plausible assumptions.
- **Root Cause:** No absolute confidence cutoff or abstention signal in `app/api/recall.py`.
- **Target Fix:** Enforce an explicit **Abstention Gate**: if $\max(\text{RRF Score}) < \tau_{\text{abstain}}$, recall explicitly returns `status: "ABSTAIN"`. The JARVIS persona prompt enforces: *"I have no record of that in my memory, Sir."*

---

### 🔴 Flaw 2: Silent Extraction Failures
- **The Issue:** If local Ollama output fails JSON validation or encounters schema errors after retries, `extraction.py` catches the error and returns `{"entities": [], "memories": []}`.
- **Root Cause:** The raw interaction is saved to `events`, but the failed extraction is swallowed silently. The operator has no visibility into lost memories.
- **Target Fix:** Dead-Letter Staging. When extraction fails, write `events.status = 'FAILED'`, stage the raw text into `review_queue` as `FAILED_EXTRACTION`, and surface an immediate alert.

---

### 🟡 Flaw 3: The Terminal Prison (No Live Visual Memory Viewer)
- **The Issue:** There is no visual web interface to browse, search, inspect character citations, edit, or forget memories outside of opening raw Markdown files in Obsidian.
- **Target Fix (inspired by `agentmemory`):** A lightweight, reactive web dashboard on `localhost:3000` (or `sheldon/index.html`) displaying memory cards, verbatim quotes, and inline edit/forget actions.

---

### 🟡 Flaw 4: Duplicate Accumulation on Conversational Corrections
- **The Issue:** Saying *"Forget that"* or *"Actually it's X"* often creates a new memory without cleanly retiring the old one in place, causing conflicting duplicates in semantic search.
- **Target Fix:** Conversational invalidation handlers that locate the target memory via lineage ID, mark `valid_to = now()`, status `SUPERSEDED`, and link a `SUPERSEDES` relation with an audit trace.

---

### 🟡 Flaw 5: Monolithic Context (No Memory Lanes)
- **The Issue:** Personal trivia, architecture decisions, and code snippets are all dumped into the same pool with identical extraction rules and decay rates.
- **Target Fix (inspired by `Hy-Memory`):** Tri-lane partitioning:
  1. `chat` (Personal): High decay, extracts habits, relationships, and preferences.
  2. `work` (Projects/Decisions): Moderate decay, extracts architectural choices, deadlines, and goals.
  3. `code` (Technical): Zero decay, extracts function signatures, dependencies, and bug root causes.

---

### 🟡 Flaw 6: Cold-Start Amnesia (No Cross-Session Continuity)
- **The Issue:** Every new terminal session begins cold. Relevant context from yesterday must be queried manually.
- **Target Fix (inspired by `claude-mem`):**
  - Context Compactor writes an **Executive Session Synopsis** on session termination.
  - On new session boot, Lumen automatically loads the top-3 most relevant past session summaries into working context based on current git branch and recent project activity.

---

## 4. The Phased Build Order & Milestones

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 THE BUILD ORDER                                        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ PHASE 0: The Hardening Gate (Fix Flaws 1 & 2 First)                                    │
│   ├── Milestone 0.1: Recall Honest Abstention Gate (Zero Hallucination / "I Don't Know")│
│   └── Milestone 0.2: Extraction Dead-Letter Staging into Review Queue                   │
│                                                                                        │
│ PHASE 1: The Glass-Box Memory Viewer (Fix Flaw 3)                                      │
│   ├── Milestone 1.1: FastAPI REST endpoints (/v1/memories/browse, /v1/memories/{id})   │
│   └── Milestone 1.2: Lightweight React/HTML5 Live Viewer (Search, Quotes, Edit, Forget)│
│                                                                                        │
│ PHASE 2: In-Place Correction & Forget Engine (Fix Flaw 4)                             │
│   ├── Milestone 2.1: Conversational "Forget that" / "Delete fact" Invalidation Handler │
│   └── Milestone 2.2: Conversational "Actually it's X" In-Place Lineage Superseding     │
│                                                                                        │
│ PHASE 3: Tri-Lane Partitioning & Session Continuity (Fix Flaws 5 & 6)                  │
│   ├── Milestone 3.1: Chat / Work / Code Lane Split with Lane-Specific Decay Curves     │
│   └── Milestone 3.2: Cross-Session Continuity Engine (claude-mem Session Bootstrapping)│
│                                                                                        │
│ PHASE 4: The Hands (Desktop Accessibility & Mixed Computer-Use Loop)                   │
│   ├── Milestone 4.1: macOS Accessibility Tree (agent-desktop / sub-50ms offline GUI)   │
│   └── Milestone 4.2: Mixed Loop Routing (API → Shell → GUI + Two-Man Rule)             │
│                                                                                        │
│ PHASE 5: The Runtime (Durable Tasks & Agent Interop)                                   │
│   ├── Milestone 5.1: Durable Checkpointed Tasks (eve / LightAgent)                     │
│   └── Milestone 5.2: Universal Model Context Protocol (MCP) Server for Cursor & Claude │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### Phase 0: The Hardening Gate (Critical Bug Fixes)
- **Milestone 0.1: Recall Honest Abstention:**
  - Define calibrated confidence threshold ($\tau = 0.18$) in `app/api/recall.py`.
  - Return `status: "ABSTAIN"` when results are below the cutoff.
  - In `jarvis.py`, enforce: *"I have no record of that in my memory, Sir."*
  - Add test case: `evals/memory/cases/abstention-01.yaml`.
- **Milestone 0.2: Extraction Dead-Letter Staging:**
  - In `app/domain/extraction.py`, log failed extractions to `review_queue` as `FAILED_EXTRACTION`.
  - Update `events.status = 'FAILED'` with diagnostic error trace.
  - Return HTTP 422 with structured error payload instead of silent empty lists.

---

### Phase 1: The Glass-Box Live Memory Viewer
- **Milestone 1.1: Backend Browse & Mutation API:**
  - `GET /v1/memories/browse?lane=&search=&limit=&offset=`
  - `PATCH /v1/memories/{id}` (inline statement correction)
  - `DELETE /v1/memories/{id}` (soft-delete / invalidate)
- **Milestone 1.2: Interactive Web UI (`sheldon/index.html`):**
  - Dark-mode, fast visual interface inspired by `agentmemory`.
  - Displays memory card, confidence meter, epistemic badge, and highlighted verbatim citation quote.
  - One-click **"Correct"** and **"Forget"** buttons.

---

### Phase 2: Conversational "Forget" & In-Place Lineage Correction
- **Milestone 2.1: Natural Language Forgetting:**
  - Handlers for *"Forget my phone number"*, *"Delete that last preference"*.
  - Bi-temporally invalidates active row (`valid_to = now()`, `status = superseded`).
- **Milestone 2.2: Conversational In-Place Correction:**
  - Handlers for *"Actually, it's PostgreSQL 17, not 16"*.
  - Locates original memory by lineage ID, increments version to `v2`, creates `SUPERSEDES` relation edge, and replicates update to Obsidian vault.

---

### Phase 3: Tri-Lane Partitioning & Session Continuity
- **Milestone 3.1: Tri-Lane Memory Split (`Hy-Memory`):**
  - Partition schema into `chat`, `work`, and `code` contexts.
  - Distinct extraction prompts and decay rates:
    - `chat`: $\lambda = 0.05$ (rapid decay for ephemeral chat).
    - `work`: $\lambda = 0.01$ (slow decay for project decisions).
    - `code`: $\lambda = 0.00$ (zero decay for technical facts and codebase rules).
- **Milestone 3.2: Cross-Session Continuity (`claude-mem`):**
  - On `/exit` or voice sleep timeout, generate an **Executive Session Synopsis**.
  - On session start, prime the top-3 most relevant synopses based on active git branch and project tasks.

---

### Phase 4: The Hands (Desktop Accessibility & Mixed Loop)
- **Milestone 4.1: macOS Accessibility Tree Automation (`agent-desktop`):**
  - Sub-50ms offline GUI inspection via macOS Accessibility APIs (no screenshots, no vision tokens).
  - Inspect UI elements, click buttons, read accessibility labels in native Mac apps.
- **Milestone 4.2: Mixed-Loop Computer Use (`OpenGCUA`):**
  - Intelligent tiered execution:
    $$\text{Target Action} \xrightarrow{\text{1. API (Fastest)}} \xrightarrow{\text{2. Shell (Direct)}} \xrightarrow{\text{3. GUI (Fallback)}}$$
  - Two-Man Rule gatekeeper strictly enforced for destructive operations.

---

### Phase 5: The Runtime (Durable Tasks & Agent MCP Adapter)
- **Milestone 5.1: Durable Checkpointed Tasks (`eve`/`LightAgent`):**
  - Every step of multi-stage tasks checkpointed to PostgreSQL.
  - Server restart or reboot automatically resumes in-flight tasks from last verified state.
- **Milestone 5.2: Universal Model Context Protocol (MCP) Server:**
  - Expose Lumen's memory engine over standard MCP (`stdio` / `SSE`).
  - Allows external agents (Cursor, Claude Code, Windsurf) to query Lumen's long-term memory directly.

---

## 5. Verification & Test Commands

```bash
# 1. Run all Memory Engine Unit Tests (21/21)
PYTHONPATH=packages/memory-engine ./.venv/bin/pytest packages/memory-engine/tests -v

# 2. Run Golden Epistemic Memory Evals (3/3 offline on Ollama)
./.venv/bin/python evals/memory/run.py

# 3. Run Core System & Voice Pipeline Tests (26/26)
PYTHONPATH=apps/lumen-core ./.venv/bin/pytest apps/lumen-core/tests/test_morning_briefing.py apps/lumen-core/tests/test_session.py apps/lumen-core/tests/test_voice_pipeline.py apps/lumen-core/tests/test_wake_word_daemon.py -v

# 4. Check Transactional Outbox Sync Status
curl -s -H "Authorization: Bearer test-token-secret" http://localhost:8000/api/v1/outbox/stats
```
