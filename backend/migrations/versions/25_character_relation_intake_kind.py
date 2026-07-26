"""Add intake_kind provenance column to legacy character_relations.

The legacy display table is written by the knowledge projection service; the
same intake vocabulary applies (llm_judgment / timeline_seed_backfill /
cooccurrence_candidate / manual / unknown). Existing rows default to
'unknown'.

Revision ID: 25relintake02
Revises: 25relintake01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "25relintake02"
down_revision: Union[str, Sequence[str], None] = "25relintake01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Idempotent guards mirror 25relintake01: safe on databases where the column
    already exists via ORM-metadata table creation.
    """
    insp = sa.inspect(op.get_bind())
    columns = {c["name"] for c in insp.get_columns("character_relations")}
    if "intake_kind" not in columns:
        op.add_column(
            "character_relations",
            sa.Column(
                "intake_kind",
                sa.String(length=32),
                nullable=False,
                server_default="unknown",
            ),
        )
    indexes = {i["name"] for i in insp.get_indexes("character_relations")}
    if "ix_character_relations_intake_kind" not in indexes:
        op.create_index(
            "ix_character_relations_intake_kind",
            "character_relations",
            ["intake_kind"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_character_relations_intake_kind",
        table_name="character_relations",
    )
    op.drop_column("character_relations", "intake_kind")
