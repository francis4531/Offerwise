"""v5_88_75_agent_briefing_table

Revision ID: b8d3e6a1f924
Revises: a7c4e9b2f5d8
Create Date: 2026-05-15 10:00:00.000000

v5.88.75 — Agent Briefing v0 Release 1: data model + form.

Creates the agent_briefings table. This is a NEW table parallel to
agent_shares (the legacy "forward a link to your buyer" flow). The
two flows coexist intentionally during v0 — AgentShare is kept under
a "Legacy" sidebar label while AgentBriefing is the primary product.

The table is created from scratch (not by altering an existing one).
Idempotent: ADD TABLE is guarded by a table-exists check so re-running
the migration is safe.

Columns:
  Core:                id, created_at, updated_at, agent_id, agent_user_id
  Property:            property_address, property_price
  Side:                representing ('buyer' | 'seller')
  Client info:         client_name, client_email
  Source document:     inspection_text, inspection_pdf_filename
  Budget tiers:        budget_qualified, budget_comfortable, budget_preferred
                       (only populated when representing='buyer')
  Required input:      agent_commentary (REQUIRED — the agent's voice)
  Analysis output:     analysis_json, offer_strategy_json, bottom_line
                       (empty in R1; populated by R2/R3 pipelines)
  Sharing:             share_token (unique, indexed)
  Branding snapshot:   agent_name_on_report, agent_biz_on_report
  Tracking:            client_viewed_at, view_count, sent_to_client_at
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8d3e6a1f924'
down_revision: Union[str, None] = 'a7c4e9b2f5d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'agent_briefings' in set(inspector.get_table_names()):
        # Idempotent re-run; table already exists from a prior partial
        # migration. Nothing to do.
        return

    op.create_table(
        'agent_briefings',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('created_at', sa.DateTime, nullable=False, index=True),
        sa.Column('updated_at', sa.DateTime, nullable=False),

        sa.Column('agent_id', sa.Integer, sa.ForeignKey('agents.id'), nullable=False, index=True),
        sa.Column('agent_user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),

        sa.Column('property_address', sa.String(500), nullable=False),
        sa.Column('property_price', sa.Float),

        # Which side — 'buyer' or 'seller'. Required.
        sa.Column('representing', sa.String(10), nullable=False),

        sa.Column('client_name', sa.String(255)),
        sa.Column('client_email', sa.String(255)),

        sa.Column('inspection_text', sa.Text),
        sa.Column('inspection_pdf_filename', sa.String(500)),

        sa.Column('budget_qualified', sa.Float),
        sa.Column('budget_comfortable', sa.Float),
        sa.Column('budget_preferred', sa.Float),

        # The agent's commentary — REQUIRED.
        sa.Column('agent_commentary', sa.Text, nullable=False),

        # Analysis output (empty at R1, populated R2/R3)
        sa.Column('analysis_json', sa.Text),
        sa.Column('offer_strategy_json', sa.Text),
        sa.Column('bottom_line', sa.Text),

        sa.Column('share_token', sa.String(32), nullable=False, unique=True, index=True),

        sa.Column('agent_name_on_report', sa.String(255)),
        sa.Column('agent_biz_on_report', sa.String(255)),

        sa.Column('client_viewed_at', sa.DateTime),
        sa.Column('view_count', sa.Integer, default=0),
        sa.Column('sent_to_client_at', sa.DateTime),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'agent_briefings' in set(inspector.get_table_names()):
        op.drop_table('agent_briefings')
