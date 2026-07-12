"""Exact actual-ID and lifecycle residue reconciliation tests."""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

import pytest

pytestmark = pytest.mark.unit

from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativeUnit,
)
from app.services.knowledge_units.indexing import NarrativeIndexingService
from app.services.knowledge_units.reconcile import reconcile_build
from tests.test_knowledge_unit_indexing import FakeStore, _candidate_build

BACKEND = Path(__file__).parents[1]


async def _indexed(db):
    build = await _candidate_build(db)
    store = FakeStore()

    async def embed(texts):
        return [[0.1, 0.2] for _ in texts]

    report = await NarrativeIndexingService(store).build_candidate(
        db, build_id=build.id, embedder=embed
    )
    collection = store.collections[report.collection_name]
    actual = [
        {"id": item_id, "metadata": metadata}
        for item_id, metadata in zip(collection.ids, collection.metadatas)
    ]
    return build, actual


async def test_clean_actual_ids_reconcile(db_session):
    build, actual = await _indexed(db_session)
    report = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    assert report.passed


async def test_orphan_and_wrong_owner_fail_reconcile(db_session):
    build, actual = await _indexed(db_session)
    actual.append(
        {"id": "unit_orphan", "metadata": {"owner_id": 999, "novel_id": build.novel_id}}
    )
    report = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    assert report.orphan == ("unit_orphan",)
    assert report.wrong_owner == ("unit_orphan",)
    assert not report.passed


async def test_deprecated_unit_is_zero_residue_gate(db_session):
    build, actual = await _indexed(db_session)
    unit = await db_session.scalar(select(NarrativeUnit))
    unit.lifecycle_status = "deprecated"
    unit.status = "deprecated"
    report = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    assert actual[0]["id"] in report.deprecated
    assert not report.passed


async def test_active_scope_fails_closed_and_selects_one_of_multiple_pointers(
    db_session,
):
    from datetime import UTC, datetime
    from scripts.reconcile_narrative_unit_index import resolve_active_build_id

    first = await _candidate_build(db_session)
    second = NarrativeIndexBuild(
        owner_id=first.owner_id,
        novel_id=first.novel_id,
        source_snapshot_id=first.source_snapshot_id,
        domain_profile="history",
        build_key="second-scope",
        status="active",
        manifest_checksum="z" * 64,
        config_checksum="c" * 64,
        unit_count=0,
    )
    first.status = "active"
    db_session.add(second)
    await db_session.flush()
    pointers = [
        NarrativeActivePointer(
            owner_id=first.owner_id,
            novel_id=first.novel_id,
            domain_profile=build.domain_profile,
            build_id=build.id,
            pointer_version=1,
            active_manifest_checksum=build.manifest_checksum,
            activated_at=datetime.now(UTC),
        )
        for build in (first, second)
    ]
    db_session.add_all(pointers)
    await db_session.flush()

    with pytest.raises(ValueError, match="exactly one"):
        await resolve_active_build_id(db_session)
    assert (
        await resolve_active_build_id(db_session, pointer_id=pointers[1].id)
        == second.id
    )
    assert (
        await resolve_active_build_id(db_session, domain="fiction") == first.id
    )


@pytest.mark.integration
def test_scoped_active_reconcile_subprocess_contract():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/reconcile_narrative_unit_index.py",
            "--active",
            "--owner-id",
            "1",
            "--novel-id",
            "2",
            "--domain",
            "fiction",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "unrecognized arguments" not in result.stderr
    assert "scope options require" not in result.stderr
