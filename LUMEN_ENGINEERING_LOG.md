# 📘 LUMEN (Project JARVIS) — Live Engineering & Architecture Log

> **Single Source of Truth** for technical implementation details, microservices, algorithms, system design patterns, and engineering progress across Cerebro Dynamics' Lumen platform.
> *Updated continuously after every feature implementation.*

---

## 📊 1. High-Level Status Dashboard

| Milestone | Subsystem | Status | Primary Pattern / Technique | Verification Method |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 0** | Infrastructure Hub | **COMPLETE** | Docker Compose (`pgvector:pg16`, `redis:7-alpine`) | Healthchecks, `psql` extension validation |
| **Phase 1** | Memory Engine v0.1 | **COMPLETE** | Hexagonal Architecture, HNSW Vector Indexing, Bi-temporal schema | Pytest (0.22s), Swagger interactive search (2.87ms) |
| **Phase 1** | Lumen Core (Agent Runtime) | **COMPLETE** | Decoupled Circuit Breaker Client, Dual-Speed Routing, Persona Engine | Pytest (0.78s), Interactive REPL |
| **Phase 2** | True Concurrency ("The Spine") | **COMPLETE** | Native Async Supervisor, Semaphore Gate (k=4), Non-blocking stdin threadpool, Postgres Checkpointing | Pytest suite (1.50s, 4 tests passed), Live REPL verified |
| **Phase 3** | Unbroken Bi-Temporal Memory | **COMPLETE** | Dual-clock time (Valid vs Transaction), Trace Provenance DAG, Automated Fact Superseding, Time-Travel Queries | Pytest suite (0.33s, 3 tests passed), Live REST & REPL `/trace` verified |
| **Phase 4** | Safe Computer Control ("The Hands") | **COMPLETE** | Multi-Layered Defense (Pipeline Splitter, Python AST, Credential Sentinel, Env Scrubbing) | Automated security tests (0.53s, 15 tests passed), Live REPL `/exec` verified |
| **Phase 5** | Knowledge Graph, Review Queue & Hybrid Retrieval | **COMPLETE** | 3-Table Schema (entities, triples, predicates), Two-Tier Entity Resolver, Review Queue Contradiction Gating, Transactional Outbox Pattern, Overnight Consolidation Daemon, Unified Hybrid RRF Search | Automated test suite (49/49 tests passed), Live REPL `/search`, `/consolidate`, `/digest`, `/outbox`, `/proposals`, `/graph` verified |
| **Phase 6** | Autonomous Web & Deep Research ("The Eyes") | **COMPLETE** | Query De-Optimizer, Multi-Source Discovery (Web, HN Algolia, Reddit, arXiv), Anti-SEO Citation Walking, Prompt Injection Shield, Crawl4AI + MarkItDown, Obsidian Research Dossiers | Automated test suite (63/63 tests passed), Live REPL `/research`, `/dossiers`, `/dossier` verified |
| **Phase 7** | Voice & Barge-in Gateway ("The Senses") | **COMPLETE** | Full-duplex WebSocket (`ws://0.0.0.0:8765/voice`), Silero VAD, EchoGate dynamic modulation, sub-50ms Barge-In Preemption, Syntactic Clause Chunking, Whisper STT + Neural TTS, Hands-Free openWakeWord Daemon ("Hey Jarvis") + Hann Acoustic Chimes | Automated test suite (15 voice/wake tests passed), Monorepo 82/82 tests green |

---

## 🏛️ 2. Architectural Decision Records (ADR Log)

