"""merge_heads

Revision ID: 9c7f25bb06e8
Revises: 3ef12024378b, a7e2c8f1d45b
Create Date: 2026-06-12 23:37:06.427181

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c7f25bb06e8'
down_revision: Union[str, Sequence[str], None] = ('3ef12024378b', 'a7e2c8f1d45b')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
