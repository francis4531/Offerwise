"""v5_87_72_infra_invoice_email_auto

Revision ID: e6b2a9f4c8d3
Revises: d5a1f8c3b9e7
Create Date: 2026-05-04 17:00:00.000000

Adds four columns to infra_invoices to support v5.87.72 email-parser
auto-ingestion of vendor invoices via Resend Inbound webhooks:

  source            VARCHAR(20)   default 'manual'   -- 'manual' | 'email_auto'
  parse_confidence  FLOAT         nullable           -- Claude self-reported 0-1
  raw_email_id      VARCHAR(100)  nullable           -- Resend email_id for audit
  needs_review      BOOLEAN       default false      -- low-confidence rows

Idempotent: each ALTER guarded so re-running on a partial state is safe.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import logging


revision: str = 'e6b2a9f4c8d3'
down_revision: Union[str, None] = 'd5a1f8c3b9e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, col: str) -> bool:
    """Defensive existence check — survives partial-migration replay."""
    try:
        insp = sa.inspect(bind)
        return col in {c['name'] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    log = logging.getLogger('alembic.runtime.migration')

    additions = [
        ('source',           sa.Column('source',           sa.String(length=20),  nullable=True)),
        ('parse_confidence', sa.Column('parse_confidence', sa.Float(),            nullable=True)),
        ('raw_email_id',     sa.Column('raw_email_id',     sa.String(length=100), nullable=True)),
        ('needs_review',     sa.Column('needs_review',     sa.Boolean(),          nullable=True)),
    ]

    for col_name, col_def in additions:
        if _has_column(bind, 'infra_invoices', col_name):
            log.info(f'v5.87.72: column infra_invoices.{col_name} already exists, skipping')
            continue
        op.add_column('infra_invoices', col_def)
        log.info(f'v5.87.72: added column infra_invoices.{col_name}')

    # Backfill: existing rows are manual entries; this is a one-shot UPDATE,
    # NULL stays as 'manual' via the ORM default but we make it explicit on disk
    # so downstream queries don't need to coalesce.
    #
    # v5.88.52 fix: wrap each UPDATE in a SAVEPOINT so a failure of the
    # UPDATE itself does NOT poison the outer transaction. Before this fix,
    # the try/except caught the Python exception but Postgres kept the
    # transaction in error state — every later statement (including
    # alembic_version INSERT) failed with InFailedSqlTransaction.
    # This bug silently blocked ALL migrations from advancing past this
    # one for ~9 days on production, including v5.88.47/.50/.51.
    for sql in (
        "UPDATE infra_invoices SET source='manual' WHERE source IS NULL",
        "UPDATE infra_invoices SET needs_review=false WHERE needs_review IS NULL",
    ):
        sp = None
        try:
            sp = bind.begin_nested()  # SAVEPOINT
            op.execute(sql)
            sp.commit()
        except Exception as e:
            if sp is not None:
                try:
                    sp.rollback()
                except Exception:
                    pass
            log.warning(f'v5.87.72: backfill skipped ({sql[:40]}...): {e}')

    # Index on needs_review for the "rows pending operator review" query path.
    try:
        op.create_index('ix_infra_invoices_needs_review', 'infra_invoices', ['needs_review'])
    except Exception:
        pass  # exists or unsupported


def downgrade() -> None:
    # Drop columns in reverse order. Safe because they are additive.
    try:
        op.drop_index('ix_infra_invoices_needs_review', table_name='infra_invoices')
    except Exception:
        pass
    for col in ('needs_review', 'raw_email_id', 'parse_confidence', 'source'):
        try:
            op.drop_column('infra_invoices', col)
        except Exception:
            pass
