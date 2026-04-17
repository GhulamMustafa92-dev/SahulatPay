"""add card_number_encrypted, cvv_encrypted, delivery_status to virtual_cards

Revision ID: c3f7a8b2d1e5
Revises: a7745e2c81ea
Create Date: 2026-04-17

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3f7a8b2d1e5'
down_revision = 'a7745e2c81ea'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('virtual_cards', sa.Column('card_number_encrypted', sa.Text(), nullable=True))
    op.add_column('virtual_cards', sa.Column('cvv_encrypted',         sa.Text(), nullable=True))
    op.add_column('virtual_cards', sa.Column('delivery_status',       sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('virtual_cards', 'delivery_status')
    op.drop_column('virtual_cards', 'cvv_encrypted')
    op.drop_column('virtual_cards', 'card_number_encrypted')
