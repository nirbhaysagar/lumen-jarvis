"""record embedding model per memory and update closeout trigger

Revision ID: 0004_embedding_meta
Revises: 0003_dead_letter
Create Date: 2026-11-23

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0004_embedding_meta'
down_revision: Union[str, None] = '0003_dead_letter'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_model text;

    CREATE OR REPLACE FUNCTION memories_closeout_only() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE free text[] := '{status,superseded_at,superseded_by,valid_to,embedding,tsv,embedding_model}';
    BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'memories are append-only'; END IF;
    IF (to_jsonb(NEW) - free) IS DISTINCT FROM (to_jsonb(OLD) - free)
    OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
    OR (OLD.valid_to IS NOT NULL AND NEW.valid_to IS DISTINCT FROM OLD.valid_to)
    THEN RAISE EXCEPTION 'memory % is immutable; write a new version', OLD.id; END IF;
    RETURN NEW;
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION memories_closeout_only() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE free text[] := '{status,superseded_at,superseded_by,valid_to,embedding,tsv}';
    BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'memories are append-only'; END IF;
    IF (to_jsonb(NEW) - free) IS DISTINCT FROM (to_jsonb(OLD) - free)
    OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
    OR (OLD.valid_to IS NOT NULL AND NEW.valid_to IS DISTINCT FROM OLD.valid_to)
    THEN RAISE EXCEPTION 'memory % is immutable; write a new version', OLD.id; END IF;
    RETURN NEW;
    END $$;

    ALTER TABLE memories DROP COLUMN IF EXISTS embedding_model;
    """)
