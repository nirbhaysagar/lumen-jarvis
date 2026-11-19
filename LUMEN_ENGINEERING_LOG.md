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
| **Phase 2** | True Concurrency ("The Spine") | **COMPLETE** | Native Async Supervisor, Semaphore Gate (k=4), Postgres Checkpointing | Pytest suite (1.50s, 4 tests passed), Non-blocking session integration |
| **Phase 3 (Current)** | Unbroken Bi-Temporal Memory | **NEXT** | Dual-clock validation, AST provenance extraction, Trace API | Integration tests |
| **Phase 4** | Safe Computer Control | QUEUED | Sandboxed execution, AST-gatekeeper, Two-man rule approvals | Sandbox isolation tests |
| **Phase 5** | Voice & Barge-in Gateway | QUEUED | Full-duplex WebSocket, VAD interrupt tokens, Silero/Kokoro | Audio roundtrip latency tests |

---

## 🏛️ 2. Architectural Decision Records (ADR Log)

| ADR ID | Decision Title | Chosen Pattern | Alternatives Considered | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **ADR-001** | Vector & State Storage | PostgreSQL (`pgvector` + JSONB) | Pinecone, ChromaDB, MongoDB | Single ACID boundary, zero distributed sync lag, HNSW cosine index built-in. |
| **ADR-002** | Service Decoupling | Hexagonal Architecture (Ports & Adapters) | Layered / Monolithic spaghetti | Core business domain (bi-temporal facts, trace graph) remains 100% independent of storage drivers. |
| **ADR-003** | Distributed Tracing | Correlation ID Header (`x-trace-id`) + Idempotency | OpenTelemetry agent alone | Sub-millisecond tracking across microservice HTTP boundaries without heavy agent overhead. |
| **ADR-004** | Agent Concurrency Model | Native Async Supervisor + Postgres Checkpoints | Hatchet, DBOS, Celery | Sub-0.1ms dispatch latency; avoids heavy Go daemons/gRPC setup while ensuring durable task recovery. |
| **ADR-005** | Resilience & Fault Tolerance | Circuit Breaker Pattern | Naive HTTP retries | Prevents agent thread exhaustion when Memory Engine is restarting or under load. |

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

## 🗺️ 4. What We Are Building Next (Roadmap Checklist)

- [x] **Phase 0:** Infrastructure Hub (PostgreSQL 16 + pgvector v0.8.7, Redis 7).
- [x] **Phase 1:** MVP Brain + Memory Engine v0.1 + JARVIS Persona (Hexagonal Architecture, HNSW 2.87ms search, Circuit Breaker).
- [x] **Phase 2:** True Concurrency "The Spine" (Supervisor, Semaphore Gate k=4, Cooperative Cancellation, Postgres Checkpoints).
- [ ] **Phase 3: Add-on 2 — Unbroken Bi-Temporal Memory Engine:**
  - [ ] Implement dual-clock time tracking (`valid_time` vs `transaction_time`) for immutable state reconstruction.
  - [ ] Trace API & AST Provenance Graph linking every derived fact to its exact source event ID.
  - [ ] Automated superseding: when a new preference contradicts an old one, update `valid_to = NOW()` without mutating history.
- [ ] **Phase 4: Add-on 3 — Safe Computer Control:**
  - [ ] Sandboxed execution runner for shell & code execution.
  - [ ] AST Gatekeeper parsing destructive commands (`rm -rf`, `DROP TABLE`).
  - [ ] Two-Man Rule approval gates (`AWAITING_APPROVAL`).
- [ ] **Phase 5: Add-on 4 — Voice & Barge-in Gateway:**
  - [ ] Full-duplex WebSocket server.
  - [ ] Silero VAD (Voice Activity Detection) with barge-in interrupt tokens.
  - [ ] Kokoro TTS streaming engine.
