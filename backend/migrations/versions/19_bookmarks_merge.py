"""Merge the bookmark migration with the newer relationship-intake head."""

from typing import Sequence, Union

from alembic import op


revision: str = "19bookmarkmerge01"
down_revision: Union[str, Sequence[str], None] = (
    "19bookmarks01",
    "25relintake02",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
