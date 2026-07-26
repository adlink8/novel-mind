"""Add intake_kind provenance column to relationship_observations.

Distinguishes producer lineage for accepted relationship facts:
llm_judgment / timeline_seed_backfill / cooccurrence_candidate / manual /
unknown. Existing rows default to 'unknown' (honest: provenance was not
recorded before Phase 25-02).

Revision ID: 25relintake01
Revises: 18appsetting1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "25relintake01"
down_revision: Union[str, Sequence[str], None] = "18appsetting1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INTAKE_KINDS_SQL = (
    "intake_kind IN ('llm_judgment','timeline_seed_backfill',"
    "'cooccurrence_candidate','manual','unknown')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "relationship_observations",
        sa.Column(
            "intake_kind",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        "idx_rel_observations_intake_kind",
        "relationship_observations",
        ["intake_kind"],
    )
    op.create_check_constraint(
        "ck_rel_observations_intake_kind",
        "relationship_observations",
        INTAKE_KINDS_SQL,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_rel_observations_intake_kind",
        "relationship_observations",
        type_="check",
    )
    op.drop_index(
        "idx_rel_observations_intake_kind",
        table_name="relationship_observations",
    )
    op.drop_column("relationship_observations", "intake_kind")
