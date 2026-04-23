"""insurance auto_deduct and policy_number columns

Revision ID: a1b2c3d4e5f6
Revises: d4e5f6a7b8c9
Create Date: 2025-04-23 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('insurance_policies', sa.Column('policy_number',       sa.String(50),                  nullable=True))
    op.add_column('insurance_policies', sa.Column('auto_deduct_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('insurance_policies', sa.Column('auto_deduct_freq',    sa.String(20), server_default='monthly', nullable=False))
    op.add_column('insurance_policies', sa.Column('next_deduction_at',   sa.DateTime(timezone=True),     nullable=True))


def downgrade() -> None:
    op.drop_column('insurance_policies', 'next_deduction_at')
    op.drop_column('insurance_policies', 'auto_deduct_freq')
    op.drop_column('insurance_policies', 'auto_deduct_enabled')
    op.drop_column('insurance_policies', 'policy_number')
