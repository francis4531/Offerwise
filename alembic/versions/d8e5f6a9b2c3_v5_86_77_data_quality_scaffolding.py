"""v5_86_77_data_quality_scaffolding

Revision ID: d8e5f6a9b2c3
Revises: c1d4e5f6a789
Create Date: 2026-04-21 15:30:00.000000

Adds:
  1. v2 label columns on ml_finding_labels (category_v2, severity_v2, etc.)
  2. New ml_ingestion_jobs table for tracking background data work

Rationale:
  - v2 columns let Stream 3 re-label the existing corpus without destroying
    the v1 labels — so we can compare old vs new and roll back if needed.
  - source_version distinguishes rows from different crawlers / extractors.
  - ml_ingestion_jobs gives us a unified view of "what data work is running
    or has run" — surfaces in Diagnostics panel.

All additions are nullable / optional — existing code paths keep working
unchanged. Training pipeline will be updated in a later deploy to prefer
v2 labels when present.

Idempotent: skips columns that already exist, skips table creation if
ml_ingestion_jobs is already there.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd8e5f6a9b2c3'
down_revision: Union[str, None] = 'c1d4e5f6a789'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── Part 1: add v2 columns to ml_finding_labels ──────────────────
    if 'ml_finding_labels' in inspector.get_table_names():
        existing_cols = {c['name'] for c in inspector.get_columns('ml_finding_labels')}

        with op.batch_alter_table('ml_finding_labels') as batch_op:
            # Version tracking for which extractor/crawler produced this row.
            # Old rows have NULL; new rows get e.g. 'ai_parse_v2', 'zillow_v1'.
            if 'source_version' not in existing_cols:
                batch_op.add_column(sa.Column('source_version', sa.String(50), nullable=True))

            # Stream 3 re-labels: populated by Claude-based relabeling job
            if 'category_v2' not in existing_cols:
                batch_op.add_column(sa.Column('category_v2', sa.String(100), nullable=True))
            if 'severity_v2' not in existing_cols:
                batch_op.add_column(sa.Column('severity_v2', sa.String(50), nullable=True))

            # Flag from Claude-based quality check: is this actually an inspection
            # finding or is it boilerplate/metadata that shouldn't be in training?
            if 'is_real_finding' not in existing_cols:
                batch_op.add_column(sa.Column('is_real_finding', sa.Boolean, nullable=True))

            # Geographic + property context for diversity tracking
            if 'geographic_region' not in existing_cols:
                batch_op.add_column(sa.Column('geographic_region', sa.String(50), nullable=True))
            if 'property_age_bucket' not in existing_cols:
                batch_op.add_column(sa.Column('property_age_bucket', sa.String(30), nullable=True))

            # Metadata about the labeling process itself
            if 'labeling_confidence' not in existing_cols:
                batch_op.add_column(sa.Column('labeling_confidence', sa.Float, nullable=True))
            if 'labeling_notes' not in existing_cols:
                batch_op.add_column(sa.Column('labeling_notes', sa.Text, nullable=True))

    # ── Part 2: create ml_ingestion_jobs table ───────────────────────
    if 'ml_ingestion_jobs' not in inspector.get_table_names():
        op.create_table(
            'ml_ingestion_jobs',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
            sa.Column('started_at', sa.DateTime, nullable=True),
            sa.Column('completed_at', sa.DateTime, nullable=True),
            # Job classification
            sa.Column('job_type', sa.String(30), nullable=False),   # reextract|crawl|relabel
            sa.Column('source', sa.String(100), nullable=False),    # e.g. 'ai_parse_v2', 'zillow_v1', 'relabel_batch_1'
            sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
            # Stats
            sa.Column('rows_processed', sa.Integer, nullable=True),
            sa.Column('rows_added', sa.Integer, nullable=True),
            sa.Column('rows_rejected', sa.Integer, nullable=True),
            sa.Column('elapsed_seconds', sa.Float, nullable=True),
            # Config + error diagnostics (job's input params and any failure info)
            sa.Column('config_json', sa.Text, nullable=True),
            sa.Column('log_json', sa.Text, nullable=True),
            sa.Column('error', sa.Text, nullable=True),
        )
        op.create_index('ix_ml_ingestion_jobs_status', 'ml_ingestion_jobs', ['status'])
        op.create_index('ix_ml_ingestion_jobs_created_at', 'ml_ingestion_jobs', ['created_at'])


def downgrade() -> None:
    # Drop the whole table, then the columns. Downgrade is best-effort — we
    # don't strictly need it because we never downgrade production, but having
    # it means 'alembic downgrade' works in dev if someone needs it.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'ml_ingestion_jobs' in inspector.get_table_names():
        op.drop_index('ix_ml_ingestion_jobs_created_at', table_name='ml_ingestion_jobs')
        op.drop_index('ix_ml_ingestion_jobs_status', table_name='ml_ingestion_jobs')
        op.drop_table('ml_ingestion_jobs')

    if 'ml_finding_labels' in inspector.get_table_names():
        existing_cols = {c['name'] for c in inspector.get_columns('ml_finding_labels')}
        new_cols = ['source_version', 'category_v2', 'severity_v2', 'is_real_finding',
                    'geographic_region', 'property_age_bucket',
                    'labeling_confidence', 'labeling_notes']
        with op.batch_alter_table('ml_finding_labels') as batch_op:
            for col in new_cols:
                if col in existing_cols:
                    batch_op.drop_column(col)
