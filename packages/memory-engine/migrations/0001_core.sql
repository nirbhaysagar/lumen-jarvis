-- 0001_core.sql (Alembic op.execute) · Postgres 16/17 + pgvector >= 0.7
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

DO $$ BEGIN
    CREATE TYPE event_kind AS ENUM ('CAPTURE','DERIVED','SYSTEM');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE memory_type AS ENUM ('fact','decision','goal','assumption','insight',
    'question','action','constraint','event','entity');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE epistemic AS ENUM ('fact','belief','inference','decision');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE memory_status AS ENUM ('active','superseded','retracted','pending_review');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE relation_type AS ENUM ('RELATED_TO','SUPPORTS','CONTRADICTS','DEPENDS_ON',
    'CAUSED_BY','PART_OF','DERIVED_FROM','SUPERSEDES','MENTIONS');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- ── Contexts: retrieval boundaries (work/personal/project:lumen …) ──
CREATE TABLE IF NOT EXISTS contexts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    parent_id uuid REFERENCES contexts(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO contexts (slug, name) VALUES ('default', 'Default') ON CONFLICT (slug) DO NOTHING;

-- ── L0 RAW ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('chat','voice','file','web','note','api','import')),
    uri text,
    title text,
    author text, -- who asserted it ('user', a site, a person)
    content_hash text NOT NULL UNIQUE, -- sha256 of normalised content: real dedupe
    published_at timestamptz,
    captured_at timestamptz NOT NULL DEFAULT now(),
    context_id uuid NOT NULL REFERENCES contexts(id),
    metadata jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events ( -- VCEA log: append-only, never UPDATE/DELETE
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE, -- total order
    kind event_kind NOT NULL,
    type text NOT NULL, -- capture.chat | memory.created | memory.superseded | memory.tombstoned | …
    actor text NOT NULL, -- 'user' | 'worker:extract' | 'model:qwen2.5:7b' | 'system'
    source_id uuid REFERENCES sources(id),
    context_id uuid NOT NULL REFERENCES contexts(id),
    caused_by uuid[] NOT NULL DEFAULT '{}', -- parent event ids (provenance DAG)
    target_id uuid, -- memory/entity this event is about
    content text, -- raw text for CAPTURE
    payload jsonb NOT NULL DEFAULT '{}',
    content_hash text,
    idempotency_key text UNIQUE,
    tombstone boolean NOT NULL DEFAULT false, -- marks a forget/retract event
    occurred_at timestamptz NOT NULL DEFAULT now(), -- when it happened in the world
    recorded_at timestamptz NOT NULL DEFAULT now() -- when Lumen learned it
);
CREATE INDEX IF NOT EXISTS events_target_idx ON events (target_id, seq);
CREATE INDEX IF NOT EXISTS events_caused_idx ON events USING gin (caused_by);
CREATE INDEX IF NOT EXISTS events_type_idx ON events (type, recorded_at DESC);

CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END $$;

