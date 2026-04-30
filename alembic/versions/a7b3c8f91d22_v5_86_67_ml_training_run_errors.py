"""v5_86_67_ml_training_run_errors

Revision ID: a7b3c8f91d22
Revises: 1f857ccea478
Create Date: 2026-04-20 10:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a7b3c8f91d22'
down_revision: Union[str, None] = '1f857ccea478'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add error columns to ml_training_runs. Each stage (fc, cd, rc) gets its
    # own error field so a failure on one doesn't mask information about others.
    # Idempotent: skip columns that already exist (SQLite test DBs sometimes
    # have them from earlier dev runs).
    #
    # Also: if the ml_training_runs table doesn't exist at all (fresh DB case),
    # skip cleanly. The v5.86.69 migration will later adopt the table from
    # the model definition. Before v5.86.69 was written, this migration would
    # crash on fresh DBs because ml_training_runs was only ever created by
    # db.create_all() at boot, not by any migration.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'ml_training_runs' not in inspector.get_table_names():
        # Table doesn't exist yet. Later migration c1d4e5f6a789 will adopt it
        # with all columns including the error fields, so skipping here is safe.
        return

    existing_cols = {c['name'] for c in inspector.get_columns('ml_training_runs')}

    with op.batch_alter_table('ml_training_runs') as batch_op:
        if 'fc_error' not in existing_cols:
            batch_op.add_column(sa.Column('fc_error', sa.Text(), nullable=True))
        if 'cd_error' not in existing_cols:
            batch_op.add_column(sa.Column('cd_error', sa.Text(), nullable=True))
        if 'rc_error' not in existing_cols:
            batch_op.add_column(sa.Column('rc_error', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ml_training_runs') as batch_op:
        batch_op.drop_column('rc_error')
        batch_op.drop_column('cd_error')
        batch_op.drop_column('fc_error')
