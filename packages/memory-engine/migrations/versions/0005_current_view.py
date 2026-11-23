"""Split current_memories view and add pending_memories view for recall honesty

Revision ID: 0005_current_view
Revises: 0004_embedding_meta
Create Date: 2026-11-23

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0005_current_view'
down_revision: Union[str, None] = '0004_embedding_meta'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update current_memories to ONLY include active memories (never unverified/pending)
    op.execute("""
    CREATE OR REPLACE VIEW current_memories AS
      SELECT * FROM memories WHERE superseded_at IS NULL AND status = 'active';
    """)
    # 2. Add pending_memories view for human audit / review
    op.execute("""
    CREATE OR REPLACE VIEW pending_memories AS
      SELECT * FROM memories WHERE superseded_at IS NULL AND status = 'pending_review';
    """)


def downgrade() -> None:
    op.execute("""
    DROP VIEW IF EXISTS pending_memories;
    CREATE OR REPLACE VIEW current_memories AS
      SELECT * FROM memories WHERE superseded_at IS NULL AND status IN ('active','pending_review');
    """)
