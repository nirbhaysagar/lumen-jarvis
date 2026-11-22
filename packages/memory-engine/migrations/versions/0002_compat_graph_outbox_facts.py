"""compat graph outbox facts

Revision ID: 0002_compat_graph_outbox_facts
Revises: 0001_core
Create Date: 2026-11-22

"""
from typing import Sequence, Union
from pathlib import Path
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002_compat_graph_outbox_facts'
down_revision: Union[str, None] = '0001_core'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql_path = Path(__file__).parent.parent / "0002_compat_graph_outbox_facts.sql"
    sql = sql_path.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    pass
