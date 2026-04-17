"""add card_subscriptions table

Revision ID: d4e1b9c2f8a6
Revises: c3f7a8b2d1e5
Create Date: 2026-04-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'd4e1b9c2f8a6'
down_revision = 'c3f7a8b2d1e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'card_subscriptions',
        sa.Column('id',            UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('card_id',       UUID(as_uuid=True), sa.ForeignKey('virtual_cards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id',       UUID(as_uuid=True), sa.ForeignKey('users.id',         ondelete='CASCADE'), nullable=False),
        sa.Column('service_name',  sa.String(100), nullable=False),
        sa.Column('service_code',  sa.String(50),  nullable=False),
        sa.Column('amount',        sa.Numeric(12, 2), nullable=False),
        sa.Column('billing_cycle', sa.String(20), server_default='monthly'),
        sa.Column('renewal_date',  sa.Date, nullable=False),
        sa.Column('is_active',     sa.Boolean, server_default='true'),
        sa.Column('created_at',    sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at',    sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_index('idx_card_subscriptions_card',    'card_subscriptions', ['card_id'])
    op.create_index('idx_card_subscriptions_renewal', 'card_subscriptions', ['renewal_date', 'is_active'])


def downgrade() -> None:
    op.drop_index('idx_card_subscriptions_renewal', 'card_subscriptions')
    op.drop_index('idx_card_subscriptions_card',    'card_subscriptions')
    op.drop_table('card_subscriptions')
