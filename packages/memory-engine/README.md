# Lumen Memory Engine (`packages/memory-engine`)

> **Thesis:** Memory → Context → Intelligence.  
> LLMs generate intelligence; Lumen supplies permanent continuity.  
> *"Dump once → never re-explain."*

The Lumen Memory Engine is a standalone, hexagonal microservice providing bi-temporal epistemic memory, continuous event-sourced capture, knowledge graph traversal, and 5-channel hybrid vector recall.

---

## 🏛️ Architecture & The 5-Stage Ingestion Pipeline

```
[User / Chat Utterance / Files]
       │
       ▼
 1. Synchronous Capture (L0 RAW) ──► Append-only raw event ledger (<20ms, zero LLM delay)
       │                             Stored in `sources`, `events`, and `chunks`
       ▼
 2. Local Model Extraction       ──► Local Ollama `qwen2.5:7b-instruct` extracts typed memories
       │                             Validated strictly against Draft 2020-12 JSON Schema
       ▼
 3. Verbatim Evidence Check      ──► RapidFuzz character-level span verification against raw text
       │                             Exact character coordinates saved in `citations`
       ▼
 4. Entity & Conflict Judging    ──► Normalized predicate ontology + Two-Tier Entity Resolution
       │                             Classifies as DUPLICATE, UPDATE (superseding), or CONTRADICT
       ▼
 5. Canonical Versioning (L1/L2) ──► Immutable versioned rows in `memories` and `triples`
                                     Transactional Outbox drains updates into Obsidian Markdown
```

---

## 🔍 The 5-Channel Hybrid Recall Pipeline

Retrival does not rely on naive vector search alone. It fuses **5 parallel retrieval channels in a single SQL round-trip**:

1. **Dense Vector Channel:** Cosine similarity via `pgvector` HNSW index on 768-d `nomic-embed-text` embeddings.
2. **Lexical / BM25 Channel:** Full-text search with `ts_rank_cd` on English lexemes + trigram matching (`pg_trgm`).
3. **Entity Channel:** Exact matches against canonical entity names and registered aliases (`entity_aliases`).
4. **Graph Expansion Channel:** 1-hop traversal across typed relational edges (`SUPPORTS`, `USES`, `DEPENDS_ON`, `PREFERS`).
5. **Temporal Decay Channel:** Validity filtering (`as_of`, `valid_from` to `valid_to`) combined with Ebbinghaus memory recency decay.

**Score Fusion:** Merged using Reciprocal Rank Fusion ($RRF, k=60$).

---

## 🗄️ Database Schema & Alembic Migrations

The engine operates on a normalized PostgreSQL schema (`lumen_memory` in development, `lumen_memory_test` in test):

| Table | Layer | Purpose |
|---|---|---|
| `sources` | L0 Raw | Origin registry (`chat`, `voice`, `file`, `web`, `api`) |
| `events` | L0 Raw | Append-only event log with SHA-256 tamper-evident hashes |
| `chunks` | L0 Raw | Text slices with character coordinates and tsvectors |
| `entities` | L1 Canonical | Canonical entity nodes with aliases and embeddings |
| `entity_aliases` | L1 Canonical | Normalized entity alias lookup table |
| `memories` | L1 Canonical | Bi-temporal versioned memory assertions with lineage tracking |
| `citations` | L1 Evidence | Character-level evidence spans linking memories to raw events |
| `claims` | L1 Epistemic | Assertion claims with confidence and credibility weights |
| `predicates` | L2 Relational | Controlled, extensible relationship predicate ontology |
| `triples` | L2 Relational | Directed knowledge graph edges with bi-temporal validity |
| `memory_relations` | L2 Relational | Typed graph relations between memories |
| `abstractions` | L2 Abstract | Pinned summaries, profiles, and behavioural directives |
| `review_queue` | Integrity Gate | Two-man human review buffer for detected contradictions |
| `memory_outbox` / `outbox` | Async Outbox | Transactional outbox with `pg_notify` for Obsidian vault sync |
| `retrieval_traces` | Observability | Full provenance traces recording ranking and fused results |

Run migrations:
```bash
alembic upgrade head
```

---

## 💻 Running Offline (Local-First)

The memory engine prioritizes 100% local, offline operation via Ollama:
- **LLM Extraction:** `ollama/qwen2.5:7b-instruct` (Metal GPU accelerated on macOS).
- **Embeddings:** `nomic-embed-text` (768 dimensions).
- **Fallback:** Deterministic n-gram hash adapter for unit test environments.

Toggle local vs cloud in `.env`:
```env
PREFER_LOCAL_MODELS=true
LUMEN_LLM_MODEL=qwen2.5:7b-instruct
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

---

## 🧪 Testing & Verification

1. **Unit Test Suite (21/21 passing):**
   ```bash
   PYTHONPATH=packages/memory-engine ./.venv/bin/pytest packages/memory-engine/tests -v
   ```

2. **Golden Memory Evaluation Suite (3/3 passing):**
   ```bash
   ./.venv/bin/python evals/memory/run.py
   ```

3. **Core Runtime Integration Tests (14/14 passing):**
   ```bash
   PYTHONPATH=apps/lumen-core ./.venv/bin/pytest apps/lumen-core/tests/test_session.py apps/lumen-core/tests/test_consolidation_daemon.py -v
   ```
