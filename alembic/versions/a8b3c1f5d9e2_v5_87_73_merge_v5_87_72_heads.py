"""v5_87_73_merge_v5_87_72_heads

Revision ID: a8b3c1f5d9e2
Revises: e6b2a9f4c8d3, e9a3c5d7f1b8
Create Date: 2026-05-04 21:30:00.000000

Merges the two parallel v5.87.72 migration heads that resulted from a
duplicate migration being created in admin_routes work:

  - e9a3c5d7f1b8 (v5_87_72_infra_invoice_email_auto_columns)
  - e6b2a9f4c8d3 (v5_87_72_infra_invoice_email_auto)

Both add identical columns (source, parse_confidence, raw_email_id,
needs_review) to infra_invoices. They were applied via separate
manual `alembic upgrade <rev>` calls during the v5.87.72 deploy. The
DB schema is identical regardless of order; this merge just cleans up
the alembic_version state so future migrations have a single head to
chain off.

This migration has NO upgrade/downgrade body — it exists purely to
declare that revision a8b3c1f5d9e2 is the descendant of both prior
heads, giving alembic a single linear history going forward.
"""
from typing import Sequence, Union


revision: str = 'a8b3c1f5d9e2'
down_revision: Union[str, Sequence[str], None] = ('e6b2a9f4c8d3', 'e9a3c5d7f1b8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
