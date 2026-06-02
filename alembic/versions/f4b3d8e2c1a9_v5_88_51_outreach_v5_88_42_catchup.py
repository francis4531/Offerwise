"""v5_88_51_outreach_v5_88_42_catchup

Revision ID: f4b3d8e2c1a9
Revises: e7a2c9f1b3d4
Create Date: 2026-05-13 18:30:00.000000

Catch-up migration: v5.88.42 introduced three new tables and one new column
into models.py WITHOUT writing a corresponding Alembic migration. Production
(Postgres) never got these schema changes, and the absence was hidden until
v5.88.50 wired up an admin endpoint that actually queries OutreachLog
including the missing campaign_id column. That endpoint then 500s with:

    psycopg2.errors.UndefinedColumn: column outreach_log.campaign_id does not exist

This migration ALSO catches up v5.88.50's user.outreach_draft_* columns if
the v5.88.50 migration (e7a2c9f1b3d4) somehow didn't run.

Three tables added (only if not present — db.create_all() may have created
them on local-dev SQLite):

  outreach_templates    — reusable subject/body templates (v5.88.42 design,
                          unused by v5.88.50 UI but persisted for fallback)
  outreach_campaigns    — groups multiple OutreachLog sends as one batch
                          (only used by the v5.88.42 buyer-campaign flow,
                          which v5.88.50 replaced — kept for audit)
  outreach_unsubscribes — global do-not-contact list (USED by v5.88.50:
                          outreach_send_buyer_draft refuses to send to
                          emails on this list)

One column added (only if not present):

  outreach_log.campaign_id  — nullable FK to outreach_campaigns. Always
                              NULL for v5.88.50 sends (single-recipient
                              flow). Was NULL for B2B sends before too.

Idempotent: every CREATE TABLE and ADD COLUMN is guarded by an inspection
check. Safe to re-run.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f4b3d8e2c1a9'
down_revision: Union[str, None] = 'e7a2c9f1b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── 1. outreach_templates ─────────────────────────────────────────
    if 'outreach_templates' not in existing_tables:
        op.create_table(
            'outreach_templates',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False, unique=True),
            sa.Column('cohort', sa.String(20), nullable=False, server_default='buyer'),
            sa.Column('subject_template', sa.String(500), nullable=False),
            sa.Column('body_template', sa.Text(), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='100'),
            sa.Column('is_seeded', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
        )

    # ── 2. outreach_campaigns ─────────────────────────────────────────
    if 'outreach_campaigns' not in existing_tables:
        op.create_table(
            'outreach_campaigns',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('cohort', sa.String(20), nullable=False, server_default='buyer'),
            sa.Column('template_id', sa.Integer(),
                      sa.ForeignKey('outreach_templates.id'), nullable=True),
            sa.Column('subject_template', sa.String(500), nullable=False),
            sa.Column('body_template', sa.Text(), nullable=False),
            sa.Column('cohort_filter_json', sa.Text()),
            sa.Column('from_email', sa.String(255), nullable=False),
            sa.Column('reply_to_email', sa.String(255), nullable=False),
            sa.Column('recipient_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('sent_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('skipped_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('send_started_at', sa.DateTime(), nullable=True),
            sa.Column('send_completed_at', sa.DateTime(), nullable=True),
            sa.Column('status', sa.String(30), nullable=False, server_default='draft'),
        )
        # Index on created_at for sort-newest-first admin lookups
        op.create_index('ix_outreach_campaigns_created_at',
                        'outreach_campaigns', ['created_at'])

    # ── 3. outreach_unsubscribes ──────────────────────────────────────
    if 'outreach_unsubscribes' not in existing_tables:
        op.create_table(
            'outreach_unsubscribes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('email', sa.String(255), unique=True, nullable=False, index=True),
            sa.Column('reason', sa.String(30), nullable=False, server_default='manual'),
            sa.Column('campaign_id', sa.Integer(),
                      sa.ForeignKey('outreach_campaigns.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('notes', sa.String(500)),
        )

    # ── 4. outreach_log.campaign_id ───────────────────────────────────
    # This is the column the production query is actually failing on.
    # The v5.87.29 migration created outreach_log without it; v5.88.42
    # added campaign_id to the model but skipped the migration.
    if 'outreach_log' in existing_tables:
        outreach_log_cols = {c['name'] for c in inspector.get_columns('outreach_log')}
        if 'campaign_id' not in outreach_log_cols:
            op.add_column('outreach_log',
                          sa.Column('campaign_id', sa.Integer(),
                                    sa.ForeignKey('outreach_campaigns.id'),
                                    nullable=True))
            # Index for grouping by campaign
            op.create_index('ix_outreach_log_campaign_id',
                            'outreach_log', ['campaign_id'])

    # ── 5. Belt-and-suspenders: re-check user outreach_draft columns ──
    # In case e7a2c9f1b3d4 (v5.88.50) somehow didn't run or partially
    # ran, add the user.outreach_draft_* columns idempotently here too.
    # Costs nothing if they're already there.
    if 'users' in existing_tables:
        user_cols = {c['name'] for c in inspector.get_columns('users')}
        if 'outreach_draft_subject' not in user_cols:
            op.add_column('users',
                          sa.Column('outreach_draft_subject', sa.String(500), nullable=True))
        if 'outreach_draft_body' not in user_cols:
            op.add_column('users',
                          sa.Column('outreach_draft_body', sa.Text(), nullable=True))
        if 'outreach_draft_generated_at' not in user_cols:
            op.add_column('users',
                          sa.Column('outreach_draft_generated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Reverse the catchup. Drops in opposite order to honor FKs."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Drop outreach_log.campaign_id index + column
    if 'outreach_log' in existing_tables:
        try:
            op.drop_index('ix_outreach_log_campaign_id', table_name='outreach_log')
        except Exception:
            pass
        outreach_log_cols = {c['name'] for c in inspector.get_columns('outreach_log')}
        if 'campaign_id' in outreach_log_cols:
            try:
                op.drop_column('outreach_log', 'campaign_id')
            except Exception:
                pass

    # Drop the three tables (note: outreach_campaigns is referenced by
    # outreach_unsubscribes.campaign_id, so drop unsubscribes first).
    for tbl in ('outreach_unsubscribes', 'outreach_campaigns', 'outreach_templates'):
        if tbl in existing_tables:
            try:
                op.drop_table(tbl)
            except Exception:
                pass

    # Don't auto-drop user.outreach_draft_* — those are owned by
    # migration e7a2c9f1b3d4 (v5.88.50). Its downgrade handles them.
