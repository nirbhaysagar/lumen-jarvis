-- 0002_compat_graph_outbox_facts.sql
-- Harmonizes the 17-table schema with Graph, Predicates, Triples, Facts, and Transactional Outbox

-- 1. Harmonize entities & events tables
ALTER TABLE entities ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'concept';
ALTER TABLE entities ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE entities ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
ALTER TABLE entities ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE entities ALTER COLUMN created_event DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS entities_canonical_name_key ON entities (canonical_name);
CREATE INDEX IF NOT EXISTS idx_entities_aliases ON entities USING gin (aliases);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities (category);

ALTER TABLE events ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS conversation_id TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE events ADD COLUMN IF NOT EXISTS hash TEXT;
ALTER TABLE events ALTER COLUMN kind DROP NOT NULL;
ALTER TABLE events ALTER COLUMN type DROP NOT NULL;
ALTER TABLE events ALTER COLUMN actor DROP NOT NULL;
ALTER TABLE events ALTER COLUMN context_id DROP NOT NULL;

-- 2. Predicates Table (Controlled Extensible Ontology)
CREATE TABLE IF NOT EXISTS predicates (
    id TEXT PRIMARY KEY,
    inverse_predicate TEXT,
    description TEXT,
    is_symmetric BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed Canonical Predicates
INSERT INTO predicates (id, inverse_predicate, description, is_symmetric) VALUES
    ('FOUNDED', 'FOUNDED_BY', 'Entity founded an organization, company, or project', FALSE),
    ('FOUNDED_BY', 'FOUNDED', 'Organization or project founded by an entity', FALSE),
    ('DEVELOPS', 'DEVELOPED_BY', 'Entity builds, authors, or develops a project or software', FALSE),
    ('DEVELOPED_BY', 'DEVELOPS', 'Software or project developed by an entity', FALSE),
    ('PREFERS', NULL, 'User or entity prefers a tool, workflow, or environment', FALSE),
    ('DISLIKES', NULL, 'User or entity explicitly dislikes or rejects an option', FALSE),
    ('USES', 'USED_BY', 'Entity uses or employs a technology, library, or dependency', FALSE),
    ('USED_BY', 'USES', 'Technology used by a system or project', FALSE),
    ('DEPENDS_ON', 'DEPENDENCY_OF', 'Project or component depends on another system', FALSE),
    ('DEPENDENCY_OF', 'DEPENDS_ON', 'Component is a dependency of another system', FALSE),
    ('CONTAINS', 'PART_OF', 'Container or system contains a subsystem or element', FALSE),
    ('PART_OF', 'CONTAINS', 'Subsystem is part of a larger container or entity', FALSE),
    ('WRITTEN_IN', NULL, 'Software is implemented in a specific programming language', FALSE),
    ('INSPIRED_BY', NULL, 'Concept or design is modeled after or inspired by an entity', FALSE),
    ('MEMBER_OF', 'HAS_MEMBER', 'Person or entity belongs to an organization or group', FALSE),
    ('HAS_MEMBER', 'MEMBER_OF', 'Group or organization includes an entity', FALSE)
ON CONFLICT (id) DO NOTHING;

-- 3. Triples Table (Relational Graph Edges with Bi-Temporal Validity)
CREATE TABLE IF NOT EXISTS triples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL REFERENCES predicates(id) ON UPDATE CASCADE,
    object_id UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    source_event_ids UUID[] NOT NULL DEFAULT '{}',
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject_id);
CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object_id);
CREATE INDEX IF NOT EXISTS idx_triples_active ON triples(valid_to) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_triples_sub_pred_obj ON triples(subject_id, predicate, object_id) WHERE valid_to IS NULL;

-- 4. Review Queue Schema Alignment
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS proposal_type TEXT;
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS subject_raw TEXT;
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS predicate_raw TEXT;
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS object_raw TEXT;
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS resolved_subject_id UUID REFERENCES entities(id);
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS resolved_predicate TEXT REFERENCES predicates(id);
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS resolved_object_id UUID REFERENCES entities(id);
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 1.0;
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'TIER_B';
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS conflict_reason TEXT;
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS conflicting_triple_id UUID REFERENCES triples(id);
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS source_event_id UUID REFERENCES events(id);
ALTER TABLE review_queue ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE review_queue ALTER COLUMN kind DROP NOT NULL;
ALTER TABLE review_queue ALTER COLUMN reason DROP NOT NULL;
ALTER TABLE review_queue ALTER COLUMN proposal DROP NOT NULL;
ALTER TABLE review_queue ALTER COLUMN created_event DROP NOT NULL;
ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_status_check;
ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_kind_check;

-- 5. Facts Table (Canonical Derived Facts L1)
CREATE TABLE IF NOT EXISTS facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity TEXT NOT NULL,
    attribute TEXT NOT NULL,
    value JSONB NOT NULL,
    memory_type TEXT NOT NULL,
    epistemic_type TEXT NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    source_event_ids UUID[] NOT NULL DEFAULT '{}',
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to TIMESTAMPTZ,
    embedding vector(768) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts (entity);
CREATE INDEX IF NOT EXISTS idx_facts_type ON facts (memory_type);
CREATE INDEX IF NOT EXISTS idx_facts_embedding_hnsw ON facts USING hnsw (embedding vector_cosine_ops);

-- 6. Transactional Memory Outbox Table + Trigger
CREATE TABLE IF NOT EXISTS memory_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(32) NOT NULL,
    aggregate_id UUID NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    retry_count INT NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_memory_outbox_pending ON memory_outbox (created_at ASC) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_memory_outbox_aggregate ON memory_outbox (aggregate_type, aggregate_id);

CREATE OR REPLACE FUNCTION notify_memory_outbox()
RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('memory_outbox_channel', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_memory_outbox_inserted ON memory_outbox;
CREATE TRIGGER trg_memory_outbox_inserted
    AFTER INSERT ON memory_outbox
    FOR EACH ROW
    EXECUTE FUNCTION notify_memory_outbox();

-- 7. Full-text and trigram indexing
CREATE INDEX IF NOT EXISTS idx_facts_fts ON facts USING gin (
    to_tsvector('english', entity || ' ' || attribute || ' ' || COALESCE(value::text, ''))
);
CREATE INDEX IF NOT EXISTS idx_facts_trgm ON facts USING gin (
    (entity || ' ' || attribute || ' ' || COALESCE(value::text, '')) gin_trgm_ops
);
CREATE INDEX IF NOT EXISTS idx_entities_fts ON entities USING gin (
    to_tsvector('english', canonical_name || ' ' || COALESCE(description, ''))
);
CREATE INDEX IF NOT EXISTS idx_entities_trgm ON entities USING gin (
    (canonical_name || ' ' || COALESCE(description, '')) gin_trgm_ops
);
