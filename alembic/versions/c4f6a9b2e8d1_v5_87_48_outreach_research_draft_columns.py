"""v5_87_48_outreach_research_draft_columns

Revision ID: c4f6a9b2e8d1
Revises: b3e8a2f5c9d1
Create Date: 2026-05-01 20:00:00.000000

Adds four columns to outreach_contacts for the research+draft pipeline:

  focus_areas         (TEXT)        — synthesized 2-4 sentences describing
                                       what the company is focused on right
                                       now. Generated via Anthropic web
                                       search at prospect-add time.
  draft_subject       (VARCHAR 500) — Claude-generated personalized subject
                                       line, awaiting founder review.
  draft_body          (TEXT)        — Claude-generated personalized body
                                       conditioned on focus_areas + role.
                                       Drafts-only — never auto-sent.
  draft_generated_at  (DATETIME)    — when the draft was last regenerated;
                                       UI uses this to show 'stale draft'
                                       hints if the draft is more than a
                                       few days old.

Rationale: the v7 customer-discovery loop calls for higher-caliber per-
prospect personalization (matching the Roc360-shaped emails Francis has
already proven work). Manual research per prospect is slow; pre-baking
focus areas + draft into the OutreachContact row lets the founder review
and send in seconds rather than rebuild context every time.

Idempotent: ALTER TABLE ... ADD COLUMN IF NOT EXISTS would be ideal, but
SQLite ALTER TABLE doesn't support IF NOT EXISTS. We catch the
duplicate-column error instead so the migration is replay-safe.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c4f6a9b2e8d1'
down_revision: Union[str, None] = 'b3e8a2f5c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the four columns. Tolerate replay (column already exists)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Confirm the table exists. If outreach_contacts isn't there, the
    # earlier migration (f2d9b6e1c0a3) didn't run — bail loudly.
    if 'outreach_contacts' not in inspector.get_table_names():
        raise RuntimeError(
            'outreach_contacts table missing. Run earlier migrations first '
            '(particularly f2d9b6e1c0a3_v5_87_29_outreach_tables.py).'
        )

    existing = {col['name'] for col in inspector.get_columns('outreach_contacts')}

    new_cols = [
        ('focus_areas',        sa.Column('focus_areas',        sa.Text())),
        ('draft_subject',      sa.Column('draft_subject',      sa.String(500))),
        ('draft_body',         sa.Column('draft_body',         sa.Text())),
        ('draft_generated_at', sa.Column('draft_generated_at', sa.DateTime())),
    ]
    for name, column in new_cols:
        if name not in existing:
            op.add_column('outreach_contacts', column)


def downgrade() -> None:
    """Remove the four columns. Tolerate already-absent."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'outreach_contacts' not in inspector.get_table_names():
        return

    existing = {col['name'] for col in inspector.get_columns('outreach_contacts')}
    for name in ('draft_generated_at', 'draft_body', 'draft_subject', 'focus_areas'):
        if name in existing:
            try:
                op.drop_column('outreach_contacts', name)
            except Exception:
                # SQLite drop_column is unreliable on older versions;
                # tolerate and move on.
                pass
