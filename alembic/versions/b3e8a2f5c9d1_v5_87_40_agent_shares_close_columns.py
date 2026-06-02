"""v5_87_40_agent_shares_close_columns

Revision ID: b3e8a2f5c9d1
Revises: a1c4e7f9b2d6
Create Date: 2026-04-30 14:00:00.000000

Adds two columns to agent_shares for the agent post-close flywheel:

  deal_closed_at   — DateTime, nullable. When set, signals a deal close and
                     gates the send_agent_postclose_email() trigger.
  final_sale_price — Float, nullable. The actual close price (vs property_price
                     which is asking).

Background:
  send_agent_postclose_email() in flywheel_notifications.py reads and writes
  share.deal_closed_at and share.final_sale_price — but those columns never
  existed on the model. Every call would have raised AttributeError. This
  migration adds them so the agent flywheel can actually fire.

Trigger wiring (added in v5.87.40 alongside this migration):
  When a buyer submits PostCloseSurvey with did_buy='yes_closed', the survey
  handler looks up matching AgentShare rows by (buyer_email + property_address)
  and fires send_agent_postclose_email(share.id). This converts the agent
  flywheel from agent-self-report (rare) to buyer-driven (frequent).

Idempotent: uses information_schema check, safe on dbs where the columns
already exist (e.g. dev environments using db.create_all()).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e8a2f5c9d1'
down_revision: Union[str, None] = 'a1c4e7f9b2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_cols = {c['name'] for c in insp.get_columns('agent_shares')}

    if 'deal_closed_at' not in existing_cols:
        op.add_column(
            'agent_shares',
            sa.Column('deal_closed_at', sa.DateTime(), nullable=True),
        )
        op.create_index(
            'ix_agent_shares_deal_closed_at',
            'agent_shares',
            ['deal_closed_at'],
        )

    if 'final_sale_price' not in existing_cols:
        op.add_column(
            'agent_shares',
            sa.Column('final_sale_price', sa.Float(), nullable=True),
        )


def downgrade() -> None:
    # Drop index first, then columns. Wrap each drop in a try/except since
    # downgrade may run against a db where db.create_all() never wrote them.
    try:
        op.drop_index('ix_agent_shares_deal_closed_at', table_name='agent_shares')
    except Exception:
        pass
    try:
        op.drop_column('agent_shares', 'final_sale_price')
    except Exception:
        pass
    try:
        op.drop_column('agent_shares', 'deal_closed_at')
    except Exception:
        pass
