"""v5_88_88_offerwatch_columns

Revision ID: c9d5f2b8a173
Revises: b8d3e6a1f924
Create Date: 2026-05-16 04:00:00.000000

v5.88.88 — Backfill the OfferWatch columns that v5.88.85/.86 introduced
in models.py but never actually wrote to production. The earlier
migration discipline relied on a stamp file (_MIGRATION_STAMP_PATH at
v5.80.15) which, once written in prod, caused the gated migration block
to be skipped forever — so neither this migration nor a corresponding
always-run ALTER block existed for these columns. Result: every
/api/watches and /api/alerts request 500'd in production because
SQLAlchemy generated SQL referencing columns that weren't there.

Columns added:
  property_watches.agent_briefing_id (v5.88.85) — FK to agent_briefings
    so OfferWatch can scope watches to the agent who created the briefing
  property_watches.survey_sent_at (v5.88.86) — idempotency anchor for
    the watch-lifecycle post-close survey job
  agent_alerts.resend_id (v5.88.86) — joins to EmailEvent.resend_id so
    opens/clicks attribute to specific alerts
  agent_alerts.view_count (v5.88.86) — passive portal-view counter

Index added: idx_agent_alerts_resend_id supports the EmailEvent join
in /api/admin/offerwatch-telemetry.

Idempotent: every ADD COLUMN is guarded by IF NOT EXISTS so re-running
the migration is safe even if the always-run block in app.py already
applied the change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d5f2b8a173'
down_revision: Union[str, None] = 'b8d3e6a1f924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # property_watches columns
    if 'property_watches' in tables:
        pw_cols = {c['name'] for c in inspector.get_columns('property_watches')}
        if 'agent_briefing_id' not in pw_cols:
            op.execute(
                "ALTER TABLE property_watches "
                "ADD COLUMN IF NOT EXISTS agent_briefing_id INTEGER"
            )
        if 'survey_sent_at' not in pw_cols:
            op.execute(
                "ALTER TABLE property_watches "
                "ADD COLUMN IF NOT EXISTS survey_sent_at TIMESTAMP"
            )

    # agent_alerts columns
    if 'agent_alerts' in tables:
        aa_cols = {c['name'] for c in inspector.get_columns('agent_alerts')}
        if 'resend_id' not in aa_cols:
            op.execute(
                "ALTER TABLE agent_alerts "
                "ADD COLUMN IF NOT EXISTS resend_id VARCHAR(100)"
            )
        if 'view_count' not in aa_cols:
            op.execute(
                "ALTER TABLE agent_alerts "
                "ADD COLUMN IF NOT EXISTS view_count INTEGER DEFAULT 0"
            )
        # Index supports the EmailEvent join in admin telemetry endpoint
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_alerts_resend_id "
            "ON agent_alerts(resend_id)"
        )


def downgrade() -> None:
    # No-op downgrade. These columns are additive; rolling back would lose
    # telemetry data and break code that assumes the columns exist. If a
    # rollback is genuinely needed, do it via a forward-migration that drops
    # the columns with explicit data-loss acknowledgment.
    pass