DROP TRIGGER IF EXISTS events_append_only ON events;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE IF NOT EXISTS chunks ( -- raw text slices: evidence spans + raw recall
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id uuid NOT NULL REFERENCES events(id),
    ord int NOT NULL,
    char_start int NOT NULL,
    char_end int NOT NULL,
    text text NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding vector(768), -- nomic-embed-text; NULL until embedded
    UNIQUE (event_id, ord)
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_vec_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- ── L1 CANONICAL ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS entities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name text NOT NULL,
    kind text NOT NULL DEFAULT 'other'
    CHECK (kind IN ('person','org','project','tool','place','concept','document','other')),
    description text,
    embedding vector(768),
    merged_into uuid REFERENCES entities(id), -- reversible merge, never DELETE
    context_id uuid REFERENCES contexts(id), -- NULL = global
    created_event uuid NOT NULL REFERENCES events(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (canonical_name, kind)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id uuid NOT NULL REFERENCES entities(id),
    alias text NOT NULL,
    PRIMARY KEY (entity_id, alias)
);
CREATE INDEX IF NOT EXISTS entities_name_trgm ON entities USING gin (canonical_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS aliases_lower_idx ON entity_aliases (lower(alias));
CREATE INDEX IF NOT EXISTS entities_vec_idx ON entities USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS memories ( -- one row per VERSION; rows are immutable
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    lineage_id uuid NOT NULL, -- stable id across versions
    version int NOT NULL DEFAULT 1,
    type memory_type NOT NULL,
    epistemic epistemic NOT NULL,
    asserted_by text NOT NULL, -- 'user' | source author | 'lumen'
    statement text NOT NULL, -- self-contained sentence, pronouns resolved
    subject_id uuid REFERENCES entities(id),
    predicate text, -- snake_case, free vocabulary (no FK → no 500s)
    object_id uuid REFERENCES entities(id),
    object_value jsonb,
    cardinality text NOT NULL DEFAULT 'many' CHECK (cardinality IN ('one','many')),
    polarity boolean NOT NULL DEFAULT true, -- false = negation ("does NOT use X")
    confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance real NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    context_id uuid NOT NULL REFERENCES contexts(id),
    status memory_status NOT NULL DEFAULT 'active',
    -- bi-temporal: valid time (world) + transaction time (Lumen's knowledge)
    valid_from timestamptz,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz, -- set once by the close-out trigger rule
    superseded_by uuid REFERENCES memories(id),
    created_event uuid NOT NULL REFERENCES events(id),
    embedding vector(768),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', statement)) STORED,
    UNIQUE (lineage_id, version)
);
CREATE INDEX IF NOT EXISTS memories_current_idx ON memories (lineage_id) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS memories_spo_idx ON memories (subject_id, predicate) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS memories_obj_idx ON memories (object_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS memories_ctx_idx ON memories (context_id, type, status);
CREATE INDEX IF NOT EXISTS memories_time_idx ON memories (valid_from, valid_to);
CREATE INDEX IF NOT EXISTS memories_rec_idx ON memories (recorded_at, superseded_at);
CREATE INDEX IF NOT EXISTS memories_tsv_idx ON memories USING gin (tsv);
CREATE INDEX IF NOT EXISTS memories_trgm_idx ON memories USING gin (statement gin_trgm_ops);
CREATE INDEX IF NOT EXISTS memories_vec_idx ON memories USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- only the close-out columns may change, and only once
CREATE OR REPLACE FUNCTION memories_closeout_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE free text[] := '{status,superseded_at,superseded_by,valid_to,embedding,tsv}';
BEGIN
IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'memories are append-only'; END IF;
IF (to_jsonb(NEW) - free) IS DISTINCT FROM (to_jsonb(OLD) - free)
OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
OR (OLD.valid_to IS NOT NULL AND NEW.valid_to IS DISTINCT FROM OLD.valid_to)
THEN RAISE EXCEPTION 'memory % is immutable; write a new version', OLD.id; END IF;
RETURN NEW; -- close-out fields may be set once (supersede / retract / embed later)
END $$;

DROP TRIGGER IF EXISTS memories_immutable ON memories;
CREATE TRIGGER memories_immutable BEFORE UPDATE OR DELETE ON memories
FOR EACH ROW EXECUTE FUNCTION memories_closeout_only();

CREATE OR REPLACE VIEW current_memories AS
SELECT * FROM memories WHERE superseded_at IS NULL AND status IN ('active','pending_review');

-- What a source SAYS (may disagree with what Lumen holds as fact)
CREATE TABLE IF NOT EXISTS claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES sources(id),
    event_id uuid NOT NULL REFERENCES events(id),
    claimant text NOT NULL, -- author / speaker
    text text NOT NULL,
    conditions text, -- "only for > 10k writes/s"
    credibility real CHECK (credibility BETWEEN 0 AND 1),
    memory_id uuid REFERENCES memories(id), -- canonical memory it became / supports
    stance text NOT NULL DEFAULT 'supports' CHECK (stance IN ('supports','refutes')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS claims_memory_idx ON claims (memory_id);

-- Evidence spans: exact characters in the raw event that justify a memory
CREATE TABLE IF NOT EXISTS citations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id uuid NOT NULL REFERENCES memories(id),
    event_id uuid NOT NULL REFERENCES events(id),
    chunk_id uuid REFERENCES chunks(id),
    claim_id uuid REFERENCES claims(id),
    char_start int NOT NULL,
    char_end int NOT NULL,
    quote text NOT NULL,
    verified boolean NOT NULL, -- quote found verbatim/fuzzy in event.content
    role text NOT NULL DEFAULT 'evidence' CHECK (role IN ('evidence','counter','reaffirm')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (char_end > char_start)
);
CREATE INDEX IF NOT EXISTS citations_memory_idx ON citations (memory_id);
CREATE INDEX IF NOT EXISTS citations_event_idx ON citations (event_id);

CREATE TABLE IF NOT EXISTS memory_relations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type relation_type NOT NULL,
    from_memory uuid NOT NULL REFERENCES memories(id),
    to_memory uuid REFERENCES memories(id),
    to_entity uuid REFERENCES entities(id), -- MENTIONS points at an entity
    confidence real NOT NULL DEFAULT 1.0,
    created_event uuid NOT NULL REFERENCES events(id),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    CHECK ((to_memory IS NULL) <> (to_entity IS NULL)),
    CHECK (type <> 'MENTIONS' OR to_entity IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS rel_from_idx ON memory_relations (from_memory, type);
CREATE INDEX IF NOT EXISTS rel_to_idx ON memory_relations (to_memory, type);
CREATE INDEX IF NOT EXISTS rel_entity_idx ON memory_relations (to_entity) WHERE to_entity IS NOT NULL;

-- ── L2 ABSTRACT ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS abstractions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('profile','pattern','summary','directive')),
    title text NOT NULL,
    body text NOT NULL,
    subject_id uuid REFERENCES entities(id),
    context_id uuid NOT NULL REFERENCES contexts(id),
    confidence real NOT NULL,
    proof_count int NOT NULL DEFAULT 0, -- how many memories back it
    pinned boolean NOT NULL DEFAULT false, -- Letta-style "core block": always in context
    status memory_status NOT NULL DEFAULT 'active',
    superseded_by uuid REFERENCES abstractions(id),
    created_event uuid NOT NULL REFERENCES events(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    embedding vector(768)
);

CREATE TABLE IF NOT EXISTS abstraction_sources ( -- DERIVED_FROM for L2 → L1
    abstraction_id uuid NOT NULL REFERENCES abstractions(id),
    memory_id uuid NOT NULL REFERENCES memories(id),
    PRIMARY KEY (abstraction_id, memory_id)
);
CREATE INDEX IF NOT EXISTS abstractions_vec_idx ON abstractions USING hnsw (embedding vector_cosine_ops);

-- ── LIFECYCLE / OPS ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memory_signals ( -- mutable stats kept OUT of immutable rows
    lineage_id uuid PRIMARY KEY,
    access_count int NOT NULL DEFAULT 0,
    reaffirm_count int NOT NULL DEFAULT 0,
    last_accessed_at timestamptz,
    last_reaffirmed_at timestamptz,
    stability_days real NOT NULL DEFAULT 7 -- Ebbinghaus S; grows on each reinforcement
);

CREATE TABLE IF NOT EXISTS review_queue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('contradiction','entity_merge','low_confidence','belief_promotion')),
    memory_ids uuid[] NOT NULL DEFAULT '{}',
    entity_ids uuid[] NOT NULL DEFAULT '{}',
    reason text NOT NULL,
    proposal jsonb NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','rejected')),
    created_event uuid NOT NULL REFERENCES events(id),
    decided_event uuid REFERENCES events(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS review_open_idx ON review_queue (created_at) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS jobs ( -- durable queue, no Redis/Celery
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL, -- embed | extract | resolve | consolidate
    event_id uuid REFERENCES events(id),
    payload jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','dead')),
    attempts int NOT NULL DEFAULT 0,
    run_after timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    last_error text,
    UNIQUE (kind, event_id)
);
CREATE INDEX IF NOT EXISTS jobs_ready_idx ON jobs (run_after) WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS retrieval_traces (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    query text NOT NULL,
    context_ids uuid[] NOT NULL,
    as_of timestamptz,
    plan jsonb NOT NULL, -- parsed entities, time range, type hints
    channels jsonb NOT NULL, -- {vector:[{id,rank,score}], bm25:[…], entity:[…], graph:[…], temporal:[…]}
    fused jsonb NOT NULL, -- [{id, rrf, lifecycle, final}]
    returned uuid[] NOT NULL,
    latency_ms int NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbox ( -- kept from Lumen: Obsidian/vault projection
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id uuid NOT NULL REFERENCES events(id),
    topic text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempts int NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION outbox_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM pg_notify('lumen_outbox', NEW.id::text); RETURN NEW; END $$;

DROP TRIGGER IF EXISTS outbox_notify_trg ON outbox;
CREATE TRIGGER outbox_notify_trg AFTER INSERT ON outbox
FOR EACH ROW EXECUTE FUNCTION outbox_notify();