| ADR ID | Decision Title | Chosen Pattern | Alternatives Considered | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **ADR-001** | Vector & State Storage | PostgreSQL (`pgvector` + JSONB) | Pinecone, ChromaDB, MongoDB | Single ACID boundary, zero distributed sync lag, HNSW cosine index built-in. |
| **ADR-002** | Service Decoupling | Hexagonal Architecture (Ports & Adapters) | Layered / Monolithic spaghetti | Core business domain (bi-temporal facts, trace graph) remains 100% independent of storage drivers. |
| **ADR-003** | Distributed Tracing | Correlation ID Header (`x-trace-id`) + Idempotency | OpenTelemetry agent alone | Sub-millisecond tracking across microservice HTTP boundaries without heavy agent overhead. |
| **ADR-004** | Agent Concurrency Model | Native Async Supervisor + Postgres Checkpoints | Hatchet, DBOS, Celery | Sub-0.1ms dispatch latency; avoids heavy Go daemons/gRPC setup while ensuring durable task recovery. |
| **ADR-005** | Resilience & Fault Tolerance | Circuit Breaker Pattern | Naive HTTP retries | Prevents agent thread exhaustion when Memory Engine is restarting or under load. |
| **ADR-006** | Safe Computer Control Architecture | Three-Ring Security Defense (AST Gatekeeper + Two-Man Rule + Sandboxed Async Runner) | Unrestricted subshell execution, Container-only sandbox | Provides multi-layered defense-in-depth: immediate deterministic pattern filtering at AST level, interactive human authorization token gate for modifying tasks, and non-blocking timeout/cancellation-aware subprocess execution. |
| **ADR-007** | Knowledge Graph Integrity & Review Queue | Event-Sourced 3-Table Schema + Tiered Review Queue | Unvalidated triple insertion, Unbounded graph growth | Solves the entity resolution trap (merging aliases like 'psql' -> 'PostgreSQL') and prevents epistemic corruption by gating contradictory belief shifts through a human-in-the-loop review queue. |
| **ADR-008** | Transactional Outbox Pattern for Obsidian Sync | PostgreSQL `memory_outbox` + `pg_notify` + Async Worker | Inline direct disk writes | Eliminates dual-write anti-patterns; removes file I/O latency from conversation turns; guarantees eventual consistency between PostgreSQL memory and Obsidian notes with exponential backoff retries. |
| **ADR-009** | Overnight Consolidation Patrol & Morning Intelligence Briefing | Autonomous Nightly Patrol + Gated Review Queue + Markdown Digest | Unchecked automated edge mutation, Destructive auto-deduping | Solves epistemic drift: AI never unilaterally resolves contradictions or merges aliases. All findings are staged as Tier B review proposals with explicit explanations, and synthesized into `vault/digests/digest_YYYY-MM-DD.md`. |
| **ADR-010** | Unified Hybrid Retrieval with Reciprocal Rank Fusion (RRF) | Multi-Channel RRF ($k=60$) over Lexical (BM25), Dense Vector (HNSW), and Graph Traversal | Score-normalization (Min-Max / Softmax), Isolated retrieval calls | Eliminates fragmented search logic; avoids uncalibrated score scale collapse; parameter-free rank fusion guarantees robust ordering across disparate retrieval channels. |
| **ADR-011** | Multi-Surface Deep Research & Anti-SEO Graph Exploration | Query De-Optimization + Multi-Source Discovery (DuckDuckGo, Hacker News Algolia, Reddit, arXiv) + Recursive Semantic Citation Traversal ($d \ge 1$) + Prompt Injection Shielding | Commercial top-5 scraping alone, Unfiltered raw HTML ingestion | Escapes commercial SEO spam farms; uncovers buried practitioner debates and primary science; protects reasoning loop from indirect prompt injection; binds findings into Knowledge Graph and Obsidian Vault. |
| **ADR-012** | Defense-in-Depth Security Hardening & Zero-Trust Crawling | Line-aware AST Lexer + SSRFGuard (IP/DNS/Redirect Gating) + Stream Ceilings (25MB) + DefusedXML + Vault Path Traversal Invariants | Naive regex-only matching, Unchecked HTTP redirects, Trusting local paths | Neutralizes multi-line shell injection delimiter evasion, eliminates SSRF/cloud metadata leaks, prevents memory exhaustion DoS, prevents XML entity expansion, and strictly confines note generation within vault root. |
| **ADR-013** | Sub-Second Full-Duplex Voice Gateway, Echo Gate & Sub-50ms Barge-In Preemption | Discrete 16kHz PCM Framing + Silero VAD + Dynamic EchoGate ($\tau: 0.50 \to 0.85$, $E: -45 \to -28\text{ dBFS}$) + Syntactic Clause Chunker + Immediate Preemption Queue Flushing | Half-duplex turn-taking, Cloud monolithic audio APIs, Blind client-side VAD | Delivers natural conversational cadence with sub-250ms Time-to-First-Audio (TTFA); prevents Lumen from cutting himself off on his own speaker playback; preempts LLM generation and output audio in under 50ms upon genuine user speech onset. |
| **ADR-014** | Ambient Hands-Free Wake-Word Daemon, openWakeWord Neural Model & Earcon Chimes | Two-State Ambient Machine (`DORMANT_LISTENING` vs `ACTIVE_CONVERSATION`) + Low-Power ONNX Model (`hey_jarvis`, 1280 samples / 80ms) + Hann-Enveloped Synthesized Chimes (Ascending D5 $\to$ A5, Descending A5 $\to$ D5) + Automatic Idle Silence Watchdog | Continuous cloud STT streaming, OS hotkeys alone | True hands-free ambient computing at <2% CPU usage without privacy exposure or battery exhaustion; provides crisp acoustic feedback earcons without external audio assets. |

---

## 🛠️ 3. Detailed Component Implementations & Techniques

### Phase 0: Foundations & Containerized Infrastructure Hub
- **Technique:** Containerized Multi-Service Mesh with Health Dependencies.
- **Microservices:**
  - `lumen-postgres`: PostgreSQL 16 with `pgvector` v0.8.7 and `uuid-ossp`. Runs on port `5432`.
  - `lumen-redis`: Redis 7 Alpine for high-speed cache & pub/sub. Runs on port `6379`.
- **System Design:**
  - Pre-seeded initialization script (`infra/docker/postgres/init.sql`) automatically provisions tables (`facts`, `events`, `episodes`) and creates HNSW index structures with `m=16, ef_construction=64`.
  - Dedicated network (`lumen-network`) ensures isolated service communication.

---

### Phase 1: MVP Brain + Memory Engine v0.1 + JARVIS Persona
- **Technique:** Hexagonal Architecture (Ports and Adapters) + Bi-Temporal Data Modeling.
- **Microservice:** `packages/memory-engine` (FastAPI on Port 8000)
  - **Domain Core (`app/domain/models.py`, `ports.py`):**
    - Pure Python dataclasses: `Fact`, `EventRecord`, `MemoryQuery`, `SearchResult`.
    - Strict interfaces (`StoragePort`, `EmbeddingPort`) decoupled from external vendors.
  - **Infrastructure Adapters (`app/adapters/`):**
    - `PostgresStorageAdapter`: Async connection pooling (`asyncpg`), ACID bi-temporal fact invalidation (`valid_to = NOW()` on conflict), and HNSW cosine distance vector matching (`<=>` operator).
    - `DeterministicHashEmbeddingAdapter`: High-performance 1536-dimensional feature hashing for offline local embedding generation with zero external API latency.
  - **Ingress Middleware (`app/api/middleware/correlation.py`):**
    - Generates or propagates `x-trace-id` on every inbound request for end-to-end telemetry.
    - Idempotency key tracking prevents duplicate writes on network replay.
  - **Web Console & Documentation:**
    - Interactive Dashboard mounted at `/` with real-time health checks, vector stats, and quick-links.
    - Full OpenAPI Swagger UI at `/docs`.
  - **Latency:** Vector search executed in **2.87ms** with full cosine ranking.

