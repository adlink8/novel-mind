"""Add durable full-analysis orchestration runs."""

from typing import Sequence, Union

from alembic import op


revision: str = "20fullanalysis01"
down_revision: Union[str, Sequence[str], None] = "26readerimages01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.models import Base

    Base.metadata.tables["full_analysis_runs"].create(
        bind=op.get_bind(), checkfirst=True
    )


def downgrade() -> None:
    from app.models import Base

    Base.metadata.tables["full_analysis_runs"].drop(
        bind=op.get_bind(), checkfirst=True
    )
