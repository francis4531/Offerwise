"""v5_87_22_ml_finding_labels_dedup_index

Revision ID: e1c8d4a7b9f6
Revises: d8e5f6a9b2c3
Create Date: 2026-04-26 10:00:00.000000

Adds composite btree index ix_ml_finding_labels_dedup on
(finding_text, source_version) to ml_finding_labels.

Rationale:
  SocrataCrawler._add_unlabeled_finding runs a SELECT WHERE finding_text=?
  AND source_version=? once for every scraped row to detect duplicates.
  Without this index, each query is a full table scan. With ~91K rows
  in the table, a 10K-row crawl took ~25-30 minutes — long enough that
  "Crawl All Active" appeared stuck on the first city and never reached
  the others.

  After adding this index, Postgres can satisfy the dedup query with
  a btree probe (~1ms vs ~50-200ms), cutting Chicago's runtime from
  ~30 min to ~3 min.

  finding_text is capped at 500 chars by the inserter, which keeps the
  composite key well under Postgres's 8KB btree leaf entry limit.

Online-safe: uses CREATE INDEX CONCURRENTLY which doesn't lock the table.
This means the migration runs outside a transaction (CONCURRENTLY can't be
in one) and is idempotent via IF NOT EXISTS so re-runs are safe.

Idempotent: IF NOT EXISTS guard on both upgrade and downgrade. Safe to
run on a DB where the index has already been created (e.g., dev that ran
db.create_all() after the model change).
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'e1c8d4a7b9f6'
down_revision: Union[str, None] = 'd8e5f6a9b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction. With
    # Alembic's default transactional behavior we need to break out.
    # autocommit_block() handles this cleanly.
    with op.get_context().autocommit_block():
        op.execute(
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ml_finding_labels_dedup '
            'ON ml_finding_labels (finding_text, source_version)'
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            'DROP INDEX CONCURRENTLY IF EXISTS ix_ml_finding_labels_dedup'
        )
