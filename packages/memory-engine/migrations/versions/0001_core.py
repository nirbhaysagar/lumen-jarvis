"""core 17-table schema

Revision ID: 0001_core
Revises: 
Create Date: 2026-11-21

"""
from typing import Sequence, Union
from pathlib import Path
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0001_core'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql_path = Path(__file__).parent.parent / "0001_core.sql"
    sql = sql_path.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    pass
