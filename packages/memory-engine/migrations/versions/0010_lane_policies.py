"""Add lane_policies table (L1)

Revision ID: 0010_lane_policies
Revises: 0009_directives
Create Date: 2026-11-25

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0010_lane_policies'
down_revision: Union[str, None] = '0009_directives'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS lane_policies (
        lane memory_lane PRIMARY KEY,
        decay_per_day real NOT NULL CHECK (decay_per_day >= 0),
        min_boost real NOT NULL DEFAULT 0.85,
        extraction_prompt text NOT NULL DEFAULT 'default',
        auto_commit_min_conf real NOT NULL DEFAULT 0.7
    );
    INSERT INTO lane_policies (lane, decay_per_day, min_boost, extraction_prompt, auto_commit_min_conf) VALUES
        ('chat', 0.05, 0.80, 'chat', 0.8),
        ('work', 0.01, 0.85, 'work', 0.7),
        ('code', 0.0, 1.0, 'code', 0.7),
        ('personal', 0.002, 0.90, 'default', 0.7)
    ON CONFLICT (lane) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lane_policies;")
