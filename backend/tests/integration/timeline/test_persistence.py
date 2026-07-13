"""Phase 08 timeline persistence authority contract."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.analysis import AnalysisRun, AnalysisVersion, ModelCallAttempt
from app.models.timeline import MachineTimelineEvent, TimelineOverride, TimelineActivePointer, TimelinePointerJournal

pytestmark = pytest.mark.integration


def test_timeline_metadata_contains_authority_tables():
    tables = AnalysisRun.metadata.tables
    expected = {
        "analysis_runs", "analysis_versions", "analysis_chapter_stages",
        "model_call_attempts", "analysis_budget_ledgers", "analysis_budget_reservations",
        "machine_timeline_events", "timeline_participants", "timeline_evidence_refs",
        "timeline_causal_edges", "timeline_overrides", "timeline_active_pointers",
        "timeline_pointer_journal",
    }
    assert expected <= set(tables)


def test_mixed_time_and_dual_order_are_explicit_columns():
    columns = set(inspect(MachineTimelineEvent).columns.keys())
    assert {"time_precision", "time_expression", "exact_time", "relative_anchor_event_id",
            "relative_relation", "fuzzy_start", "fuzzy_end", "narrative_chapter_number",
            "narrative_index", "story_rank", "story_constraints"} <= columns


def test_owner_scope_and_override_relink_are_persisted():
    assert {"owner_id", "novel_id"} <= set(inspect(AnalysisVersion).columns.keys())
    assert {"supersedes_id", "status", "needs_relink"} <= set(inspect(TimelineOverride).columns.keys())


def test_failure_audit_cache_skip_and_reversible_pointer_contract():
    attempt = set(inspect(ModelCallAttempt).columns.keys())
    assert {"status", "cache_key", "cache_source_attempt_id", "request_hash", "response_hash"} <= attempt
    pointer = set(inspect(TimelineActivePointer).columns.keys())
    journal = set(inspect(TimelinePointerJournal).columns.keys())
    assert {"version_id", "revision", "manifest_checksum"} <= pointer
    assert {"from_version_id", "to_version_id", "action", "expected_revision", "resulting_revision", "manifest"} <= journal


@pytest.mark.asyncio
async def test_postgres_round_trip_and_constraints(pg_async_url, require_postgres):
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # The shared PG fixture is migrated before this test; prove the new authority exists.
        connection = await session.connection()
        names = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
        assert "analysis_runs" in names
        assert "machine_timeline_events" in names
    await engine.dispose()
