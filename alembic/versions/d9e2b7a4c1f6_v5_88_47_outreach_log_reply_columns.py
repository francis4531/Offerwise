"""v5_88_47_outreach_log_reply_columns

Revision ID: d9e2b7a4c1f6
Revises: f3a8c4e6b9d1
Create Date: 2026-05-13 16:00:00.000000

Adds two columns to outreach_log so buyer-cohort replies can be tracked
in the same place as B2B replies. B2B replies already live on
outreach_contacts.replied_at + last_reply_summary (one row per contact),
but buyer sends point at users.id which isn't a sane home for reply state
(a single user may be emailed across multiple campaigns over time).

  replied_at       (DATETIME) — when the founder manually flagged this
                                 send as having received a reply. NULL
                                 means no reply yet.
  reply_summary    (TEXT)     — free-form note the founder wrote about
                                 what the reply said. NULL by default.

Both populated by the new /api/admin/outreach/reply/<log_id> endpoint
from the v5.88.47 Results admin page. The same endpoint also bumps the
linked entity:
  - cohort='b2b'    → OutreachContact.status='replied' + replied_at + last_reply_summary
  - cohort='buyer'  → no User-side bump (the buyer's reply state belongs
                      to the send, not the user — same user may reply to
                      one campaign but not another)

Idempotent: tolerates re-run via column existence check.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd9e2b7a4c1f6'
down_revision: Union[str, None] = 'f3a8c4e6b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add replied_at + reply_summary to outreach_log. Tolerate replay."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'outreach_log' not in inspector.get_table_names():
        raise RuntimeError(
            'outreach_log table missing. Run earlier migrations first '
            '(particularly f2d9b6e1c0a3_v5_87_29_outreach_tables.py).'
        )

    existing = {col['name'] for col in inspector.get_columns('outreach_log')}

    new_cols = [
        ('replied_at',    sa.Column('replied_at',    sa.DateTime())),
        ('reply_summary', sa.Column('reply_summary', sa.Text())),
    ]
    for name, column in new_cols:
        if name not in existing:
            op.add_column('outreach_log', column)


def downgrade() -> None:
    """Remove the two columns. Tolerate already-absent."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'outreach_log' not in inspector.get_table_names():
        return

    existing = {col['name'] for col in inspector.get_columns('outreach_log')}
    for name in ('reply_summary', 'replied_at'):
        if name in existing:
            try:
                op.drop_column('outreach_log', name)
            except Exception:
                # SQLite drop_column is unreliable on older versions;
                # tolerate and move on.
                pass