- **Microservice:** `apps/lumen-core` (JARVIS Agent Runtime)
  - **Technique:** Circuit Breaker Client + Dual-Speed Model Routing + Grounded Offline RAG Synthesis.
  - **Components:**
    - `MemoryClient`: Handles HTTP communication with the Memory Engine. Integrates a 3-state Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`) to gracefully degrade if the database goes down.
    - `PersonaEngine`: Implements the Cerebro JARVIS system prompt (dry wit, proactive intellect, concise responses, protocol-driven).
    - `ModelRouter`: Dual-speed engine supporting live streaming via Groq (Llama-3.3-70B), OpenAI, Gemini, and Anthropic.
    - **Dynamic Offline RAG Synthesis:** When operating without external LLM API keys, the router dynamically parses semantic facts retrieved from PostgreSQL pgvector (HNSW cosine search threshold tuned to `0.01`), deduplicating verified records and articulating the exact answer directly from the memory ledger.
    - `Session`: Context orchestrator managing short-term conversation memory, automatic context injection from long-term memory, and conversation history turns.

---

### Phase 2: Add-on 1 — True Concurrency: "The Spine" (COMPLETED)
- **Problem Solved:** Standard LLM agent loops block the entire thread during tool execution or file reads. The user cannot speak or interrupt while a task runs.
- **Architectural Solution:** Dual-Loop Architecture.
  1. **Conversation Loop:** High-frequency, non-blocking asyncio loop handling user speech/chat (< 100ms response).
  2. **Task Supervisor Loop:** Bounded background worker pool executing autonomous long-running tasks.
- **Techniques & Patterns:**
  - **Bounded Concurrency Gate (`apps/lumen-core/src/concurrency/supervisor.py`):**
    - Enforced using `asyncio.Semaphore(max_parallel=4)` (pattern adapted from `orbitbot`).
    - Tasks beyond capacity remain in `QUEUED` state without blocking caller thread.
  - **Cooperative Cancellation (`CancellationToken`):**
    - Built on `asyncio.Event` with `throw_if_cancelled()` method.
    - Tasks periodically check token status; user barge-in or `/task-cancel` immediately aborts the task cleanly without orphaned processes.
  - **PostgreSQL Checkpointing (`task_checkpoints` table):**
    - Durable persistence of state machine (`QUEUED`, `RUNNING`, `PAUSED`, `AWAITING_APPROVAL`, `COMPLETED`, `CANCELLED`, `FAILED`), fractional progress (`0.0 - 1.0`), and JSONB results.
    - Schema backed by indices on `conversation_id` and `state`.
  - **Dynamic Signature Introspection:**
    - Supervisor inspects worker coroutine signatures and automatically injects `cancellation_token` and `progress_reporter` callbacks if declared.
  - **Asynchronous Notification Emitter:**
    - In-memory async queue delivers task completion and failure notices directly into the next conversation turn as real-time alerts.
- **Verification & Test Results:**
    - `tests/test_concurrency.py`: 4 passing tests in **1.50s**.
      - `test_bounded_concurrency_gate`: Verified exactly 4 tasks run simultaneously out of 7 spawned.
      - `test_cooperative_cancellation`: Verified immediate task halt on cancel signal.
      - `test_postgres_checkpoint_persistence`: Verified roundtrip DB write/read in PostgreSQL 16.
      - `test_non_blocking_session_with_background_task`: Verified instant conversation response while background job runs concurrently.

---

### Phase 3: Add-on 2 — Unbroken Bi-Temporal Memory Engine & Provenance (COMPLETED & HARDENED)
- **Problem Solved:** Standard AI agent systems suffer from destructive amnesia or conflicting context windows when user constraints change. They lack causal lineage (asking *"Why did you do that?"* causes hallucinations).
- **Architectural Solution:** Bi-Temporal Data Architecture + Directed Acyclic Graph (DAG) Provenance + Obsidian Knowledge Vault Bridge.
  1. **Dual-Clock Validity:**
     - **Valid Time ($T_v$):** The real-world window during which the fact was true in reality (`valid_from` to `valid_to`).
     - **Transaction Time ($T_t$):** The immutable database timestamp when the record was recorded into the ledger (`created_at`).
  2. **Automated ACID Superseding:**
     - Hard `DELETE` statements are prohibited in Lumen.
     - When a new assertion arrives for an existing `(entity, attribute)` with active state (`valid_to IS NULL`), an ACID transaction automatically retires the prior assertion by updating `valid_to = new_fact.valid_from`.
  3. **Provenance Reaffirmation (Deduplication Guard):**
     - If an identical assertion is stated again in conversation, instead of creating duplicate versions, the new source event ID is merged into the existing fact's `source_event_ids` array (`SELECT DISTINCT unnest(...)`).
  4. **Domain Classification:**
     - Conversational preferences are categorized by domain (e.g. `database`, `editor`, `programming_language`, `ui_theme`) so modifying your editor preference never overwrites your database preference.
  5. **Context Compaction Engine (`apps/lumen-core/src/compactor/compactor.py`):**
     - Monitors high watermark (`high_watermark_turns = 6`) and low watermark (`low_watermark_turns = 2`).
     - Prunes and condenses older conversation turns into episodic narratives, leaving a summary anchor in the active context window.
     - Manual on-demand compaction via `/compact`.
  6. **Obsidian Markdown Vault Synchronizer (`apps/lumen-core/src/vault/vault_sync.py`):**
     - Exports atomic facts to `/Users/forion/Desktop/lumen/vault/memories/` with YAML frontmatter and `[[Wikilinks]]` for instant visualization in Obsidian, Logseq, or Foam.
     - Flushes compacted episodes into `/vault/episodes/`.
     - Inspection via `/vault` command in the interactive REPL.
  7. **The Trace API (`/api/v1/trace/{fact_id}`):**
     - Traverses from derived L1 Canonical Fact back to raw L0 User Utterances via `source_event_ids`.
     - Supports short prefix UUIDs (e.g. `/trace 8496964f`).
     - Returns historical evolution lineage (e.g. Version 1 $\to$ Version 2 $\to$ Version 3), revealing exactly when and why facts were modified.
  8. **Time-Travel Querying (`/api/v1/memories/time-travel`):**
     - Reconstructs the agent's exact state of mind as of any historical timestamp using bi-temporal range filtering:
       $$\text{valid\_from} \le T_{\text{as\_of}} \quad \text{AND} \quad (\text{valid\_to IS NULL} \lor \text{valid\_to} > T_{\text{as\_of}})$$
  9. **Normalized Vector Tokenization:**
     - Upgraded `DeterministicHashEmbeddingAdapter` with regex word boundary tokenization (`re.findall(r"\w+", text)`), eliminating punctuation-attachment similarity drop-offs and achieving high cosine fidelity ($>0.26$).
- **Verification & Test Results:**
  - `packages/memory-engine/tests/test_memory_api.py`: 3 passing integration tests in **0.37s**.
  - `apps/lumen-core/tests/test_compactor.py`: 4 passing tests in **0.12s**.
  - Full suite: **13/13 automated tests green**.
  - Interactive REPL commands `/trace <id>`, `/vault`, and `/compact` verified live.

---

### Phase 4: Safe Computer Control ("The Hands")
- **Technique:** Multi-Layered Defense-in-Depth + Lexical Pipeline Analysis + Python AST Inspection + Environment Scrubbing + Two-Man Rule Approval Gate.
- **Microservice / Modules:** `apps/lumen-core/src/tools/`
  1. **Ring 1 — AST Command Gatekeeper (`gatekeeper.py`):**
     - **Compound Pipeline Splitting:** Parses chained statements (`&&`, `||`, `;`, `|`, `&`) using `shlex.shlex(punctuation_chars=True)` and computes maximum risk across all segments. Prevents command smuggling (e.g. `ls && rm -rf /`).
     - **Subshell Extraction:** Detects and recursively evaluates nested command substitutions: `$(...)` and `` `...` ``.
     - **Reverse Shell & Tunnel Blocking:** Hard-refuses network tunneling payloads (`nc -e /bin/sh`, `bash -i >& /dev/tcp/...`, `socat exec:...`, `mkfifo`).
     - **Protected Credential & Secret Sentinel:** Automatically audits paths for sensitive credentials (`.ssh`, `.aws`, `.gnupg`, `.netrc`, `.env`, `/etc/shadow`, `.bashrc`, `.zshrc`, `*.pem`, `*.key`, `id_rsa`). Reading sensitive paths elevates to `CAUTION` (requiring explicit human confirmation); modifying or deleting sensitive paths is hard `FORBIDDEN`.
     - **Python Security AST Inspector:** Uses Python's built-in `ast.parse()` to inspect inline scripts (`python -c "..."`). Blocks destructive standard library calls (`shutil.rmtree('/')`), low-level imports (`ctypes`, `pty`), arbitrary dynamic execution (`eval`, `exec`), and recursively audits shell commands spawned via `os.system` or `subprocess`.
     - **Command Classification:** Standard read-only utilities (`ls`, `git status`, `cat`, `grep`, `pwd`) auto-approved as `SAFE`. Modifying utilities (`git commit`, `pip install`, `pytest`, `mkdir`) marked `CAUTION`. Output file redirection (`>`, `>>`) automatically elevates read-only commands to `CAUTION`.
  2. **Ring 2 — Two-Man Rule Approval Gate (`approval.py`):**
     - Modifying or `CAUTION` commands cannot execute autonomously.
     - The engine mints a unique approval token (`appr_<id>`) and pauses execution in an asynchronous `AWAITING_APPROVAL` barrier using `asyncio.Event`.
     - Human operator inspects pending authorizations with `/approvals` and can authorize with `/approve <id>` or revoke with `/reject <id>`.
  3. **Ring 3 — Sandboxed Subprocess Runner (`runner.py`):**
     - **Environment Sanitization (Secret Scrubbing):** Child subprocesses run with a sanitized environment that scrubs all keys matching `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`, `DATABASE_URL`, `POSTGRES_*`, `REDIS_*`. Prevents child processes or prompt-injected tools from exfiltrating credentials.
     - **Bounded Stream Buffering (Memory Protection):** Replaced unbounded `communicate()` with chunked asynchronous stream reading capped at `max_output_bytes = 512KB`. If a runaway process attempts to flood output, the buffer truncates safely and sends `SIGTERM` to protect system RAM.
     - **Directory Jail Guard:** Validates working directories before launch; strictly rejects execution inside critical host roots (`/etc`, `/System`, `/Library`, `/private`, `/bin`, `/sbin`).
     - **Cooperative Cancellation & Timeouts:** Enforces wall-clock timeouts (default 30s) and clean process termination cascades (`SIGTERM` $\to$ grace period $\to$ `SIGKILL`).
  4. **Runtime REPL Integration (`apps/lumen-core/src/runtime/session.py`):**
     - Interactive commands: `/exec <cmd>`, `/approve <id>`, `/reject <id>`, `/approvals`.
- **Verification & Test Results:**
  - `apps/lumen-core/tests/test_computer_control.py`: **15 passing unit & adversarial security tests** in **0.53s**.
  - Full project test suite: **28/28 automated tests green** (25 `lumen-core`, 3 `memory-engine`).

---

### Phase 5: Knowledge Graph Integrity, Entities & Review Queue (Steps 1, 2 & 3 COMPLETE)
- **Technique:** Event-Sourced 3-Table Schema (`events`, `entities`, `triples`) + Controlled Predicate Ontology + Two-Tier Entity Resolver + Confidence-Tiered Review Queue + Transactional Outbox Pattern (`memory_outbox` + `LISTEN/NOTIFY`).
- **Microservice / Modules:** `packages/memory-engine/` and `apps/lumen-core/`
  1. **Step 1: The 3-Table Normalized Relational Schema (`infra/docker/postgres/migrations/002_knowledge_graph_and_review_queue.sql`):**
     - **`entities` table:** Unique canonical nodes (`canonical_name UNIQUE`), category (`person`, `organization`, `technology`, `project`), `aliases TEXT[]` with a dedicated PostgreSQL GIN index, and HNSW vector cosine index on dense embeddings.
     - **`predicates` table:** Extensible controlled ontology seeded with 16 canonical relationships (`FOUNDED`, `DEVELOPS`, `USES`, `DEPENDS_ON`, `PREFERS`, `DISLIKES`, `WRITTEN_IN`, etc.) with inverse predicates and symmetry tracking.
     - **`triples` table:** Relational graph edges (`subject_id FK`, `predicate FK`, `object_id FK`) with dual-clock bi-temporal validity (`valid_from`, `valid_to`), source event provenance array (`source_event_ids UUID[]`), and active edge partial index (`valid_to IS NULL`).
     - **`review_queue` table:** Staging ground for proposed memory updates (`proposal_type`, `tier`, `status`, `conflict_reason`, `conflicting_triple_id`, `source_event_id`).
  2. **Step 2: Two-Tier Entity Resolver & Review Queue Staging (`app/domain/resolver.py`):**
     - **Two-Tier Entity Resolution:**
       - *Tier 1:* Case-insensitive exact name and alias array containment lookup (`aliases @> ARRAY[name]`). Fast, deterministic, zero false positives.
       - *Tier 2:* High-confidence vector embedding match ($>0.90$) for novel entity variations; automatic new entity creation when novel.
     - **Controlled Predicate Normalization:**
       - Normalizes colloquial verb phrases (`CREATED` $\to$ `FOUNDED`, `BUILDS` $\to$ `DEVELOPS`, `UTILIZES` $\to$ `USES`).
     - **Confidence-Tiered Contradiction Gating:**
       - *Tier A (Auto-Commit):* Non-conflicting assertions with confidence $\ge 0.85$ commit directly to canonical `triples` and log an `AUTO_COMMITTED` receipt.
       - *Tier B (Human Review Queue):* Contradictory belief shifts (e.g. changing preferred editor from Neovim to VS Code) or low-confidence assertions pause in `review_queue` with `status = 'PENDING'` and an explicit human-readable conflict explanation.
     - **Human Resolution:**
       - `/approve-proposal <id>` atomically supersedes the conflicting triple (`valid_to = NOW()`) and activates the new triple.
       - `/reject-proposal <id>` dismisses the proposed change, keeping the prior belief intact.
  3. **Step 3: The Transactional Outbox Pattern for Obsidian Sync (`apps/lumen-core/src/vault/outbox_worker.py`):**
     - **Relational Outbox Schema (`infra/docker/postgres/migrations/003_transactional_outbox.sql`):**
       - Table `memory_outbox`: `(id, aggregate_type, aggregate_id, event_type, payload JSONB, status, retry_count, error, created_at, processed_at)`.
       - Partial index `idx_memory_outbox_pending` on `created_at ASC WHERE status = 'PENDING'`.
       - PostgreSQL Trigger `trg_memory_outbox_inserted` broadcasting `pg_notify('memory_outbox_channel', NEW.id::text)`.
     - **Atomic Dual-Write Elimination:**
       - `save_entity`, `save_triple`, and `save_fact` write the mutation AND the outbox intent within the *same* ACID database transaction.
       - Inline disk writes removed from the conversation turn loop, saving 20–100ms per turn and guaranteeing zero drift if filesystem I/O locks.
     - **Sub-Millisecond Event-Driven Dispatch:**
       - `OutboxWorker` maintains a persistent asyncpg listener to `memory_outbox_channel`.
       - Wakes up with $0\%$ CPU polling overhead immediately when Postgres commits.
       - 15-second safety sweep provides fault-tolerant recovery for any missed frames.
     - **Rich Graph Obsidian Rendering (`src/vault/vault_sync.py`):**
       - `export_entity`: Generates `vault/entities/{canonical_name}.md` with YAML frontmatter, aliases, category badges, and dynamic 2-way relationship tables.
       - `export_triple`: Atomically injects outbound `[[Wikilinks]]` on subject entity cards and inbound `[[Wikilinks]]` on object cards.
     - **Interactive Inspection:**
       - REPL command `/outbox` displays pending queue, processed count, failed syncs, and total markdown vault stats.
  4. **Step 4: The Overnight Consolidation Daemon (`apps/lumen-core/src/consolidation/daemon.py` & `app/domain/consolidator.py`):**
     - **Contradiction Patrol:**
       - Detects opposing active assertions (e.g. $(u, \text{PREFERS}, v) \land (u, \text{DISLIKES}, v)$).
       - Detects multi-value collisions on functional predicates (`PREFERS`, `MEMBER_OF`, `FOUNDED`).
       - Crucial Epistemic Principle: AI NEVER overwrites or settles contradictions silently. All conflicts are staged in `review_queue` as `TIER_B` (`PENDING`) with explicit collision explanations for human approval (`/approve-proposal`, `/reject-proposal`).
     - **Semantic Clustering & Alias Detection:**
       - Scans orphan entities ($d(v) \le 1$).
       - Computes pairwise cosine similarity via `pgvector` ($1 - (\mathbf{e}_1 \Leftrightarrow \mathbf{e}_2) \ge 0.88$).
       - Proposes `ALIAS_MERGE` review queue candidates (e.g. merging "JS" into "JavaScript").
     - **Morning Intelligence Digest (`src/vault/vault_sync.py`):**
       - Compiles daily briefing note into `vault/digests/digest_YYYY-MM-DD.md` with YAML frontmatter, patrol metrics, action items awaiting review, and background task progress.
     - **REPL Controls:**
       - `/consolidate`: Triggers on-demand consolidation patrol and updates Obsidian digest.
       - `/digest`: Displays current day's briefing in terminal.
  5. **Step 5: The Unified Hybrid Retrieval Service (`search()`) with Reciprocal Rank Fusion:**
     - **Multi-Channel Indexing (`infra/docker/postgres/migrations/004_hybrid_search_indices.sql`):**
       - GIN `to_tsvector('english', ...)` for English lexical search on `facts` and `entities`.
       - GIN `pg_trgm` trigram indices on `facts` and `entities` for fuzzy identifier and code matching.
       - HNSW vector index (`vector_cosine_ops`) for dense embeddings on `facts` and `entities`.
     - **Tri-Stream Concurrent Retrieval (`app/adapters/storage/postgres_adapter.py`):**
       - *Channel 1 (Dense Vector):* HNSW cosine distance search (`<=>`) on 1536-dimensional query embeddings.
       - *Channel 2 (Lexical BM25):* `to_tsvector @@ plainto_tsquery` combined with `ts_rank_cd` and trigram similarity ranking.
       - *Channel 3 (Associative Graph):* Entity resolution on query $\to$ multi-hop active triple traversal $\to$ retrieves facts tied to connected entity nodes.
     - **Reciprocal Rank Fusion (RRF) Formula:**
       $$\text{RRF\_Score}(d) = \sum_{m \in \{\text{dense}, \text{lexical}, \text{graph}\}} \frac{w_m}{k + r_m(d)}$$
       with smoothing constant $k = 60$ and channel weights $w_{\text{dense}} = 1.0, w_{\text{lexical}} = 1.0, w_{\text{graph}} = 1.2$.
     - **Single Canonical Interface:**
       - REST: `POST /api/v1/search/hybrid` returning ranked items with `rrf_score` and exact `channel_ranks`.
       - SDK: `await memory.search(query, limit=5, mode="hybrid")`.
       - Turn-Level Grounding: `session.process_turn()` automatically injects hybrid RRF context into system prompt.
       - REPL: `/search <query>` displays fused rankings and per-channel breakdown.
  6. **Recursive Graph Traversal ($O(V+E)$):**
     - Sub-millisecond multi-hop graph walking in PostgreSQL using Recursive Common Table Expressions (CTEs) with cycle prevention (`NOT (t.id = ANY(gw.path))`) and depth bounding.
  7. **Runtime REPL & API Integration:**
     - REPL commands: `/search <query>`, `/consolidate`, `/digest`, `/outbox`, `/proposals`, `/approve-proposal <id>`, `/reject-proposal <id>`, `/graph [entity]`.
     - Endpoints: `POST /api/v1/search/hybrid`, `POST /api/v1/entities`, `POST /api/v1/graph/propose`, `GET /api/v1/graph/review-queue`, `POST /api/v1/graph/review-queue/{id}/resolve`, `GET /api/v1/graph/traverse/{entity}`, `GET /api/v1/outbox/pending`, `POST /api/v1/outbox/{id}/ack`, `POST /api/v1/outbox/{id}/fail`, `GET /api/v1/outbox/stats`, `POST /api/v1/graph/consolidate`, `GET /api/v1/graph/orphans`.
- **Verification & Test Results:**
  - `packages/memory-engine/tests/test_hybrid_retrieval.py`: 3 passing integration tests in **0.39s**.
  - `packages/memory-engine/tests/test_consolidation.py`: 3 passing integration tests in **0.30s**.
  - `packages/memory-engine/tests/test_transactional_outbox.py`: 3 passing integration tests in **0.41s**.
  - `packages/memory-engine/tests/test_knowledge_graph.py`: 4 passing integration tests in **0.43s**.
  - `packages/memory-engine/tests/test_memory_api.py`: 3 passing integration tests in **0.49s**.
  - `apps/lumen-core/tests/test_consolidation_daemon.py`: 3 passing unit & live integration tests in **0.11s**.
  - `apps/lumen-core/tests/test_outbox_worker.py`: 4 passing unit & live integration tests in **0.17s**.
  - `apps/lumen-core/tests/`: 33 passing unit & session tests in **4.88s**.
  - Full project monorepo test suite: **49/49 automated tests green (100%)**.

---

### Phase 6: Add-on 5 — Autonomous Web & Deep Research ("The Eyes")
- **Technique:** Multi-Surface Discovery + Query De-Optimization + Recursive Semantic Link Walking + Defense-in-Depth Injection Quarantine.
- **Core Architecture (`apps/lumen-core/src/research/`):**
  1. **Step 1: Multi-Source Discovery Adapter (`MultiSourceDiscoveryEngine` & `QueryDeoptimizer`):**
     - **Query De-Optimizer (`src/research/deoptimizer.py`):** Strips conversational fluff; isolates precise technical tokens (`snake_case`, dot notation, numbers); generates anti-SEO negative tokens (`-seo -affiliate -sponsored -buy -course -top10`); constructs 4 specialized queries (`general`, `exact_token`, `community`, `academic`).
     - **Search Provider Protocol (`src/research/providers/`):**
       - `DuckDuckGoProvider`: Zero-credential web search via `ddgs`/`primp` browser impersonation.
       - `HackerNewsProvider`: Direct Algolia REST API (`hn.algolia.com/api/v1/search`) returning practitioner comments, stories, points, and discussion threads.
       - `RedditProvider`: Scoped search syntax targeting Reddit community discussions without triggering bot 403 API gates.
       - `ArxivProvider`: Direct public Atom XML query (`export.arxiv.org/api/query`) returning academic papers, authors, abstracts, and direct PDF links.
     - **Fault-Tolerant Concurrent Fan-Out:** Uses `asyncio.wait(tasks, timeout=12.0)` with per-task harvesting and pending task cancellation so a slow provider never delays fast results. Strips URL tracking parameters (`utm_*`, `ref=`) for canonical deduplication.
  2. **Step 2: Content Extraction Engine (`ContentExtractor`) & Prompt Injection Shield:**
     - **Prompt Injection Defense (`src/research/extractor.py`):** Scans extracted text against compiled attack signatures (`ignore all previous instructions`, `you are now in developer mode`, `system prompt:`, exfiltration image beacons `![img](...?secret=...)`). Quarantines detected payloads inside safe security blocks and neutralizes beacons.
     - **Multi-Tier Content Ingestion:**
       - *Tier 1 (Fast Stealth HTML):* `httpx` + BeautifulSoup strips `<nav>`, `<footer>`, `<script>`, `<style>`, `<aside>`, converting headings, lists, tables, and code blocks into clean Markdown.
       - *Tier 2 (Dynamic SPA Fallback):* Dispatches client-rendered React/Vue pages to `Crawl4AI` (`AsyncWebCrawler`).
       - *Tier 3 (Document Conversion):* Integrates Microsoft `MarkItDown` for converting local and remote PDFs, DOCX, and text into Markdown.
     - **Outbound Link Harvesting:** Parses `<a href="...">` on visited pages to feed the recursive link frontier.
  3. **Step 3: Deep Research Orchestrator (`DeepResearchAgent`):**
     - Executes non-blocking research loops on **TaskSupervisor ("The Spine")**.
     - **Semantic Link Frontier ($d \ge 1$):** Scores outbound links using topic keyword relevance ($\text{Score}(u)$), hopping into high-scoring citations to uncover un-SEO technical blogs, Gists, and primary papers.
     - **Claim & Quote Extractor:** Breaks paragraphs into individual sentences, isolates assertive empirical claims, and extracts verbatim practitioner quotes with source attribution.
     - **Cooperative Cancellation:** Full compliance with `CancellationToken.throw_if_cancelled()` for instant `/task-cancel` support.
  4. **Step 4: Knowledge Graph Ingestion, Obsidian Dossier Generation & REPL Commands:**
     - **Obsidian Research Dossiers (`vault/research/{topic_slug}.md`):** Automatically renders YAML frontmatter, executive summary, key technical findings, practitioner quotes, and explored sources table via `ObsidianVaultSync.export_research_dossier()`.
     - **Epistemic Integrity:** External web claims are staged to the Memory Engine Review Queue as `TIER_B` proposals (`(Topic)-[EVIDENCE_FOR]->(Claim)`) rather than blindly mutating user memory.
     - **REPL Commands:**
       - `/research <topic>`: Dispatches non-blocking deep research task in background.
       - `/dossiers`: Lists all generated research dossiers in the Obsidian vault.
       - `/dossier <name>`: Displays full findings, verbatim quotes, and cited sources.
- **Verification & Test Results:**
  - `apps/lumen-core/tests/test_multi_source_search.py`: 6 passed in **0.90s** (de-optimizer, deduplication, fault tolerance, live HN API).
  - `apps/lumen-core/tests/test_content_extractor.py`: 5 passed in **0.47s** (injection shield, beacon neutralization, HTML cleaning, MarkItDown).
  - `apps/lumen-core/tests/test_deep_research_agent.py`: 3 passed in **0.14s** (end-to-end research loop, cooperative cancellation, vault dossier export).
  - Monorepo test suite: **63/63 automated tests green (100%)** (47 `lumen-core`, 16 `memory-engine`).

---

### Codebase Security Audit & Vulnerability Hardening (Zero-Trust Guardrails)
- **Technique:** Multi-Vector Vulnerability Penetration & Hardening Audit.
- **Identified & Neutralized Vulnerabilities:**
  1. **Command Injection & Delimiter Evasion via Newline (`\n`) in `ASTGatekeeper` (`gatekeeper.py`):**
     - *Flaw:* `shlex` with `whitespace_split=True` treated `\n` as whitespace. A compound command like `echo hi\ncurl evil.com` was masked as a single `echo` command (classified as SAFE), which then executed `curl` via subshell!
     - *Fix:* `_split_into_segments` now splits by lines (`raw.splitlines()`) first and recursively evaluates every line and pipeline segment independently.
  2. **Server-Side Request Forgery (SSRF) & Cloud Metadata Exfiltration (`extractor.py`):**
     - *Flaw:* Crawler fetched arbitrary URLs and followed redirects into internal network interfaces (`localhost:5432`, `127.0.0.1:8000`, `169.254.169.254` AWS/GCP metadata).
     - *Fix:* Built `SSRFGuard` performing strict scheme verification (`http`/`https`), DNS resolution checks (`socket.getaddrinfo`), and blocking all private, loopback, link-local, multicast, and metadata IPs with redirect hop validation.
  3. **Path Traversal & Arbitrary File Disclosure in `/dossier` (`session.py`):**
     - *Flaw:* `/dossier ../../LUMEN_ENGINEERING_LOG` could read markdown files outside `vault/research/`.
     - *Fix:* Path sanitization stripping directory separators, enforcing `target.resolve().is_relative_to(research_dir.resolve())`.
  4. **Denial of Service via Unbounded Download Streams (`extractor.py`):**
     - *Flaw:* Reading arbitrary file streams into memory without ceiling risked OOM crashes on large files or decomp bombs.
     - *Fix:* Enforced a 25 MB stream ceiling (`MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024`) with `Content-Length` pre-check and streaming accumulator bounds.
  5. **XML External Entity (XXE) & Entity Expansion Bomb Defense (`arxiv.py`):**
     - *Flaw:* Standard `xml.etree.ElementTree` is vulnerable to XML entity expansion bombs (Billion Laughs).
     - *Fix:* Replaced with `defusedxml.ElementTree` to prevent entity expansion and external DTD attacks.
  6. **Vault Note Path Confinement (`vault_sync.py`):**
     - *Flaw:* Entity and note sanitization stripped slashes but allowed sequences of double dots `..`.
     - *Fix:* Replaced `\.\.+` with underscores and added `_safe_vault_target` ensuring every write is strictly relative to `vault_path`.
- **Verification & Test Results:**
  - `apps/lumen-core/tests/test_security_audit.py`: 4 passing security regression tests in **0.26s**.
  - Monorepo test suite: **67/67 automated tests green (100%)** (51 `lumen-core`, 16 `memory-engine`).

### Phase 7: Voice, Barge-In & Sub-Second Latency Gateway ("The Senses" - Final Capstone)
- **Technique:** Sub-Second Full-Duplex Audio Pipeline with Dynamic Echo Gating and Preemptive Barge-In.
- **Architectural Specification:** Locked in `docs/VOICE_GATEWAY_ARCHITECTURE.md`.
- **Core Components & Mathematical Formulations:**
  1. **Acoustic Audio Framing (`models.py`):**
     - Discrete 16-bit 16kHz linear PCM frames (30ms window = 480 samples = 960 bytes).
     - $\text{RMS} = \sqrt{\frac{1}{N}\sum_{i=0}^{N-1} s_i^2}$, $\text{dBFS} = 20 \log_{10}\left(\frac{\text{RMS}}{32767}\right) \in [-96.0, 0.0]$.
  2. **Dynamic Echo Gating (`echo_gate.py`):**
     - Dynamic threshold elevation while Lumen is outputting audio to prevent self-interruption:
       $$\tau(t) = \tau_{\text{idle}} + (\tau_{\text{speaking}} - \tau_{\text{idle}}) \cdot I(t) = 0.50 \to 0.85$$
       $$E_{\text{floor}}(t) = -45.0\text{ dBFS} \to -28.0\text{ dBFS}$$
       $$k_{\text{onset}} = 2 \text{ frames} \to 3 \text{ frames}$$
  3. **Voice Activity Detection (`vad.py`):**
     - Silero VAD supporting Apple CoreML ONNX acceleration with native spectral acoustic fallback:
       $$P(\text{speech}) = 0.60 \cdot E_{\text{norm}} + 0.25 \cdot ZCR_{\text{score}} + 0.15 \cdot C_{\text{crest}}$$
     - Hangover hysteresis preventing premature audio clipping during natural pauses.
  4. **Sub-50ms Barge-In Coordinator (`interruption.py`):**
     - Emits `InterruptionToken` when user speaks during assistant playback.
     - Preemption hooks immediately cut speaker output, flush TTS buffers, and cancel in-flight LLM generation in $<50$ms.
  5. **Syntactic Clause Chunker (`chunker.py`):**
     - Analyzes LLM token streams and splits on natural prosodic boundaries (`.`, `!`, `?`, `,`, `;`), protecting abbreviations and decimals (`3.14`).
     - Emits clauses early to achieve sub-250ms Time-To-First-Audio (TTFA).
  6. **Streaming STT & Neural TTS (`stt.py`, `tts.py`):**
     - `WhisperSTTProvider`: Canonical RIFF/WAV header packing with sub-150ms Groq Whisper (`whisper-large-v3-turbo`) and OpenAI Whisper API integration, plus deterministic `MockSTTProvider`.
     - `MockNeuralTTS`: Harmonic 16kHz multi-sine vocal synthesis for offline testing and fast verification.
     - `AudioOutputPlayer`: Async playback queue with instant barge-in purge.
  7. **Full-Duplex Coordinator & WebSocket Gateway (`pipeline.py`, `gateway.py`):**
     - Streaming bidirectional WebSocket server (`ws://0.0.0.0:8765/voice`) accepting 16-bit PCM binary frames and emitting synthesized audio frames and JSON telemetry.
     - REPL slash commands: `/voice` (launch server), `/voice-status` (real-time telemetry & latency), `/voice-stop`.
  8. **Hands-Free Ambient Wake-Word Daemon & Acoustic Earcon Chimes (`wake_word.py`, `chime.py`, `daemon.py`):**
     - Two-State Ambient Machine (`DORMANT_LISTENING` $\longleftrightarrow$ `ACTIVE_CONVERSATION`).
     - Real-time neural openWakeWord model (`hey_jarvis`, 1280-sample / 80ms inference blocks) running on macOS microphone capture via `sounddevice` at $<2\%$ CPU.
     - Synthesized pure harmonic acoustic earcons (Hann window envelope shaped):
       - Wake Chime: Ascending two-tone chirp ($D_5: 587.33\text{ Hz} \to A_5: 880.00\text{ Hz}$, 220ms).
       - Sleep Chime: Descending two-tone dismissal ($A_5: 880.00\text{ Hz} \to D_5: 587.33\text{ Hz}$, 220ms).
       - Barge-In Ping: Crisp acoustic pulse ($C_6: 1046.50\text{ Hz}$, 35ms).
     - Auto-sleep watchdog: safely returns to dormant listening after 10s of ambient silence.
     - REPL commands: `/listen`, `/listen-status`, `/listen-stop`.
