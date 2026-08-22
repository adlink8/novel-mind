"""Phase 35-04: durable contamination block audit (REQ-CRE-02 / D-35-02).

Creates the ``canon_contamination_blocks`` audit table that backs the shared
derivative-write guard adapter (``canon_fork/contamination.py``).  Every
blocked derivative write into an Original pipeline is recorded *after* the
contaminated transaction has rolled back, so the blocked reason survives even
though the write never landed.  Real PostgreSQL constraints:

- composite unique ``(owner_id, novel_id, space, pipeline, attempt_hash)`` —
  an identical repeated blocked attempt is idempotent;
- FK to ``users`` / ``novels`` — the attempt must resolve to a real owner/novel;
- check constraints pin ``space`` to the derivative vocabulary, ``pipeline`` to
  the Original consumer pipelines and require a non-empty ``blocked_reason``.

Revision ID: 20260801_canon_contamination04
Revises: 20260801_canon_fork01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_canon_contamination04"
down_revision = "20260801_canon_fork01"
branch_labels = None
depends_on = None

TABLE = "canon_contamination_blocks"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the contamination block audit table (idempotent guard)."""
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("space", sa.String(length=32), nullable=False),
        sa.Column("pipeline", sa.String(length=64), nullable=False),
        sa.Column("blocked_reason", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("scope_hash", sa.String(length=64), nullable=True),
        sa.Column("attempt_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "owner_id",
            "novel_id",
            "space",
            "pipeline",
            "attempt_hash",
            name="uq_canon_contamination_block_attempt",
        ),
        sa.CheckConstraint(
            "space IN ('user_interpretation','fanfiction_canon')",
            name="ck_canon_contamination_block_space",
        ),
        sa.CheckConstraint(
            "pipeline IN ('original_analysis','original_retrieval','facet',"
            "'evaluation','candidate_builder')",
            name="ck_canon_contamination_block_pipeline",
        ),
        sa.CheckConstraint(
            "length(blocked_reason) > 0",
            name="ck_canon_contamination_block_reason",
        ),
    )
    op.create_index(
        "ix_canon_contamination_blocks_scope",
        TABLE,
        ["owner_id", "novel_id", "pipeline"],
    )
    op.create_index("ix_canon_contamination_blocks_attempt", TABLE, ["attempt_hash"])
    # Match the ORM model: owner_id / novel_id are index=True FK columns.
    op.create_index(
        "ix_canon_contamination_blocks_owner_id", TABLE, ["owner_id"]
    )
    op.create_index(
        "ix_canon_contamination_blocks_novel_id", TABLE, ["novel_id"]
    )


def downgrade() -> None:
    """Drop the audit table symmetrically."""
    if not _has_table(TABLE):
        return
    op.drop_index("ix_canon_contamination_blocks_novel_id", table_name=TABLE)
    op.drop_index("ix_canon_contamination_blocks_owner_id", table_name=TABLE)
    op.drop_index("ix_canon_contamination_blocks_attempt", table_name=TABLE)
    op.drop_index("ix_canon_contamination_blocks_scope", table_name=TABLE)
    op.drop_table(TABLE)
