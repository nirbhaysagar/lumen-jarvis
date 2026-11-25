"""Add redactions table for privacy purge (F1)

Revision ID: 0008_redactions
Revises: 0007_save_aspects
Create Date: 2026-11-25

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0008_redactions'
down_revision: Union[str, None] = '0007_save_aspects'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS redactions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id uuid NOT NULL REFERENCES events(id),
        char_start int NOT NULL,
        char_end int NOT NULL,
        reason text NOT NULL,
        created_event uuid NOT NULL REFERENCES events(id),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS redactions_event_idx ON redactions(event_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS redactions;
    """)
