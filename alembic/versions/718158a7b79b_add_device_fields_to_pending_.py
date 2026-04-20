"""add_device_fields_to_pending_registrations

Revision ID: 718158a7b79b
Revises: 1f6943fab365
Create Date: 2026-04-20 12:03:09.182731

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '718158a7b79b'
down_revision: Union[str, None] = '1f6943fab365'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pending_registrations', sa.Column('device_fingerprint', sa.String(length=255), nullable=True))
    op.add_column('pending_registrations', sa.Column('device_name', sa.String(length=255), nullable=True))
    op.add_column('pending_registrations', sa.Column('device_os', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('pending_registrations', 'device_os')
    op.drop_column('pending_registrations', 'device_name')
    op.drop_column('pending_registrations', 'device_fingerprint')
