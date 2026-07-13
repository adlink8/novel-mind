"""Phase 08 versioned analysis and timeline authority.

Revision ID: 10analysistime01
Revises: 09chunkhier01
"""

from alembic import op

revision = "10analysistime01"
down_revision = "09chunkhier01"
branch_labels = None
depends_on = None


ANALYSIS_TABLES = [
    "analysis_versions", "analysis_runs", "analysis_budget_ledgers",
    "analysis_budget_reservations", "analysis_chapter_stages", "model_call_attempts",
]
TIMELINE_TABLES = [
    "machine_timeline_events", "timeline_participants", "timeline_evidence_refs",
    "timeline_causal_edges", "timeline_overrides", "timeline_active_pointers",
    "timeline_pointer_journal",
]


def upgrade() -> None:
    # ORM tables are the single DDL contract; dependency order is explicit here.
    from app.models import Base  # imports all model modules into metadata

    bind = op.get_bind()
    for name in ANALYSIS_TABLES + TIMELINE_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    from app.models import Base

    for name in reversed(ANALYSIS_TABLES + TIMELINE_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
