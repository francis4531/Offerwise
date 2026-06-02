"""v5_88_50_user_outreach_draft_columns

Revision ID: e7a2c9f1b3d4
Revises: d9e2b7a4c1f6
Create Date: 2026-05-13 17:30:00.000000

Adds three columns to the users table for per-user customer-discovery
email drafts. Mirrors the same pattern as outreach_contacts (B2B):

  outreach_draft_subject       (VARCHAR 500) — Claude-generated personalized subject
  outreach_draft_body          (TEXT)        — Claude-generated personalized body
  outreach_draft_generated_at  (DATETIME)    — when the draft was last (re)generated

Why on the users table instead of a new table: a single user has a single
in-flight draft at any time. After successful send, the draft columns are
cleared (so the next send starts from scratch and we don't surface a
stale draft as if it were new). If we ever need draft history, OutreachLog
already records every send with its full subject + body.

Why not reuse outreach_contacts: that table is for B2B prospects only.
Mixing buyer drafts in there confuses the cohort distinction.

Idempotent: tolerates re-run via column existence check.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e7a2c9f1b3d4'
down_revision: Union[str, None] = 'd9e2b7a4c1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the three outreach_draft columns to users. Tolerate replay."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'users' not in inspector.get_table_names():
        raise RuntimeError(
            'users table missing. Run earlier migrations first.'
        )

    existing = {col['name'] for col in inspector.get_columns('users')}

    new_cols = [
        ('outreach_draft_subject',      sa.Column('outreach_draft_subject',      sa.String(500))),
        ('outreach_draft_body',         sa.Column('outreach_draft_body',         sa.Text())),
        ('outreach_draft_generated_at', sa.Column('outreach_draft_generated_at', sa.DateTime())),
    ]
    for name, column in new_cols:
        if name not in existing:
            op.add_column('users', column)


def downgrade() -> None:
    """Remove the three columns. Tolerate already-absent."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'users' not in inspector.get_table_names():
        return

    existing = {col['name'] for col in inspector.get_columns('users')}
    for name in ('outreach_draft_generated_at', 'outreach_draft_body', 'outreach_draft_subject'):
        if name in existing:
            try:
                op.drop_column('users', name)
            except Exception:
                # SQLite drop_column is unreliable on older versions
                pass
