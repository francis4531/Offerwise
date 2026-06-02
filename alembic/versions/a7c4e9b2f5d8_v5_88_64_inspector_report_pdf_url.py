"""v5_88_64_inspector_report_pdf_url

Revision ID: a7c4e9b2f5d8
Revises: f4b3d8e2c1a9
Create Date: 2026-05-14 17:30:00.000000

v5.88.64: Inspector PDF link pass-through (Option B).

Adds a single nullable column to inspector_reports:

  inspection_pdf_url  VARCHAR(2048) NULL

This stores the inspector's own hosted PDF URL (e.g., Spectora /
HomeGauge public report link). OfferWise does NOT host the PDF
content — we just render a "View Full Inspection Report" button
on the buyer-facing report that links out to this URL.

Rationale for not storing the PDF itself (Option A rejected):
- Inspector's existing inspection software already hosts the PDF
- Storing other people's inspection PDFs creates copyright, retention,
  and storage-cost questions we don't want to take on at this stage
- Inspector keeps control: if they take the link down, the button
  breaks — that's their call to make about their content

Why this column is nullable:
- The field is OPTIONAL on the inspector portal's New Analysis form
- Existing inspector_reports rows pre-date this field; they stay NULL
- If the inspector doesn't paste anything, no button renders on the
  buyer report (silent absence is correct here)

Idempotent: ADD COLUMN is guarded by a column-existence check.
Safe to re-run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c4e9b2f5d8'
down_revision: Union[str, None] = 'f4b3d8e2c1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Guard against the table not existing on a fresh / partial install.
    # (inspector_reports has existed since v5.78ish, so this is belt-and-
    # suspenders, but cheap.)
    if 'inspector_reports' not in set(inspector.get_table_names()):
        return

    existing_cols = {c['name'] for c in inspector.get_columns('inspector_reports')}
    if 'inspection_pdf_url' not in existing_cols:
        op.add_column(
            'inspector_reports',
            sa.Column('inspection_pdf_url', sa.String(2048), nullable=True)
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'inspector_reports' not in set(inspector.get_table_names()):
        return
    existing_cols = {c['name'] for c in inspector.get_columns('inspector_reports')}
    if 'inspection_pdf_url' in existing_cols:
        op.drop_column('inspector_reports', 'inspection_pdf_url')
