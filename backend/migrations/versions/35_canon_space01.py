"""Phase 35-01: triple knowledge-space contract boundaries (REQ-FORK-01/CRE-01).

Extends the existing ``canon_space_artifacts`` table (31_canon_space_artifacts)
with the D-35-01/D-35-03 frozen lineage and the D-35-02 Original read-only
boundary:

- ``source_snapshot_hash``: immutable source snapshot the artifact is bound to
  (64-hex, enforced).
- ``through_chapter`` / ``full_book_authorized``: the server-derived spoiler
  cutoff; a mutable active row never replaces a version.
- ``read_only``: Original Canon marker. The check constraint
  ``(space = 'original_canon') = (read_only = TRUE)`` binds read-only to the
  Original space in both directions.
- composite lineage index for branch-aware retrieval.

Existing rows (Phase 31 contract fixtures) receive the legacy sentinel snapshot
hash and a space-consistent ``read_only`` backfill so the migration is
reversible and never deletes data. New rows must carry real lineage through the
contract layer.

Revision ID: 20260801_canon_space01
Revises: 20260801_illustration_anchors
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_canon_space01"
down_revision = "20260801_illustration_anchors"
branch_labels = None
depends_on = None

TABLE = "canon_space_artifacts"
LEGACY_SNAPSHOT_SENTINEL = "0" * 64


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(col["name"] == column for col in insp.get_columns(table))


def _has_check(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(ck["name"] == name for ck in insp.get_check_constraints(table))


def _has_index(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {idx["name"] for idx in insp.get_indexes(TABLE)}


def _create_check(name: str, sqltext: str) -> None:
    if not _has_check(TABLE, name):
        op.create_check_constraint(name, TABLE, sqltext)


def upgrade() -> None:
    """Upgrade schema: frozen lineage + read-only marker (idempotent guards)."""
    if not _has_table(TABLE):
        return

    if not _has_column(TABLE, "source_snapshot_hash"):
        op.add_column(
            TABLE,
            sa.Column(
                "source_snapshot_hash",
                sa.String(length=64),
                nullable=False,
                server_default=LEGACY_SNAPSHOT_SENTINEL,
            ),
        )
    if not _has_column(TABLE, "through_chapter"):
        op.add_column(
            TABLE,
            sa.Column("through_chapter", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column(TABLE, "full_book_authorized"):
        op.add_column(
            TABLE,
            sa.Column(
                "full_book_authorized",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if not _has_column(TABLE, "read_only"):
        op.add_column(
            TABLE,
            sa.Column(
                "read_only", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )
        # Backfill: the marker is bound to the Original Canon space so the
        # bidirectional check below holds for pre-existing rows.
        op.execute(
            sa.text(
                "UPDATE canon_space_artifacts "
                "SET read_only = (space = 'original_canon')"
            )
        )

    _create_check(
        "ck_canon_space_artifact_snapshot_hash",
        "length(source_snapshot_hash) = 64",
    )
    _create_check("ck_canon_space_artifact_cutoff", "through_chapter > 0")
    _create_check(
        "ck_canon_space_artifact_readonly",
        "(space = 'original_canon') = (read_only = TRUE)",
    )

    if not _has_index("ix_canon_space_artifacts_lineage"):
        op.create_index(
            "ix_canon_space_artifacts_lineage",
            TABLE,
            ["space", "namespace", "version_key"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema: drop lineage columns/constraints symmetrically."""
    if not _has_table(TABLE):
        return

    if _has_index("ix_canon_space_artifacts_lineage"):
        op.drop_index("ix_canon_space_artifacts_lineage", table_name=TABLE)

    for name in (
        "ck_canon_space_artifact_readonly",
        "ck_canon_space_artifact_cutoff",
        "ck_canon_space_artifact_snapshot_hash",
    ):
        if _has_check(TABLE, name):
            op.drop_constraint(name, TABLE, type_="check")

    for column in ("read_only", "full_book_authorized", "through_chapter", "source_snapshot_hash"):
        if _has_column(TABLE, column):
            op.drop_column(TABLE, column)
