"""Phase 36-05: widen the derivative revision kind gate for the agent-proposal path.

REQ-FORK-02 / REQ-AGENT-03/04/07 / D-36-02. The 36-05 Agent Consumer Contract
separates ``user_autosave`` and ``agent_proposal`` on every axis (endpoints,
event names, actor labels, CAS entry points). The revision row kind is the
database-level separation: this migration widens ``ck_derivative_revisions_kind``
from ``('create','autosave','rollback')`` to
``('create','autosave','rollback','agent_proposal')`` so the deterministic
Revision Service's application of an approved DerivativeEditProposal can never
be mistaken for a user draft. Everything else in the append-only lineage
contract stays untouched.

Revision ID: 20260801_derivative_agent_edit01
Revises: 20260801_derivative_revision01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_derivative_agent_edit01"
down_revision = "20260801_derivative_revision01"
branch_labels = None
depends_on = None

TABLE = "derivative_revisions"
CONSTRAINT = "ck_derivative_revisions_kind"

_OLD_KINDS = "'create','autosave','rollback'"
_NEW_KINDS = "'create','autosave','rollback','agent_proposal'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Widen the kind check constraint to admit agent_proposal rows."""
    if not _has_table(TABLE):
        return
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        f"kind IN ({_NEW_KINDS})",
    )


def downgrade() -> None:
    """Restore the original kind gate (replayable round trip)."""
    if not _has_table(TABLE):
        return
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        f"kind IN ({_OLD_KINDS})",
    )
