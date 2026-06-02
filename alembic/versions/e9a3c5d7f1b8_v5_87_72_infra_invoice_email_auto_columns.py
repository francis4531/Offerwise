"""v5_87_72_infra_invoice_email_auto_columns

Revision ID: e9a3c5d7f1b8
Revises: d5a1f8c3b9e7
Create Date: 2026-05-04 19:00:00.000000

Adds 4 columns to infra_invoices to support email-based auto-ingestion
of vendor invoices via Resend Inbound webhook + Claude Haiku parsing.

Columns added:
  - source           VARCHAR(20)  default 'manual'   ('manual' | 'email_auto')
  - parse_confidence FLOAT        nullable           Claude's self-reported 0.0-1.0
  - raw_email_id     VARCHAR(100) nullable           Resend email_id for audit
  - needs_review     BOOLEAN      default false, indexed

Idempotent: uses try/except per-column so partial replays succeed.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e9a3c5d7f1b8'
down_revision: Union[str, None] = 'd5a1f8c3b9e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _try_add_column(table: str, column: sa.Column) -> None:
    """Add column, swallow 'already exists' errors for replay safety."""
    try:
        op.add_column(table, column)
    except Exception as e:
        msg = str(e).lower()
        if 'already exists' in msg or 'duplicate column' in msg:
            return
        raise


def upgrade() -> None:
    _try_add_column('infra_invoices', sa.Column(
        'source', sa.String(20), nullable=True, server_default='manual',
    ))
    _try_add_column('infra_invoices', sa.Column(
        'parse_confidence', sa.Float(), nullable=True,
    ))
    _try_add_column('infra_invoices', sa.Column(
        'raw_email_id', sa.String(100), nullable=True,
    ))
    _try_add_column('infra_invoices', sa.Column(
        'needs_review', sa.Boolean(), nullable=True, server_default=sa.false(),
    ))
    # Index on needs_review for the "review queue" filter
    try:
        op.create_index('ix_infra_invoices_needs_review',
                        'infra_invoices', ['needs_review'])
    except Exception as e:
        if 'already exists' not in str(e).lower():
            raise


def downgrade() -> None:
    try:
        op.drop_index('ix_infra_invoices_needs_review', table_name='infra_invoices')
    except Exception:
        pass
    for col in ('needs_review', 'raw_email_id', 'parse_confidence', 'source'):
        try:
            op.drop_column('infra_invoices', col)
        except Exception:
            pass