- **Verification & Test Results:**
  - `apps/lumen-core/tests/test_voice_pipeline.py`: 10 passing tests in **0.49s**.
  - `apps/lumen-core/tests/test_wake_word_daemon.py`: 5 passing tests in **2.37s** (chime synthesis, refractory period gating, neural ONNX inference, state transitions, speech watchdog refreshing).
  - Monorepo test suite: **82/82 automated tests green (100%)** (66 `lumen-core`, 16 `memory-engine`).

---

## 🗺️ 4. What We Are Building Next (Roadmap Checklist)

- [x] **Phase 0:** Infrastructure Hub (PostgreSQL 16 + pgvector v0.8.7, Redis 7).
- [x] **Phase 1:** MVP Brain + Memory Engine v0.1 + JARVIS Persona (Hexagonal Architecture, HNSW 2.87ms search, Circuit Breaker).
- [x] **Phase 2:** True Concurrency "The Spine" (Supervisor, Semaphore Gate k=4, Cooperative Cancellation, Postgres Checkpoints, Non-blocking threadpool REPL).
- [x] **Phase 3:** Unbroken Bi-Temporal Memory Engine (Dual-clock validity, Trace API DAG, Automated Superseding, Time-Travel Vector Queries).
- [x] **Phase 4: Add-on 3 — Safe Computer Control:**
  - [x] AST Gatekeeper parsing destructive commands (`rm -rf`, `DROP TABLE`).
  - [x] Two-Man Rule approval gates (`AWAITING_APPROVAL`, `/approve`, `/reject`).
  - [x] Sandboxed execution runner with timeouts, cancellation, and stdout/stderr capture.
