"""v5_87_59_discovery_queue

Revision ID: d5a1f8c3b9e7
Revises: c4f6a9b2e8d1
Create Date: 2026-05-02 18:30:00.000000

Creates the discovery_queue table that backs the v5.87.59 nightly
discovery crawler. Each row represents one company domain queued for
discovery via Hunter (primary) or Snov (fallback). The 3:30am job
processes pending rows up to a per-night cap (default 5).

State machine:
  pending  → running   → completed | failed | deferred
  failed   → pending   (auto-retry up to 3 attempts)
  deferred → pending   (when provider credit floors recover)

Idempotent: ALTER TABLE IF NOT EXISTS pattern not portable to SQLite,
so we use a try/except around create_table for replay safety.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd5a1f8c3b9e7'
down_revision: Union[str, None] = 'c4f6a9b2e8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create discovery_queue table. Replay-safe."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'discovery_queue' in inspector.get_table_names():
        # Already exists — replay scenario, no-op
        return

    op.create_table(
        'discovery_queue',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('domain', sa.String(255), nullable=False),
        sa.Column('wedge', sa.String(50)),
        sa.Column('queued_by', sa.String(20), nullable=False, server_default='manual'),
        sa.Column('queued_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_attempt_at', sa.DateTime()),
        sa.Column('completed_at', sa.DateTime()),
        sa.Column('prospects_found_count', sa.Integer(), server_default='0'),
        sa.Column('drafts_generated_count', sa.Integer(), server_default='0'),
        sa.Column('source_used', sa.String(20)),
        sa.Column('error', sa.String(1000)),
        sa.Column('title_filter', sa.String(500)),
        sa.Column('seniority_filter', sa.String(100)),
    )
    op.create_index('ix_discovery_queue_domain', 'discovery_queue', ['domain'])
    op.create_index('ix_discovery_queue_status', 'discovery_queue', ['status'])
    op.create_index('ix_discovery_queue_queued_at', 'discovery_queue', ['queued_at'])


def downgrade() -> None:
    """Drop the discovery_queue table. Replay-safe."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'discovery_queue' not in inspector.get_table_names():
        return

    try:
        op.drop_index('ix_discovery_queue_queued_at', table_name='discovery_queue')
        op.drop_index('ix_discovery_queue_status', table_name='discovery_queue')
        op.drop_index('ix_discovery_queue_domain', table_name='discovery_queue')
    except Exception:
        pass
    op.drop_table('discovery_queue')
