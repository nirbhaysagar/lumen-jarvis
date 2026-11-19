-- Migration 002: Knowledge Graph Entities, Predicates, Triples, and Review Queue
-- Implements Event-Sourced Entity Resolution, Canonical Aliases, and Tiered Review Staging

-- 1. Predicates Table (Controlled Extensible Ontology)
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

-- 2. Entities Table (Canonical Registry with Aliases & Embeddings)
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'concept',
    aliases TEXT[] NOT NULL DEFAULT '{}',
    description TEXT,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_entities_aliases ON entities USING gin(aliases);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(category);
CREATE INDEX IF NOT EXISTS idx_entities_embedding_hnsw ON entities USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

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

-- 4. Review Queue (Staging Buffer & Human-in-the-Loop Gate)
CREATE TABLE IF NOT EXISTS review_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_type TEXT NOT NULL,
    subject_raw TEXT NOT NULL,
    predicate_raw TEXT NOT NULL,
    object_raw TEXT NOT NULL,
    resolved_subject_id UUID REFERENCES entities(id),
    resolved_predicate TEXT REFERENCES predicates(id),
    resolved_object_id UUID REFERENCES entities(id),
    confidence FLOAT NOT NULL DEFAULT 1.0,
    tier TEXT NOT NULL DEFAULT 'TIER_B',
    status TEXT NOT NULL DEFAULT 'PENDING',
    conflict_reason TEXT,
    conflicting_triple_id UUID REFERENCES triples(id),
    source_event_id UUID REFERENCES events(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_review_queue_tier ON review_queue(tier);
