"""v5_87_38_ad_campaign_config

Revision ID: a1c4e7f9b2d6
Revises: f2d9b6e1c0a3
Create Date: 2026-04-28 19:00:00.000000

Adds the ad_campaign_config table for prepaid budget tracking per ad channel.

Primary use: Zillow Group ads are prepaid — you load $501 for 30 days and
burn it down. The dashboard shows daily spend correctly via GTMAdPerformance
but lacked any budget envelope context. This table adds one row per channel
holding the budget, window, and notes; daily spend is joined live to compute
remaining budget.

Schema design notes:
- channel is the primary key (one campaign per channel; multi-campaign
  support deferred until the actual usage pattern justifies the complexity)
- prepaid_budget is nullable — postpay/PPC channels can still use this
  table for date-window display without a budget number
- end_date is nullable for open-ended campaigns
- No FK to GTMAdPerformance; the join is a date-window query

Idempotent: uses IF NOT EXISTS guards, safe on dbs where db.create_all()
already produced the table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e7f9b2d6'
down_revision: Union[str, None] = 'f2d9b6e1c0a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'ad_campaign_config' not in existing_tables:
        op.create_table(
            'ad_campaign_config',
            sa.Column('channel', sa.String(50), primary_key=True),
            sa.Column('campaign_name', sa.String(200)),
            sa.Column('prepaid_budget', sa.Float()),
            sa.Column('start_date', sa.Date(), nullable=False),
            sa.Column('end_date', sa.Date()),
            sa.Column('notes', sa.Text()),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table('ad_campaign_config')
