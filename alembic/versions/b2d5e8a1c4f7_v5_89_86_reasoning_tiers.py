"""v5_89_86_reasoning_tiers

Revision ID: b2d5e8a1c4f7
Revises: a1c4e7f9b2d6
Create Date: 2026-05-29 00:00:00.000000

Phase 0b: persist the reasoning tiers — Finding / Claim / Issue — plus the two
association tables (claim_findings, issue_claims) modeling the architecture's
many-to-many relationships.

ADDITIVE: creates new tables only. No ALTER of existing tables. Safe alongside
db.create_all() (which also creates these from the SQLAlchemy models on boot) —
the IF-NOT-EXISTS inspector guard makes apply order irrelevant.

Schema notes:
- Integer PKs and FKs to analyses.id / properties.id / documents.id, matching
  the existing convention. FKs are nullable so a finding/claim/issue can be
  written before its parent linkage is known.
- Claim keys on checklist_item_id (+ checklist_version) — the missing middle
  term the runtime engines will later pivot onto.
- Claim carries DUAL confidence (inference_confidence, evidence_quality_confidence)
  per Commitment 2.3.
- Issue keys on decision_class (the Q-5.4 decision axis) with is_reserve and
  silent_hazard_flag, feeding the two-output offer handoff.

Idempotent: uses inspector guards; safe on dbs where db.create_all() already
produced the tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2d5e8a1c4f7'
down_revision: Union[str, None] = 'a1c4e7f9b2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if 'reasoning_findings' not in existing:
        op.create_table(
            'reasoning_findings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('analysis_id', sa.Integer(), sa.ForeignKey('analyses.id'), nullable=True, index=True),
            sa.Column('property_id', sa.Integer(), sa.ForeignKey('properties.id'), nullable=True, index=True),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('documents.id'), nullable=True, index=True),
            sa.Column('source_document', sa.String(50)),
            sa.Column('source_page', sa.Integer(), nullable=True),
            sa.Column('source_quote', sa.Text(), nullable=True),
            sa.Column('raw_text', sa.Text()),
            sa.Column('modality', sa.String(40), nullable=True),
            sa.Column('legacy_category', sa.String(50), nullable=True),
            sa.Column('severity', sa.String(20), nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if 'reasoning_claims' not in existing:
        op.create_table(
            'reasoning_claims',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('analysis_id', sa.Integer(), sa.ForeignKey('analyses.id'), nullable=True, index=True),
            sa.Column('property_id', sa.Integer(), sa.ForeignKey('properties.id'), nullable=True, index=True),
            sa.Column('checklist_item_id', sa.String(120), nullable=False, index=True),
            sa.Column('checklist_version', sa.String(20)),
            sa.Column('resolved_value', sa.Text(), nullable=True),
            sa.Column('resolution_state', sa.String(20), server_default='answered'),
            sa.Column('polarity', sa.String(20), nullable=True),
            sa.Column('inference_confidence', sa.Float(), nullable=True),
            sa.Column('evidence_quality_confidence', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if 'reasoning_issues' not in existing:
        op.create_table(
            'reasoning_issues',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('analysis_id', sa.Integer(), sa.ForeignKey('analyses.id'), nullable=True, index=True),
            sa.Column('property_id', sa.Integer(), sa.ForeignKey('properties.id'), nullable=True, index=True),
            sa.Column('decision_class', sa.String(40), nullable=False, index=True),
            sa.Column('silent_hazard_flag', sa.Boolean(), server_default=sa.false()),
            sa.Column('severity', sa.String(20)),
            sa.Column('cost_band_low', sa.Float(), nullable=True),
            sa.Column('cost_band_high', sa.Float(), nullable=True),
            sa.Column('is_reserve', sa.Boolean(), server_default=sa.false()),
            sa.Column('title', sa.String(300)),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if 'claim_findings' not in existing:
        op.create_table(
            'claim_findings',
            sa.Column('claim_id', sa.Integer(), sa.ForeignKey('reasoning_claims.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('finding_id', sa.Integer(), sa.ForeignKey('reasoning_findings.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('role', sa.String(20), nullable=False, server_default='supporting'),
        )

    if 'issue_claims' not in existing:
        op.create_table(
            'issue_claims',
            sa.Column('issue_id', sa.Integer(), sa.ForeignKey('reasoning_issues.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('claim_id', sa.Integer(), sa.ForeignKey('reasoning_claims.id', ondelete='CASCADE'), primary_key=True),
        )


def downgrade() -> None:
    # Drop in FK-safe order: association tables first, then tiers.
    for table in ('issue_claims', 'claim_findings', 'reasoning_issues',
                  'reasoning_claims', 'reasoning_findings'):
        op.drop_table(table)
