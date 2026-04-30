"""Add contractor marketplace tables and columns."""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Add new columns to contractor_leads
    with op.batch_alter_table('contractor_leads') as batch_op:
        batch_op.add_column(sa.Column('available_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('claim_count', sa.Integer(), nullable=True, server_default='0'))

    # Add new columns to contractors
    with op.batch_alter_table('contractors') as batch_op:
        batch_op.add_column(sa.Column('available', sa.Boolean(), nullable=True, server_default='true'))
        batch_op.add_column(sa.Column('unavailable_until', sa.DateTime(), nullable=True))

    # Create contractor_lead_claims table
    op.create_table('contractor_lead_claims',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('contractor_leads.id'), nullable=False),
        sa.Column('contractor_id', sa.Integer(), sa.ForeignKey('contractors.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=True, server_default='claimed'),
        sa.Column('passed_at', sa.DateTime(), nullable=True),
        sa.Column('contacted_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('job_value', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lead_id', 'contractor_id', name='unique_lead_contractor'),
    )
    op.create_index('ix_contractor_lead_claims_lead_id', 'contractor_lead_claims', ['lead_id'])
    op.create_index('ix_contractor_lead_claims_contractor_id', 'contractor_lead_claims', ['contractor_id'])
    op.create_index('ix_contractor_lead_claims_created_at', 'contractor_lead_claims', ['created_at'])

def downgrade():
    op.drop_table('contractor_lead_claims')
    with op.batch_alter_table('contractors') as batch_op:
        batch_op.drop_column('available')
        batch_op.drop_column('unavailable_until')
    with op.batch_alter_table('contractor_leads') as batch_op:
        batch_op.drop_column('available_at')
        batch_op.drop_column('expires_at')
        batch_op.drop_column('claim_count')
