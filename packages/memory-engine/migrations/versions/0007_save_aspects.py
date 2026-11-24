"""Save pipeline aspects: memory_lane, sensitivity, save_runs table, hash chain columns

Revision ID: 0007_save_aspects
Revises: 0006_outbox_cols
Create Date: 2026-11-24

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0007_save_aspects'
down_revision: Union[str, None] = '0006_outbox_cols'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    -- 1. Enums for lane and sensitivity
    DO $$ BEGIN
        CREATE TYPE memory_lane AS ENUM ('personal','work','code','chat');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;

    DO $$ BEGIN
        CREATE TYPE sensitivity AS ENUM ('normal','private','secret');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;

    -- 2. Add columns to memories
    ALTER TABLE memories ADD COLUMN IF NOT EXISTS lane memory_lane NOT NULL DEFAULT 'personal';
    ALTER TABLE memories ADD COLUMN IF NOT EXISTS sensitivity sensitivity NOT NULL DEFAULT 'normal';
    CREATE INDEX IF NOT EXISTS memories_lane_idx ON memories (context_id, lane) WHERE superseded_at IS NULL;

    -- 3. Update memories_closeout_only trigger function free-list
    CREATE OR REPLACE FUNCTION memories_closeout_only()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $function$
        DECLARE free text[] := '{status,superseded_at,superseded_by,valid_to,embedding,tsv,embedding_model,lane,sensitivity}';
        BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'memories are append-only'; END IF;
        IF (to_jsonb(NEW) - free) IS DISTINCT FROM (to_jsonb(OLD) - free)
        OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
        OR (OLD.valid_to IS NOT NULL AND NEW.valid_to IS DISTINCT FROM OLD.valid_to)
        THEN RAISE EXCEPTION 'memory % is immutable; write a new version', OLD.id; END IF;
        RETURN NEW;
        END $function$;

    -- 4. Add columns to events, chunks, entities, citations, and memory_signals
    ALTER TABLE events ADD COLUMN IF NOT EXISTS speaker text;
    ALTER TABLE events ADD COLUMN IF NOT EXISTS prev_hash text;
    ALTER TABLE events ADD COLUMN IF NOT EXISTS chain_hash text;

    ALTER TABLE chunks ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
    ALTER TABLE entities ADD COLUMN IF NOT EXISTS description text;
    ALTER TABLE entities ADD COLUMN IF NOT EXISTS embedding vector(768);

    ALTER TABLE citations ADD COLUMN IF NOT EXISTS chunk_id uuid REFERENCES chunks(id);
    ALTER TABLE citations ADD COLUMN IF NOT EXISTS role text NOT NULL DEFAULT 'evidence';

    ALTER TABLE memory_signals ADD COLUMN IF NOT EXISTS last_reaffirmed_at timestamptz;

    -- 5. Sources source_key
    ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_content_hash_key;
    ALTER TABLE sources ADD COLUMN IF NOT EXISTS source_key text;
    CREATE UNIQUE INDEX IF NOT EXISTS sources_source_key_uq ON sources(source_key);

    -- 6. Save runs table for audit and transparency
    CREATE TABLE IF NOT EXISTS save_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id uuid NOT NULL REFERENCES events(id),
        stage_ms jsonb NOT NULL,
        counts jsonb NOT NULL,
        dropped jsonb NOT NULL DEFAULT '[]',
        model text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS save_runs_event_idx ON save_runs(event_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS save_runs;
    DROP INDEX IF EXISTS sources_source_key_uq;
    ALTER TABLE sources DROP COLUMN IF EXISTS source_key;

    ALTER TABLE memory_signals DROP COLUMN IF EXISTS last_reaffirmed_at;
    ALTER TABLE citations DROP COLUMN IF EXISTS role;
    ALTER TABLE citations DROP COLUMN IF EXISTS chunk_id;

    ALTER TABLE entities DROP COLUMN IF EXISTS embedding;
    ALTER TABLE entities DROP COLUMN IF EXISTS description;
    ALTER TABLE chunks DROP COLUMN IF EXISTS metadata;

    ALTER TABLE events DROP COLUMN IF EXISTS chain_hash;
    ALTER TABLE events DROP COLUMN IF EXISTS prev_hash;
    ALTER TABLE events DROP COLUMN IF EXISTS speaker;

    DROP INDEX IF EXISTS memories_lane_idx;
    ALTER TABLE memories DROP COLUMN IF EXISTS sensitivity;
    ALTER TABLE memories DROP COLUMN IF EXISTS lane;

    CREATE OR REPLACE FUNCTION memories_closeout_only()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $function$
        DECLARE free text[] := '{status,superseded_at,superseded_by,valid_to,embedding,tsv,embedding_model}';
        BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'memories are append-only'; END IF;
        IF (to_jsonb(NEW) - free) IS DISTINCT FROM (to_jsonb(OLD) - free)
        OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
        OR (OLD.valid_to IS NOT NULL AND NEW.valid_to IS DISTINCT FROM OLD.valid_to)
        THEN RAISE EXCEPTION 'memory % is immutable; write a new version', OLD.id; END IF;
        RETURN NEW;
        END $function$;

    DROP TYPE IF EXISTS sensitivity;
    DROP TYPE IF EXISTS memory_lane;
    """)
