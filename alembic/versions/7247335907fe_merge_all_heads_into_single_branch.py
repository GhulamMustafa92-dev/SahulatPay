"""merge all heads into single branch

Revision ID: 7247335907fe
Revises: f1e2d3c4b5a6, h2i3j4k5l6m7
Create Date: 2026-04-23 22:03:56.134276

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7247335907fe'
down_revision: Union[str, None] = ('f1e2d3c4b5a6', 'h2i3j4k5l6m7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
