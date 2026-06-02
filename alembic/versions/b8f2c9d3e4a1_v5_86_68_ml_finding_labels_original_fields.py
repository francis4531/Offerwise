"""v5_86_68_ml_finding_labels_original_fields

Adds `original_category` and `original_severity` to `ml_finding_labels`.
These columns exist on the SQLAlchemy model (used when an inspector corrects
an AI-parsed label — we preserve the original for training-data quality analysis)
but were never added via migration. That drift caused a
`psycopg2.errors.InFailedSqlTransaction` on any query touching this table
after the v5.86.67 deploy exposed the inconsistency.

Revision ID: b8f2c9d3e4a1
Revises: a7b3c8f91d22
Create Date: 2026-04-20 13:30:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b8f2c9d3e4a1'
down_revision: Union[str, None] = 'a7b3c8f91d22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: check what's already there before adding. Production may have
    # these columns already if someone ran ALTER TABLE manually; dev envs may
    # not. This migration should be safe either way.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    try:
        existing_cols = {c['name'] for c in inspector.get_columns('ml_finding_labels')}
    except Exception:
        # Table doesn't exist — nothing to migrate. Full-schema migration handles creation.
        return

    with op.batch_alter_table('ml_finding_labels') as batch_op:
        if 'original_category' not in existing_cols:
            batch_op.add_column(sa.Column('original_category', sa.String(length=100), nullable=True))
        if 'original_severity' not in existing_cols:
            batch_op.add_column(sa.Column('original_severity', sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ml_finding_labels') as batch_op:
        batch_op.drop_column('original_severity')
        batch_op.drop_column('original_category')
