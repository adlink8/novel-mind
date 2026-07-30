"""Allow owner-scoped clue evidence in frozen Reader Chat manifests."""

from typing import Sequence, Union

from alembic import op


revision: str = "21readerclue01"
down_revision: Union[str, Sequence[str], None] = "20fullanalysis01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_reader_evidence_source_type",
        "reader_context_evidence_refs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_reader_evidence_source_type",
        "reader_context_evidence_refs",
        "source_type IN ('selection','hierarchy','timeline','knowledge',"
        "'relationship_observation','clue_evidence')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_reader_evidence_source_type",
        "reader_context_evidence_refs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_reader_evidence_source_type",
        "reader_context_evidence_refs",
        "source_type IN ('selection','hierarchy','timeline','knowledge',"
        "'relationship_observation')",
    )

