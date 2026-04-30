"""v5_87_29_outreach_tables

Revision ID: f2d9b6e1c0a3
Revises: e1c8d4a7b9f6
Create Date: 2026-04-26 18:00:00.000000

Adds two tables for founder-led customer discovery + B2B cold outreach:

  outreach_contacts — manually-added B2B prospects (enterprise wedge: renovation
                      lenders, insurtechs, brokerage tech). Buyer signups are
                      NOT stored here; the admin UI looks them up live from
                      the users table.

  outreach_log      — append-only log of every send. Joins to email_send_log
                      via resend_id and to either users.id or
                      outreach_contacts.id depending on cohort.

Rationale (v7 report card · April 26, 2026):
  Rowen S6 prescribes founder-led customer discovery as the next-quarter
  priority. The 14+ uncontacted signups + 10-account enterprise wedge list
  both flow through the same admin UI, with replies routed to the founder's
  Gmail via Resend's reply_to header. EmailEvent already tracks opens/clicks
  via the existing /webhook/resend endpoint, so this migration only adds the
  tables that link those events back to a specific outreach attempt.

Idempotent: uses IF NOT EXISTS guards. Safe on dbs where db.create_all()
already produced the tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2d9b6e1c0a3'
down_revision: Union[str, None] = 'e1c8d4a7b9f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'outreach_contacts' not in existing_tables:
        op.create_table(
            'outreach_contacts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('cohort', sa.String(20), nullable=False, server_default='b2b'),
            sa.Column('email', sa.String(255), nullable=False),
            sa.Column('name', sa.String(255)),
            sa.Column('title', sa.String(255)),
            sa.Column('company', sa.String(255)),
            sa.Column('linkedin_url', sa.String(500)),
            sa.Column('wedge', sa.String(50)),
            sa.Column('notes', sa.Text()),
            sa.Column('status', sa.String(30), server_default='not_contacted'),
            sa.Column('last_contacted_at', sa.DateTime()),
            sa.Column('replied_at', sa.DateTime()),
            sa.Column('last_reply_summary', sa.Text()),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index('ix_outreach_contacts_cohort', 'outreach_contacts', ['cohort'])
        op.create_index('ix_outreach_contacts_email', 'outreach_contacts', ['email'])
        op.create_index('ix_outreach_contacts_company', 'outreach_contacts', ['company'])
        op.create_index('ix_outreach_contacts_wedge', 'outreach_contacts', ['wedge'])
        op.create_index('ix_outreach_contacts_status', 'outreach_contacts', ['status'])
        op.create_index('ix_outreach_contacts_last_contacted_at', 'outreach_contacts', ['last_contacted_at'])

    if 'outreach_log' not in existing_tables:
        op.create_table(
            'outreach_log',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('sent_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('cohort', sa.String(20), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('contact_id', sa.Integer(), sa.ForeignKey('outreach_contacts.id'), nullable=True),
            sa.Column('to_email', sa.String(255), nullable=False),
            sa.Column('subject', sa.String(500)),
            sa.Column('body', sa.Text()),
            sa.Column('reply_to', sa.String(255)),
            sa.Column('resend_id', sa.String(100)),
            sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('error', sa.String(500)),
        )
        op.create_index('ix_outreach_log_sent_at', 'outreach_log', ['sent_at'])
        op.create_index('ix_outreach_log_cohort', 'outreach_log', ['cohort'])
        op.create_index('ix_outreach_log_user_id', 'outreach_log', ['user_id'])
        op.create_index('ix_outreach_log_contact_id', 'outreach_log', ['contact_id'])
        op.create_index('ix_outreach_log_to_email', 'outreach_log', ['to_email'])
        op.create_index('ix_outreach_log_resend_id', 'outreach_log', ['resend_id'])


def downgrade() -> None:
    op.drop_table('outreach_log')
    op.drop_table('outreach_contacts')
