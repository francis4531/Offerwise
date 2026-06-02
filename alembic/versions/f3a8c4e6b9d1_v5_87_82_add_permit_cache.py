"""v5_87_82_add_permit_cache

Revision ID: f3a8c4e6b9d1
Revises: c5e7d9a3b1f8
Create Date: 2026-05-05 11:30:00.000000

Adds a permit_cache table backing the per-jurisdiction permit lookup
introduced in v5.87.82. Each row caches one LLM finding for a unique
(jurisdiction_key, system_key) tuple.

Cache TTL is enforced at read time (default 90 days, configurable via
PERMIT_CACHE_DAYS env var). Stale rows are not auto-deleted; they are
simply ignored on read and overwritten on next LLM lookup. A periodic
cleanup is unnecessary because new lookups always upsert.

v5.88.53 fix: original migration was NOT idempotent — it called
op.create_table directly with no existence check. On production where
db.create_all() had already created the table from the SQLAlchemy
model, this migration would crash with DuplicateTable and block the
entire downstream chain (v5.88.47 .. v5.88.51 schema changes never
ran). Wrapping in an existence check makes the migration safe to
re-run on partial states without breaking.

Idempotent on a fresh DB OR a DB where create_all() already produced
the table. Drop on downgrade is safe; no other table references this
one.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f3a8c4e6b9d1'
down_revision: Union[str, None] = 'c5e7d9a3b1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'permit_cache' in existing_tables:
        # Table already exists (created by db.create_all() on legacy
        # production). Nothing to do. v5.88.53 added this guard after
        # discovering this exact migration was blocking 9 days of queued
        # schema changes on prod with DuplicateTable.
        return

    op.create_table(
        'permit_cache',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('jurisdiction_key', sa.String(120), nullable=False, index=True),
        sa.Column('system_key', sa.String(120), nullable=False, index=True),
        sa.Column('payload_json', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint('jurisdiction_key', 'system_key',
                            name='uq_permit_cache_juris_system'),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'permit_cache' in set(inspector.get_table_names()):
        op.drop_table('permit_cache')