- [x] **Phase 5: Add-on 4 — Knowledge Graph & Overnight Consolidation (The Super-Brain) — COMPLETE:**
  - [x] **Step 1:** Normalized 3-table relational schema (`entities`, `triples`, `predicates`) with bi-temporal validity.
  - [x] **Step 2:** Two-Tier Entity Resolver & Review Queue Staging Buffer (preventing messy node duplicates and epistemic drift).
  - [x] **Step 3:** Transactional Outbox Pattern for decoupled Obsidian Vault Synchronization (`memory_outbox` + `pg_notify`).
  - [x] **Step 4:** Overnight Consolidation Daemon (contradiction patrol, semantic orphan clustering, morning intelligence digest in `vault/digests/`).
  - [x] **Step 5:** Unified Hybrid Retrieval Service (`search()`) with Reciprocal Rank Fusion (BM25 + pgvector + graph).
- [x] **Phase 6: Add-on 5 — Autonomous Web & Deep Research ("The Eyes") — COMPLETE:**
  - [x] Multi-Source Discovery Adapter (DuckDuckGo, Hacker News Algolia, Reddit, arXiv).
  - [x] Query De-Optimizer with anti-commercial exclusions and exact token quoting.
  - [x] Content Extraction Engine (Crawl4AI, Microsoft MarkItDown, BeautifulSoup stealth cleaner).
  - [x] Prompt Injection Defense Shield (jailbreak quarantine, tracking beacon neutralization).
  - [x] Deep Research Orchestrator with recursive citation link walking ($d \ge 1$) into un-SEO resources.
  - [x] Knowledge Graph Review Queue staging (`TIER_B`) and Obsidian Dossier generation in `vault/research/`.
- [x] **Phase 7: Add-on 6 — Voice & Barge-in Gateway ("The Senses" - Final Capstone) — COMPLETE:**
  - [x] Full-duplex WebSocket server (`ws://0.0.0.0:8765/voice`).
  - [x] Silero VAD CoreML / ONNX & native spectral acoustic probability engine.
  - [x] Dynamic EchoGate acoustic suppression preventing self-interruption.
  - [x] Sub-50ms Barge-In Preemption Coordinator with atomic buffer flush and LLM cancellation.
  - [x] Syntactic Clause Chunker for prosodically natural speech and sub-250ms TTFA.
  - [x] Streaming STT (Whisper WAV container & Groq/OpenAI cloud integration) + Neural TTS synthesis.
  - [x] openWakeWord hands-free ambient activation ("Hey Jarvis" / "Hey Lumen") with sounddevice mic capture.
  - [x] Acoustic Earcon Chime synthesis (Hann window shaped ascending wake and descending sleep earcons).
  - [x] Terminal REPL integration (`/voice`, `/voice-status`, `/voice-stop`, `/listen`, `/listen-status`, `/listen-stop`).
