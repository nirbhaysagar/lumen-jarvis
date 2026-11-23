"""Add processed_at and last_error columns to outbox table

Revision ID: 0006_outbox_cols
Revises: 0005_current_view
Create Date: 2026-11-23

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0006_outbox_cols'
down_revision: Union[str, None] = '0005_current_view'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE outbox ADD COLUMN IF NOT EXISTS processed_at timestamptz;
    ALTER TABLE outbox ADD COLUMN IF NOT EXISTS last_error text;
    CREATE INDEX IF NOT EXISTS idx_outbox_status_created ON outbox (status, created_at);
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS idx_outbox_status_created;
    ALTER TABLE outbox DROP COLUMN IF EXISTS last_error;
    ALTER TABLE outbox DROP COLUMN IF EXISTS processed_at;
    """)
