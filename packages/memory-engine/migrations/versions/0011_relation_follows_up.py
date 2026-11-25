"""Add FOLLOWS_UP to relation_type enum (C1)

Revision ID: 0011_relation_follows_up
Revises: 0010_lane_policies
Create Date: 2026-11-25

"""
from typing import Sequence, Union
from alembic import op

revision: str = '0011_relation_follows_up'
down_revision: Union[str, None] = '0010_lane_policies'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE relation_type ADD VALUE IF NOT EXISTS 'FOLLOWS_UP';")


def downgrade() -> None:
    # Postgres enums do not support dropping values cleanly without recreating the type.
    pass
