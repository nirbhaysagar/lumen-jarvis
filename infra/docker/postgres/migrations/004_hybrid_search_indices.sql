-- 004_hybrid_search_indices.sql
-- Full-Text (tsvector) and Trigram (pg_trgm) indexing for BM25-style lexical search
-- Used in conjunction with pgvector HNSW and Knowledge Graph CTEs for Reciprocal Rank Fusion (RRF).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Facts Full-Text Search (English stemming, stopword removal)
CREATE INDEX IF NOT EXISTS idx_facts_fts ON facts USING gin (
    to_tsvector('english', entity || ' ' || attribute || ' ' || COALESCE(value::text, ''))
);

-- Facts Trigram Index for fuzzy substring matching and code identifiers
CREATE INDEX IF NOT EXISTS idx_facts_trgm ON facts USING gin (
    (entity || ' ' || attribute || ' ' || COALESCE(value::text, '')) gin_trgm_ops
);

-- Entities Full-Text Search across canonical name and description (aliases already indexed via GIN)
CREATE INDEX IF NOT EXISTS idx_entities_fts ON entities USING gin (
    to_tsvector('english', canonical_name || ' ' || COALESCE(description, ''))
);

-- Entities Trigram Index
CREATE INDEX IF NOT EXISTS idx_entities_trgm ON entities USING gin (
    (canonical_name || ' ' || COALESCE(description, '')) gin_trgm_ops
);
