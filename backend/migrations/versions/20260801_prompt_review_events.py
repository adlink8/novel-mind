"""PromptRevision append-only review events table (Phase 32-04).

REQ-VIS-03 / D-32-04: approval/review is explicit, append-only and idempotent.
One new append-only table:

  - ``prompt_revision_review_events``: one explicit review action
    (approve/reject/supersede/needs_relink) on one PromptRevision candidate.
    A unique ``(owner_id, novel_id, revision_id, event_key)`` constraint makes
    a duplicate action replay instead of appending a second event. The
    ``prompt_revisions.review_state`` column is a projection; approval only
    marks the PromptRevision as an approved Phase 33 input and never rewrites
    the SceneSpec or original source.

Design conventions (matching 20260801_scene_spec_prompt/20260801_key_scene):
idempotent inspector guards, symmetric downgrade, composite owner/novel/revision
scope FK and composite idempotency constraint. No existing rows are touched.

Revision ID: 20260801_prompt_review_events
Revises: 20260801_scene_spec_prompt
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_prompt_review_events"
down_revision = "20260801_scene_spec_prompt"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_ACTIONS = "'approve','reject','supersede','needs_relink'"
_ACTOR_SOURCES = "'human','machine'"
_REVIEW_STATES = "'candidate','approved','rejected','superseded','needs_relink'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""
    if _has_table("prompt_revision_review_events"):
        return
    op.create_table(
        "prompt_revision_review_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer, nullable=False),
        sa.Column("novel_id", sa.Integer, nullable=False),
        sa.Column("revision_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("actor_source", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("from_review_state", sa.String(16), nullable=False),
        sa.Column("to_review_state", sa.String(16), nullable=False),
        sa.Column("details", JSONB, nullable=False),
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
        sa.UniqueConstraint(
            "owner_id",
            "novel_id",
            "revision_id",
            "event_key",
            name="uq_prompt_revision_review_events_key",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "revision_id"],
            [
                "prompt_revisions.owner_id",
                "prompt_revisions.novel_id",
                "prompt_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_prompt_revision_review_events_revision_scope",
        ),
        sa.CheckConstraint(
            f"action IN ({_ACTIONS})",
            name="ck_prompt_revision_review_events_action",
        ),
        sa.CheckConstraint(
            f"actor_source IN ({_ACTOR_SOURCES})",
            name="ck_prompt_revision_review_events_actor_source",
        ),
        sa.CheckConstraint(
            f"from_review_state IN ({_REVIEW_STATES})",
            name="ck_prompt_revision_review_events_from_state",
        ),
        sa.CheckConstraint(
            f"to_review_state IN ({_REVIEW_STATES})",
            name="ck_prompt_revision_review_events_to_state",
        ),
    )
    op.create_index(
        "idx_prompt_revision_review_events_revision",
        "prompt_revision_review_events",
        ["owner_id", "novel_id", "revision_id"],
    )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; existing rows stay untouched."""
    if not _has_table("prompt_revision_review_events"):
        return
    op.drop_index(
        "idx_prompt_revision_review_events_revision",
        table_name="prompt_revision_review_events",
    )
    op.drop_table("prompt_revision_review_events")
