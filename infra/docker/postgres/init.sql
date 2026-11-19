-- PostgreSQL Initialization Script for Lumen Hub
-- Automatically executed on first container boot

-- 1. Enable pgvector for high-dimensional semantic search
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Enable UUID generation for distributed trace and entity IDs
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 3. Provision Task Checkpoints table for durable Task Supervisor recovery
CREATE TABLE IF NOT EXISTS task_checkpoints (
    task_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(64),
    state VARCHAR(32) NOT NULL DEFAULT 'QUEUED',
    progress DOUBLE PRECISION DEFAULT 0.0,
    status_message TEXT,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_checkpoints_conv ON task_checkpoints(conversation_id);
CREATE INDEX IF NOT EXISTS idx_task_checkpoints_state ON task_checkpoints(state);

-- 4. Confirm extensions are installed
DO $$
BEGIN
    RAISE NOTICE 'Lumen Postgres initialized with vector, uuid-ossp, and task_checkpoints.';
END $$;
