-- 003_transactional_outbox.sql
-- Transactional Outbox Pattern for Asynchronous, Resilient Obsidian Vault Sync
-- Decouples ACID relational memory operations from filesystem I/O.

CREATE TABLE IF NOT EXISTS memory_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(32) NOT NULL, -- 'ENTITY', 'TRIPLE', 'FACT', 'EPISODE'
    aggregate_id UUID NOT NULL,
    event_type VARCHAR(32) NOT NULL,     -- 'CREATED', 'UPDATED', 'SUPERSEDED'
    payload JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'PROCESSED', 'FAILED'
    retry_count INT NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

-- Partial index for high-throughput, low-latency fetching of pending events
CREATE INDEX IF NOT EXISTS idx_memory_outbox_pending
    ON memory_outbox (created_at ASC)
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_memory_outbox_aggregate
    ON memory_outbox (aggregate_type, aggregate_id);

-- PostgreSQL LISTEN / NOTIFY trigger function
CREATE OR REPLACE FUNCTION notify_memory_outbox()
RETURNS trigger AS $$
BEGIN
    -- Broadcast notification with the outbox record ID on channel 'memory_outbox_channel'
    PERFORM pg_notify('memory_outbox_channel', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_memory_outbox_inserted ON memory_outbox;

CREATE TRIGGER trg_memory_outbox_inserted
    AFTER INSERT ON memory_outbox
    FOR EACH ROW
    EXECUTE FUNCTION notify_memory_outbox();
