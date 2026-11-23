"""dead letter and extraction attempts

Revision ID: 0003_dead_letter
Revises: 0002_compat_graph_outbox_facts
Create Date: 2026-11-23

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0003_dead_letter'
down_revision: Union[str, None] = '0002_compat_graph_outbox_facts'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_kind_check;
    ALTER TABLE review_queue ADD CONSTRAINT review_queue_kind_check
      CHECK (kind IN ('contradiction','entity_merge','low_confidence','belief_promotion',
                      'failed_extraction','vault_conflict'));

    CREATE TABLE IF NOT EXISTS extraction_attempts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      event_id uuid NOT NULL REFERENCES events(id),
      model text NOT NULL,
      attempt int NOT NULL,
      ok boolean NOT NULL,
      error_kind text,
      error_detail text,
      raw_output text,
      latency_ms int NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS extraction_attempts_event_idx ON extraction_attempts(event_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS extraction_attempts CASCADE;
    ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_kind_check;
    ALTER TABLE review_queue ADD CONSTRAINT review_queue_kind_check
      CHECK (kind IN ('contradiction','entity_merge','low_confidence','belief_promotion'));
    """)
