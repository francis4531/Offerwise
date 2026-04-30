"""v5_86_69_adopt_orphan_ml_and_agent_tables

Brings Alembic's migration history in sync with tables that were previously
created only by `db.create_all()` on app startup. Each table creation and
column addition is idempotent — if production already has the schema (which
it does, because db.create_all() has been creating these at boot), these
become no-ops that simply register the tables in Alembic's understanding.

This migration exists to pay down technical debt: the codebase was in a
hybrid state where Alembic knew about ~45 tables while db.create_all() was
creating ~14 more. That hybrid state was OK until v5.86.67 ran Alembic on
a fresh Render container restart, which left one table in an inconsistent
state (ml_finding_labels was missing original_category/original_severity)
and caused cascading psycopg2.errors.InFailedSqlTransaction errors on any
query to that table.

Tables covered:
  Orphan ML tables: ml_finding_labels, ml_contradiction_pairs,
    ml_cooccurrence_buckets, ml_cost_data, ml_agent_runs, ml_training_runs
  Orphan product tables: agent_alerts, agent_shares, agents,
    contractor_job_completions, feature_events, issue_confirmations,
    post_close_surveys, property_watches

Revision ID: c1d4e5f6a789
Revises: b8f2c9d3e4a1
Create Date: 2026-04-20 14:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c1d4e5f6a789'
down_revision: Union[str, None] = 'b8f2c9d3e4a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def batch_add_column(table_name, column):
    """Add a column to an existing table. Alembic's batch mode handles SQLite
    specifics; on PostgreSQL it's a straight ALTER TABLE ADD COLUMN."""
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ── agent_alerts ─────────────────────────────────────
    if 'agent_alerts' not in existing_tables:
        op.create_table('agent_alerts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('watch_id', sa.Integer(), nullable=False, index=True),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('alert_type', sa.String(length=50), nullable=False, index=True),
            sa.Column('severity', sa.String(length=20), nullable=True),
            sa.Column('title', sa.String(length=300), nullable=False),
            sa.Column('body', sa.Text(), nullable=True),
            sa.Column('detail_json', sa.Text(), nullable=True),
            sa.Column('email_sent', sa.Boolean(), nullable=True),
            sa.Column('email_sent_at', sa.DateTime(), nullable=True),
            sa.Column('read_at', sa.DateTime(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('agent_alerts')}
        if 'created_at' not in cols:
            batch_add_column('agent_alerts', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'watch_id' not in cols:
            batch_add_column('agent_alerts', sa.Column('watch_id', sa.Integer(), nullable=True))
        if 'user_id' not in cols:
            batch_add_column('agent_alerts', sa.Column('user_id', sa.Integer(), nullable=True))
        if 'alert_type' not in cols:
            batch_add_column('agent_alerts', sa.Column('alert_type', sa.String(length=50), nullable=True))
        if 'severity' not in cols:
            batch_add_column('agent_alerts', sa.Column('severity', sa.String(length=20), nullable=True))
        if 'title' not in cols:
            batch_add_column('agent_alerts', sa.Column('title', sa.String(length=300), nullable=True))
        if 'body' not in cols:
            batch_add_column('agent_alerts', sa.Column('body', sa.Text(), nullable=True))
        if 'detail_json' not in cols:
            batch_add_column('agent_alerts', sa.Column('detail_json', sa.Text(), nullable=True))
        if 'email_sent' not in cols:
            batch_add_column('agent_alerts', sa.Column('email_sent', sa.Boolean(), nullable=True))
        if 'email_sent_at' not in cols:
            batch_add_column('agent_alerts', sa.Column('email_sent_at', sa.DateTime(), nullable=True))
        if 'read_at' not in cols:
            batch_add_column('agent_alerts', sa.Column('read_at', sa.DateTime(), nullable=True))

    # ── agent_shares ─────────────────────────────────────
    if 'agent_shares' not in existing_tables:
        op.create_table('agent_shares',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('agent_id', sa.Integer(), nullable=False, index=True),
            sa.Column('agent_user_id', sa.Integer(), nullable=False),
            sa.Column('property_address', sa.String(length=500), nullable=True),
            sa.Column('property_price', sa.Float(), nullable=True),
            sa.Column('buyer_name', sa.String(length=255), nullable=True),
            sa.Column('buyer_email', sa.String(length=255), nullable=True),
            sa.Column('analysis_json', sa.Text(), nullable=True),
            sa.Column('share_token', sa.String(length=32), nullable=True, index=True, unique=True),
            sa.Column('agent_name_on_report', sa.String(length=255), nullable=True),
            sa.Column('agent_biz_on_report', sa.String(length=255), nullable=True),
            sa.Column('buyer_viewed_at', sa.DateTime(), nullable=True),
            sa.Column('buyer_registered', sa.Boolean(), nullable=True),
            sa.Column('buyer_converted', sa.Boolean(), nullable=True),
            sa.Column('view_count', sa.Integer(), nullable=True),
            sa.Column('has_text', sa.Boolean(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('agent_shares')}
        if 'created_at' not in cols:
            batch_add_column('agent_shares', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'agent_id' not in cols:
            batch_add_column('agent_shares', sa.Column('agent_id', sa.Integer(), nullable=True))
        if 'agent_user_id' not in cols:
            batch_add_column('agent_shares', sa.Column('agent_user_id', sa.Integer(), nullable=True))
        if 'property_address' not in cols:
            batch_add_column('agent_shares', sa.Column('property_address', sa.String(length=500), nullable=True))
        if 'property_price' not in cols:
            batch_add_column('agent_shares', sa.Column('property_price', sa.Float(), nullable=True))
        if 'buyer_name' not in cols:
            batch_add_column('agent_shares', sa.Column('buyer_name', sa.String(length=255), nullable=True))
        if 'buyer_email' not in cols:
            batch_add_column('agent_shares', sa.Column('buyer_email', sa.String(length=255), nullable=True))
        if 'analysis_json' not in cols:
            batch_add_column('agent_shares', sa.Column('analysis_json', sa.Text(), nullable=True))
        if 'share_token' not in cols:
            batch_add_column('agent_shares', sa.Column('share_token', sa.String(length=32), nullable=True))
        if 'agent_name_on_report' not in cols:
            batch_add_column('agent_shares', sa.Column('agent_name_on_report', sa.String(length=255), nullable=True))
        if 'agent_biz_on_report' not in cols:
            batch_add_column('agent_shares', sa.Column('agent_biz_on_report', sa.String(length=255), nullable=True))
        if 'buyer_viewed_at' not in cols:
            batch_add_column('agent_shares', sa.Column('buyer_viewed_at', sa.DateTime(), nullable=True))
        if 'buyer_registered' not in cols:
            batch_add_column('agent_shares', sa.Column('buyer_registered', sa.Boolean(), nullable=True))
        if 'buyer_converted' not in cols:
            batch_add_column('agent_shares', sa.Column('buyer_converted', sa.Boolean(), nullable=True))
        if 'view_count' not in cols:
            batch_add_column('agent_shares', sa.Column('view_count', sa.Integer(), nullable=True))
        if 'has_text' not in cols:
            batch_add_column('agent_shares', sa.Column('has_text', sa.Boolean(), nullable=True))

    # ── agents ─────────────────────────────────────
    if 'agents' not in existing_tables:
        op.create_table('agents',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True, unique=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('business_name', sa.String(length=255), nullable=True),
            sa.Column('agent_name', sa.String(length=255), nullable=True),
            sa.Column('license_number', sa.String(length=100), nullable=True),
            sa.Column('license_state', sa.String(length=2), nullable=True),
            sa.Column('phone', sa.String(length=50), nullable=True),
            sa.Column('website', sa.String(length=255), nullable=True),
            sa.Column('service_areas', sa.String(length=500), nullable=True),
            sa.Column('plan', sa.String(length=20), nullable=True),
            sa.Column('monthly_quota', sa.Integer(), nullable=True),
            sa.Column('monthly_used', sa.Integer(), nullable=True),
            sa.Column('quota_reset_at', sa.DateTime(), nullable=True),
            sa.Column('total_shares', sa.Integer(), nullable=True),
            sa.Column('total_buyers_converted', sa.Integer(), nullable=True),
            sa.Column('vanity_slug', sa.String(length=60), nullable=True, index=True, unique=True),
            sa.Column('photo_url', sa.String(length=500), nullable=True),
            sa.Column('is_verified', sa.Boolean(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('agents')}
        if 'user_id' not in cols:
            batch_add_column('agents', sa.Column('user_id', sa.Integer(), nullable=True))
        if 'created_at' not in cols:
            batch_add_column('agents', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'business_name' not in cols:
            batch_add_column('agents', sa.Column('business_name', sa.String(length=255), nullable=True))
        if 'agent_name' not in cols:
            batch_add_column('agents', sa.Column('agent_name', sa.String(length=255), nullable=True))
        if 'license_number' not in cols:
            batch_add_column('agents', sa.Column('license_number', sa.String(length=100), nullable=True))
        if 'license_state' not in cols:
            batch_add_column('agents', sa.Column('license_state', sa.String(length=2), nullable=True))
        if 'phone' not in cols:
            batch_add_column('agents', sa.Column('phone', sa.String(length=50), nullable=True))
        if 'website' not in cols:
            batch_add_column('agents', sa.Column('website', sa.String(length=255), nullable=True))
        if 'service_areas' not in cols:
            batch_add_column('agents', sa.Column('service_areas', sa.String(length=500), nullable=True))
        if 'plan' not in cols:
            batch_add_column('agents', sa.Column('plan', sa.String(length=20), nullable=True))
        if 'monthly_quota' not in cols:
            batch_add_column('agents', sa.Column('monthly_quota', sa.Integer(), nullable=True))
        if 'monthly_used' not in cols:
            batch_add_column('agents', sa.Column('monthly_used', sa.Integer(), nullable=True))
        if 'quota_reset_at' not in cols:
            batch_add_column('agents', sa.Column('quota_reset_at', sa.DateTime(), nullable=True))
        if 'total_shares' not in cols:
            batch_add_column('agents', sa.Column('total_shares', sa.Integer(), nullable=True))
        if 'total_buyers_converted' not in cols:
            batch_add_column('agents', sa.Column('total_buyers_converted', sa.Integer(), nullable=True))
        if 'vanity_slug' not in cols:
            batch_add_column('agents', sa.Column('vanity_slug', sa.String(length=60), nullable=True))
        if 'photo_url' not in cols:
            batch_add_column('agents', sa.Column('photo_url', sa.String(length=500), nullable=True))
        if 'is_verified' not in cols:
            batch_add_column('agents', sa.Column('is_verified', sa.Boolean(), nullable=True))
        if 'is_active' not in cols:
            batch_add_column('agents', sa.Column('is_active', sa.Boolean(), nullable=True))
        if 'notes' not in cols:
            batch_add_column('agents', sa.Column('notes', sa.Text(), nullable=True))

    # ── contractor_job_completions ─────────────────────────────────────
    if 'contractor_job_completions' not in existing_tables:
        op.create_table('contractor_job_completions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('lead_id', sa.Integer(), nullable=True, index=True),
            sa.Column('claim_id', sa.Integer(), nullable=True),
            sa.Column('contractor_id', sa.Integer(), nullable=False, index=True),
            sa.Column('property_address', sa.String(length=500), nullable=True),
            sa.Column('zip_code', sa.String(length=10), nullable=True, index=True),
            sa.Column('won_job', sa.Boolean(), nullable=False),
            sa.Column('final_price', sa.Float(), nullable=True),
            sa.Column('work_completed', sa.String(length=500), nullable=True),
            sa.Column('permit_uploaded', sa.Boolean(), nullable=True),
            sa.Column('permit_number', sa.String(length=100), nullable=True),
            sa.Column('original_estimate_low', sa.Float(), nullable=True),
            sa.Column('original_estimate_high', sa.Float(), nullable=True),
            sa.Column('variance_pct', sa.Float(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('contractor_job_completions')}
        if 'created_at' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'lead_id' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('lead_id', sa.Integer(), nullable=True))
        if 'claim_id' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('claim_id', sa.Integer(), nullable=True))
        if 'contractor_id' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('contractor_id', sa.Integer(), nullable=True))
        if 'property_address' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('property_address', sa.String(length=500), nullable=True))
        if 'zip_code' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('zip_code', sa.String(length=10), nullable=True))
        if 'won_job' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('won_job', sa.Boolean(), nullable=True))
        if 'final_price' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('final_price', sa.Float(), nullable=True))
        if 'work_completed' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('work_completed', sa.String(length=500), nullable=True))
        if 'permit_uploaded' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('permit_uploaded', sa.Boolean(), nullable=True))
        if 'permit_number' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('permit_number', sa.String(length=100), nullable=True))
        if 'original_estimate_low' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('original_estimate_low', sa.Float(), nullable=True))
        if 'original_estimate_high' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('original_estimate_high', sa.Float(), nullable=True))
        if 'variance_pct' not in cols:
            batch_add_column('contractor_job_completions', sa.Column('variance_pct', sa.Float(), nullable=True))

    # ── feature_events ─────────────────────────────────────
    if 'feature_events' not in existing_tables:
        op.create_table('feature_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('user_id', sa.Integer(), nullable=True, index=True),
            sa.Column('session_id', sa.String(length=64), nullable=True, index=True),
            sa.Column('feature', sa.String(length=80), nullable=False, index=True),
            sa.Column('action', sa.String(length=80), nullable=True),
            sa.Column('property_id', sa.Integer(), nullable=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True),
            sa.Column('meta', sa.Text(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('feature_events')}
        if 'created_at' not in cols:
            batch_add_column('feature_events', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'user_id' not in cols:
            batch_add_column('feature_events', sa.Column('user_id', sa.Integer(), nullable=True))
        if 'session_id' not in cols:
            batch_add_column('feature_events', sa.Column('session_id', sa.String(length=64), nullable=True))
        if 'feature' not in cols:
            batch_add_column('feature_events', sa.Column('feature', sa.String(length=80), nullable=True))
        if 'action' not in cols:
            batch_add_column('feature_events', sa.Column('action', sa.String(length=80), nullable=True))
        if 'property_id' not in cols:
            batch_add_column('feature_events', sa.Column('property_id', sa.Integer(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('feature_events', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'meta' not in cols:
            batch_add_column('feature_events', sa.Column('meta', sa.Text(), nullable=True))

    # ── issue_confirmations ─────────────────────────────────────
    if 'issue_confirmations' not in existing_tables:
        op.create_table('issue_confirmations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('user_id', sa.Integer(), nullable=True, index=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True, index=True),
            sa.Column('property_id', sa.Integer(), nullable=True),
            sa.Column('system', sa.String(length=80), nullable=False),
            sa.Column('severity', sa.String(length=20), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('verdict', sa.String(length=20), nullable=False),
            sa.Column('buyer_note', sa.Text(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('issue_confirmations')}
        if 'created_at' not in cols:
            batch_add_column('issue_confirmations', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'user_id' not in cols:
            batch_add_column('issue_confirmations', sa.Column('user_id', sa.Integer(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('issue_confirmations', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'property_id' not in cols:
            batch_add_column('issue_confirmations', sa.Column('property_id', sa.Integer(), nullable=True))
        if 'system' not in cols:
            batch_add_column('issue_confirmations', sa.Column('system', sa.String(length=80), nullable=True))
        if 'severity' not in cols:
            batch_add_column('issue_confirmations', sa.Column('severity', sa.String(length=20), nullable=True))
        if 'description' not in cols:
            batch_add_column('issue_confirmations', sa.Column('description', sa.Text(), nullable=True))
        if 'verdict' not in cols:
            batch_add_column('issue_confirmations', sa.Column('verdict', sa.String(length=20), nullable=True))
        if 'buyer_note' not in cols:
            batch_add_column('issue_confirmations', sa.Column('buyer_note', sa.Text(), nullable=True))

    # ── ml_agent_runs ─────────────────────────────────────
    if 'ml_agent_runs' not in existing_tables:
        op.create_table('ml_agent_runs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('elapsed_seconds', sa.Float(), nullable=True),
            sa.Column('trigger', sa.String(length=30), nullable=True),
            sa.Column('crawl_added', sa.Integer(), nullable=True),
            sa.Column('crawl_scanned', sa.Integer(), nullable=True),
            sa.Column('data_findings', sa.Integer(), nullable=True),
            sa.Column('data_pairs', sa.Integer(), nullable=True),
            sa.Column('data_costs', sa.Integer(), nullable=True),
            sa.Column('skipped_reason', sa.String(length=200), nullable=True),
            sa.Column('trained', sa.Boolean(), nullable=True),
            sa.Column('fc_acc', sa.Float(), nullable=True),
            sa.Column('cd_acc', sa.Float(), nullable=True),
            sa.Column('rc_r2', sa.Float(), nullable=True),
            sa.Column('rolled_back', sa.Boolean(), nullable=True),
            sa.Column('rollback_reason', sa.String(length=200), nullable=True),
            sa.Column('agent_log', sa.Text(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('ml_agent_runs')}
        if 'created_at' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'elapsed_seconds' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('elapsed_seconds', sa.Float(), nullable=True))
        if 'trigger' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('trigger', sa.String(length=30), nullable=True))
        if 'crawl_added' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('crawl_added', sa.Integer(), nullable=True))
        if 'crawl_scanned' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('crawl_scanned', sa.Integer(), nullable=True))
        if 'data_findings' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('data_findings', sa.Integer(), nullable=True))
        if 'data_pairs' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('data_pairs', sa.Integer(), nullable=True))
        if 'data_costs' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('data_costs', sa.Integer(), nullable=True))
        if 'skipped_reason' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('skipped_reason', sa.String(length=200), nullable=True))
        if 'trained' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('trained', sa.Boolean(), nullable=True))
        if 'fc_acc' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('fc_acc', sa.Float(), nullable=True))
        if 'cd_acc' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('cd_acc', sa.Float(), nullable=True))
        if 'rc_r2' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('rc_r2', sa.Float(), nullable=True))
        if 'rolled_back' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('rolled_back', sa.Boolean(), nullable=True))
        if 'rollback_reason' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('rollback_reason', sa.String(length=200), nullable=True))
        if 'agent_log' not in cols:
            batch_add_column('ml_agent_runs', sa.Column('agent_log', sa.Text(), nullable=True))

    # ── ml_contradiction_pairs ─────────────────────────────────────
    if 'ml_contradiction_pairs' not in existing_tables:
        op.create_table('ml_contradiction_pairs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('seller_claim', sa.Text(), nullable=False),
            sa.Column('inspector_finding', sa.Text(), nullable=False),
            sa.Column('label', sa.String(length=30), nullable=False, index=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True, index=True),
            sa.Column('source', sa.String(length=50), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('ml_contradiction_pairs')}
        if 'created_at' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'seller_claim' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('seller_claim', sa.Text(), nullable=True))
        if 'inspector_finding' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('inspector_finding', sa.Text(), nullable=True))
        if 'label' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('label', sa.String(length=30), nullable=True))
        if 'confidence' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('confidence', sa.Float(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'source' not in cols:
            batch_add_column('ml_contradiction_pairs', sa.Column('source', sa.String(length=50), nullable=True))

    # ── ml_cooccurrence_buckets ─────────────────────────────────────
    if 'ml_cooccurrence_buckets' not in existing_tables:
        op.create_table('ml_cooccurrence_buckets',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True, index=True, unique=True),
            sa.Column('findings_set', sa.Text(), nullable=False),
            sa.Column('n_findings', sa.Integer(), nullable=True),
            sa.Column('property_zip', sa.String(length=10), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('ml_cooccurrence_buckets')}
        if 'created_at' not in cols:
            batch_add_column('ml_cooccurrence_buckets', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('ml_cooccurrence_buckets', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'findings_set' not in cols:
            batch_add_column('ml_cooccurrence_buckets', sa.Column('findings_set', sa.Text(), nullable=True))
        if 'n_findings' not in cols:
            batch_add_column('ml_cooccurrence_buckets', sa.Column('n_findings', sa.Integer(), nullable=True))
        if 'property_zip' not in cols:
            batch_add_column('ml_cooccurrence_buckets', sa.Column('property_zip', sa.String(length=10), nullable=True))

    # ── ml_cost_data ─────────────────────────────────────
    if 'ml_cost_data' not in existing_tables:
        op.create_table('ml_cost_data',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('finding_text', sa.Text(), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=True, index=True),
            sa.Column('severity', sa.String(length=20), nullable=True, index=True),
            sa.Column('cost_low', sa.Float(), nullable=True),
            sa.Column('cost_high', sa.Float(), nullable=True),
            sa.Column('cost_mid', sa.Float(), nullable=False, index=True),
            sa.Column('zip_code', sa.String(length=10), nullable=True, index=True),
            sa.Column('source', sa.String(length=50), nullable=False, index=True),
            sa.Column('source_meta', sa.Text(), nullable=True),
            sa.Column('content_hash', sa.String(length=64), nullable=True, index=True, unique=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('ml_cost_data')}
        if 'created_at' not in cols:
            batch_add_column('ml_cost_data', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'finding_text' not in cols:
            batch_add_column('ml_cost_data', sa.Column('finding_text', sa.Text(), nullable=True))
        if 'category' not in cols:
            batch_add_column('ml_cost_data', sa.Column('category', sa.String(length=50), nullable=True))
        if 'severity' not in cols:
            batch_add_column('ml_cost_data', sa.Column('severity', sa.String(length=20), nullable=True))
        if 'cost_low' not in cols:
            batch_add_column('ml_cost_data', sa.Column('cost_low', sa.Float(), nullable=True))
        if 'cost_high' not in cols:
            batch_add_column('ml_cost_data', sa.Column('cost_high', sa.Float(), nullable=True))
        if 'cost_mid' not in cols:
            batch_add_column('ml_cost_data', sa.Column('cost_mid', sa.Float(), nullable=True))
        if 'zip_code' not in cols:
            batch_add_column('ml_cost_data', sa.Column('zip_code', sa.String(length=10), nullable=True))
        if 'source' not in cols:
            batch_add_column('ml_cost_data', sa.Column('source', sa.String(length=50), nullable=True))
        if 'source_meta' not in cols:
            batch_add_column('ml_cost_data', sa.Column('source_meta', sa.Text(), nullable=True))
        if 'content_hash' not in cols:
            batch_add_column('ml_cost_data', sa.Column('content_hash', sa.String(length=64), nullable=True))

    # ── ml_finding_labels ─────────────────────────────────────
    if 'ml_finding_labels' not in existing_tables:
        op.create_table('ml_finding_labels',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('finding_text', sa.Text(), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=False, index=True),
            sa.Column('severity', sa.String(length=50), nullable=False, index=True),
            sa.Column('source', sa.String(length=50), nullable=False, index=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('is_validated', sa.Boolean(), nullable=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True, index=True),
            sa.Column('report_id', sa.Integer(), nullable=True),
            sa.Column('property_zip', sa.String(length=10), nullable=True),
            sa.Column('property_price', sa.Float(), nullable=True),
            sa.Column('original_category', sa.String(length=100), nullable=True),
            sa.Column('original_severity', sa.String(length=50), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('ml_finding_labels')}
        if 'created_at' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'finding_text' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('finding_text', sa.Text(), nullable=True))
        if 'category' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('category', sa.String(length=100), nullable=True))
        if 'severity' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('severity', sa.String(length=50), nullable=True))
        if 'source' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('source', sa.String(length=50), nullable=True))
        if 'confidence' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('confidence', sa.Float(), nullable=True))
        if 'is_validated' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('is_validated', sa.Boolean(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'report_id' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('report_id', sa.Integer(), nullable=True))
        if 'property_zip' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('property_zip', sa.String(length=10), nullable=True))
        if 'property_price' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('property_price', sa.Float(), nullable=True))
        if 'original_category' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('original_category', sa.String(length=100), nullable=True))
        if 'original_severity' not in cols:
            batch_add_column('ml_finding_labels', sa.Column('original_severity', sa.String(length=50), nullable=True))

    # ── ml_training_runs ─────────────────────────────────────
    if 'ml_training_runs' not in existing_tables:
        op.create_table('ml_training_runs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('trigger', sa.String(length=30), nullable=True),
            sa.Column('elapsed_seconds', sa.Float(), nullable=True),
            sa.Column('fc_status', sa.String(length=20), nullable=True),
            sa.Column('fc_category_acc', sa.Float(), nullable=True),
            sa.Column('fc_severity_acc', sa.Float(), nullable=True),
            sa.Column('fc_data_points', sa.Integer(), nullable=True),
            sa.Column('fc_augmented', sa.Integer(), nullable=True),
            sa.Column('fc_error', sa.Text(), nullable=True),
            sa.Column('cd_status', sa.String(length=20), nullable=True),
            sa.Column('cd_accuracy', sa.Float(), nullable=True),
            sa.Column('cd_data_points', sa.Integer(), nullable=True),
            sa.Column('cd_error', sa.Text(), nullable=True),
            sa.Column('rc_status', sa.String(length=20), nullable=True),
            sa.Column('rc_r2', sa.Float(), nullable=True),
            sa.Column('rc_mae', sa.Float(), nullable=True),
            sa.Column('rc_median_pct', sa.Float(), nullable=True),
            sa.Column('rc_data_points', sa.Integer(), nullable=True),
            sa.Column('rc_error', sa.Text(), nullable=True),
            sa.Column('inference_tested', sa.Boolean(), nullable=True),
            sa.Column('inference_passed', sa.Integer(), nullable=True),
            sa.Column('inference_failed', sa.Integer(), nullable=True),
            sa.Column('inference_details', sa.Text(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('ml_training_runs')}
        if 'created_at' not in cols:
            batch_add_column('ml_training_runs', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'trigger' not in cols:
            batch_add_column('ml_training_runs', sa.Column('trigger', sa.String(length=30), nullable=True))
        if 'elapsed_seconds' not in cols:
            batch_add_column('ml_training_runs', sa.Column('elapsed_seconds', sa.Float(), nullable=True))
        if 'fc_status' not in cols:
            batch_add_column('ml_training_runs', sa.Column('fc_status', sa.String(length=20), nullable=True))
        if 'fc_category_acc' not in cols:
            batch_add_column('ml_training_runs', sa.Column('fc_category_acc', sa.Float(), nullable=True))
        if 'fc_severity_acc' not in cols:
            batch_add_column('ml_training_runs', sa.Column('fc_severity_acc', sa.Float(), nullable=True))
        if 'fc_data_points' not in cols:
            batch_add_column('ml_training_runs', sa.Column('fc_data_points', sa.Integer(), nullable=True))
        if 'fc_augmented' not in cols:
            batch_add_column('ml_training_runs', sa.Column('fc_augmented', sa.Integer(), nullable=True))
        if 'fc_error' not in cols:
            batch_add_column('ml_training_runs', sa.Column('fc_error', sa.Text(), nullable=True))
        if 'cd_status' not in cols:
            batch_add_column('ml_training_runs', sa.Column('cd_status', sa.String(length=20), nullable=True))
        if 'cd_accuracy' not in cols:
            batch_add_column('ml_training_runs', sa.Column('cd_accuracy', sa.Float(), nullable=True))
        if 'cd_data_points' not in cols:
            batch_add_column('ml_training_runs', sa.Column('cd_data_points', sa.Integer(), nullable=True))
        if 'cd_error' not in cols:
            batch_add_column('ml_training_runs', sa.Column('cd_error', sa.Text(), nullable=True))
        if 'rc_status' not in cols:
            batch_add_column('ml_training_runs', sa.Column('rc_status', sa.String(length=20), nullable=True))
        if 'rc_r2' not in cols:
            batch_add_column('ml_training_runs', sa.Column('rc_r2', sa.Float(), nullable=True))
        if 'rc_mae' not in cols:
            batch_add_column('ml_training_runs', sa.Column('rc_mae', sa.Float(), nullable=True))
        if 'rc_median_pct' not in cols:
            batch_add_column('ml_training_runs', sa.Column('rc_median_pct', sa.Float(), nullable=True))
        if 'rc_data_points' not in cols:
            batch_add_column('ml_training_runs', sa.Column('rc_data_points', sa.Integer(), nullable=True))
        if 'rc_error' not in cols:
            batch_add_column('ml_training_runs', sa.Column('rc_error', sa.Text(), nullable=True))
        if 'inference_tested' not in cols:
            batch_add_column('ml_training_runs', sa.Column('inference_tested', sa.Boolean(), nullable=True))
        if 'inference_passed' not in cols:
            batch_add_column('ml_training_runs', sa.Column('inference_passed', sa.Integer(), nullable=True))
        if 'inference_failed' not in cols:
            batch_add_column('ml_training_runs', sa.Column('inference_failed', sa.Integer(), nullable=True))
        if 'inference_details' not in cols:
            batch_add_column('ml_training_runs', sa.Column('inference_details', sa.Text(), nullable=True))

    # ── post_close_surveys ─────────────────────────────────────
    if 'post_close_surveys' not in existing_tables:
        op.create_table('post_close_surveys',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
            sa.Column('token', sa.String(length=64), nullable=False, index=True, unique=True),
            sa.Column('user_id', sa.Integer(), nullable=True, index=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True, index=True),
            sa.Column('property_address', sa.String(length=500), nullable=True),
            sa.Column('status', sa.String(length=30), nullable=True),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('did_buy', sa.String(length=30), nullable=True),
            sa.Column('final_price', sa.Float(), nullable=True),
            sa.Column('repairs_needed', sa.Text(), nullable=True),
            sa.Column('repair_cost_range', sa.String(length=30), nullable=True),
            sa.Column('surprises_text', sa.Text(), nullable=True),
            sa.Column('accuracy_rating', sa.Integer(), nullable=True),
            sa.Column('predicted_offer_low', sa.Float(), nullable=True),
            sa.Column('predicted_offer_high', sa.Float(), nullable=True),
            sa.Column('predicted_repair_total', sa.Float(), nullable=True),
            sa.Column('predicted_findings_count', sa.Integer(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('post_close_surveys')}
        if 'created_at' not in cols:
            batch_add_column('post_close_surveys', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'token' not in cols:
            batch_add_column('post_close_surveys', sa.Column('token', sa.String(length=64), nullable=True))
        if 'user_id' not in cols:
            batch_add_column('post_close_surveys', sa.Column('user_id', sa.Integer(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('post_close_surveys', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'property_address' not in cols:
            batch_add_column('post_close_surveys', sa.Column('property_address', sa.String(length=500), nullable=True))
        if 'status' not in cols:
            batch_add_column('post_close_surveys', sa.Column('status', sa.String(length=30), nullable=True))
        if 'sent_at' not in cols:
            batch_add_column('post_close_surveys', sa.Column('sent_at', sa.DateTime(), nullable=True))
        if 'completed_at' not in cols:
            batch_add_column('post_close_surveys', sa.Column('completed_at', sa.DateTime(), nullable=True))
        if 'did_buy' not in cols:
            batch_add_column('post_close_surveys', sa.Column('did_buy', sa.String(length=30), nullable=True))
        if 'final_price' not in cols:
            batch_add_column('post_close_surveys', sa.Column('final_price', sa.Float(), nullable=True))
        if 'repairs_needed' not in cols:
            batch_add_column('post_close_surveys', sa.Column('repairs_needed', sa.Text(), nullable=True))
        if 'repair_cost_range' not in cols:
            batch_add_column('post_close_surveys', sa.Column('repair_cost_range', sa.String(length=30), nullable=True))
        if 'surprises_text' not in cols:
            batch_add_column('post_close_surveys', sa.Column('surprises_text', sa.Text(), nullable=True))
        if 'accuracy_rating' not in cols:
            batch_add_column('post_close_surveys', sa.Column('accuracy_rating', sa.Integer(), nullable=True))
        if 'predicted_offer_low' not in cols:
            batch_add_column('post_close_surveys', sa.Column('predicted_offer_low', sa.Float(), nullable=True))
        if 'predicted_offer_high' not in cols:
            batch_add_column('post_close_surveys', sa.Column('predicted_offer_high', sa.Float(), nullable=True))
        if 'predicted_repair_total' not in cols:
            batch_add_column('post_close_surveys', sa.Column('predicted_repair_total', sa.Float(), nullable=True))
        if 'predicted_findings_count' not in cols:
            batch_add_column('post_close_surveys', sa.Column('predicted_findings_count', sa.Integer(), nullable=True))

    # ── property_watches ─────────────────────────────────────
    if 'property_watches' not in existing_tables:
        op.create_table('property_watches',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('analysis_id', sa.Integer(), nullable=True),
            sa.Column('address', sa.String(length=500), nullable=False),
            sa.Column('latitude', sa.Float(), nullable=True),
            sa.Column('longitude', sa.Float(), nullable=True),
            sa.Column('asking_price', sa.Float(), nullable=True),
            sa.Column('avm_at_analysis', sa.Float(), nullable=True),
            sa.Column('baseline_comps_json', sa.Text(), nullable=True),
            sa.Column('baseline_permits_json', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True, index=True),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('deactivated_reason', sa.String(length=100), nullable=True),
            sa.Column('inspector_report_id', sa.Integer(), nullable=True),
            sa.Column('agent_share_id', sa.Integer(), nullable=True),
            sa.Column('contractor_lead_id', sa.Integer(), nullable=True),
            sa.Column('ghost_buyer_email', sa.String(length=255), nullable=True, index=True),
            sa.Column('owned_by_professional', sa.Boolean(), nullable=True),
            sa.Column('last_comps_check_at', sa.DateTime(), nullable=True),
            sa.Column('last_permit_check_at', sa.DateTime(), nullable=True),
            sa.Column('last_earthquake_check_at', sa.DateTime(), nullable=True),
            sa.Column('last_price_check_at', sa.DateTime(), nullable=True),
            sa.Column('last_deadline_check_at', sa.DateTime(), nullable=True),
            sa.Column('offer_accepted_date', sa.Date(), nullable=True),
            sa.Column('inspection_contingency_date', sa.Date(), nullable=True),
            sa.Column('loan_contingency_date', sa.Date(), nullable=True),
            sa.Column('appraisal_contingency_date', sa.Date(), nullable=True),
            sa.Column('seller_response_deadline', sa.Date(), nullable=True),
            sa.Column('repair_completion_deadline', sa.Date(), nullable=True),
            sa.Column('close_of_escrow_date', sa.Date(), nullable=True),
        )
    else:
        # Table exists — make sure every column exists
        cols = {c['name'] for c in inspector.get_columns('property_watches')}
        if 'created_at' not in cols:
            batch_add_column('property_watches', sa.Column('created_at', sa.DateTime(), nullable=True))
        if 'updated_at' not in cols:
            batch_add_column('property_watches', sa.Column('updated_at', sa.DateTime(), nullable=True))
        if 'user_id' not in cols:
            batch_add_column('property_watches', sa.Column('user_id', sa.Integer(), nullable=True))
        if 'analysis_id' not in cols:
            batch_add_column('property_watches', sa.Column('analysis_id', sa.Integer(), nullable=True))
        if 'address' not in cols:
            batch_add_column('property_watches', sa.Column('address', sa.String(length=500), nullable=True))
        if 'latitude' not in cols:
            batch_add_column('property_watches', sa.Column('latitude', sa.Float(), nullable=True))
        if 'longitude' not in cols:
            batch_add_column('property_watches', sa.Column('longitude', sa.Float(), nullable=True))
        if 'asking_price' not in cols:
            batch_add_column('property_watches', sa.Column('asking_price', sa.Float(), nullable=True))
        if 'avm_at_analysis' not in cols:
            batch_add_column('property_watches', sa.Column('avm_at_analysis', sa.Float(), nullable=True))
        if 'baseline_comps_json' not in cols:
            batch_add_column('property_watches', sa.Column('baseline_comps_json', sa.Text(), nullable=True))
        if 'baseline_permits_json' not in cols:
            batch_add_column('property_watches', sa.Column('baseline_permits_json', sa.Text(), nullable=True))
        if 'is_active' not in cols:
            batch_add_column('property_watches', sa.Column('is_active', sa.Boolean(), nullable=True))
        if 'expires_at' not in cols:
            batch_add_column('property_watches', sa.Column('expires_at', sa.DateTime(), nullable=True))
        if 'deactivated_reason' not in cols:
            batch_add_column('property_watches', sa.Column('deactivated_reason', sa.String(length=100), nullable=True))
        if 'inspector_report_id' not in cols:
            batch_add_column('property_watches', sa.Column('inspector_report_id', sa.Integer(), nullable=True))
        if 'agent_share_id' not in cols:
            batch_add_column('property_watches', sa.Column('agent_share_id', sa.Integer(), nullable=True))
        if 'contractor_lead_id' not in cols:
            batch_add_column('property_watches', sa.Column('contractor_lead_id', sa.Integer(), nullable=True))
        if 'ghost_buyer_email' not in cols:
            batch_add_column('property_watches', sa.Column('ghost_buyer_email', sa.String(length=255), nullable=True))
        if 'owned_by_professional' not in cols:
            batch_add_column('property_watches', sa.Column('owned_by_professional', sa.Boolean(), nullable=True))
        if 'last_comps_check_at' not in cols:
            batch_add_column('property_watches', sa.Column('last_comps_check_at', sa.DateTime(), nullable=True))
        if 'last_permit_check_at' not in cols:
            batch_add_column('property_watches', sa.Column('last_permit_check_at', sa.DateTime(), nullable=True))
        if 'last_earthquake_check_at' not in cols:
            batch_add_column('property_watches', sa.Column('last_earthquake_check_at', sa.DateTime(), nullable=True))
        if 'last_price_check_at' not in cols:
            batch_add_column('property_watches', sa.Column('last_price_check_at', sa.DateTime(), nullable=True))
        if 'last_deadline_check_at' not in cols:
            batch_add_column('property_watches', sa.Column('last_deadline_check_at', sa.DateTime(), nullable=True))
        if 'offer_accepted_date' not in cols:
            batch_add_column('property_watches', sa.Column('offer_accepted_date', sa.Date(), nullable=True))
        if 'inspection_contingency_date' not in cols:
            batch_add_column('property_watches', sa.Column('inspection_contingency_date', sa.Date(), nullable=True))
        if 'loan_contingency_date' not in cols:
            batch_add_column('property_watches', sa.Column('loan_contingency_date', sa.Date(), nullable=True))
        if 'appraisal_contingency_date' not in cols:
            batch_add_column('property_watches', sa.Column('appraisal_contingency_date', sa.Date(), nullable=True))
        if 'seller_response_deadline' not in cols:
            batch_add_column('property_watches', sa.Column('seller_response_deadline', sa.Date(), nullable=True))
        if 'repair_completion_deadline' not in cols:
            batch_add_column('property_watches', sa.Column('repair_completion_deadline', sa.Date(), nullable=True))
        if 'close_of_escrow_date' not in cols:
            batch_add_column('property_watches', sa.Column('close_of_escrow_date', sa.Date(), nullable=True))

def downgrade() -> None:
    # Intentionally no-op: we don't drop tables that were created via
    # db.create_all() before this migration existed. Dropping them would
    # destroy production data. If you need to roll back this migration's
    # tracking, alembic stamp the previous revision.
    pass
