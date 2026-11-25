"""Add directive columns to abstractions table (D1)

Revision ID: 0009_directives
Revises: 0008_redactions
Create Date: 2026-11-25

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0009_directives'
down_revision: Union[str, None] = '0008_redactions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE abstractions ADD COLUMN IF NOT EXISTS directive_type text
      CHECK (directive_type IS NULL OR directive_type IN ('preference','constraint','goal','style','context'));
    ALTER TABLE abstractions ADD COLUMN IF NOT EXISTS priority int
      CHECK (priority IS NULL OR (priority >= 1 AND priority <= 10));
    CREATE INDEX IF NOT EXISTS abstractions_directives_idx
      ON abstractions (context_id, priority DESC) WHERE kind = 'directive' AND status = 'active';
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS abstractions_directives_idx;
    ALTER TABLE abstractions DROP COLUMN IF EXISTS priority;
    ALTER TABLE abstractions DROP COLUMN IF EXISTS directive_type;
    """)
