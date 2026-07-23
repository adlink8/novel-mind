"""Conservative canonicalization and lifecycle tests."""

import pytest

pytestmark = pytest.mark.unit

from sqlalchemy import select

from app.models.knowledge_unit import NarrativeUnit
from app.services.knowledge_units.canonicalize import (
    canonical_key,
    merge_block_reason,
    narrative_canonicalizer,
)
from app.services.knowledge_units.lifecycle import sync_unit_lifecycle
from tests.test_knowledge_unit_materialize import _accepted_source
from app.services.knowledge_units.materialize import narrative_unit_materializer


def _unit(**overrides):
    values = dict(
        owner_id=1,
        novel_id=1,
        domain_profile="fiction",
        subject_key="entity:1",
        relation_type="ally",
        answer="entity:1 --ally--> entity:2。",
        lifecycle_status="current",
    )
    values.update(overrides)
    unit = NarrativeUnit()
    for key, value in values.items():
        setattr(unit, key, value)
    return unit


def test_exact_units_share_canonical_key():
    assert canonical_key(_unit()) == canonical_key(_unit())


def test_subject_mismatch_blocks_merge():
    assert (
        merge_block_reason(_unit(), _unit(subject_key="entity:2")) == "subject_mismatch"
    )


def test_relation_conflict_blocks_merge():
    assert (
        merge_block_reason(_unit(relation_type="ally"), _unit(relation_type="enemy"))
        == "relation_conflict"
    )


def test_sequence_is_not_causality():
    assert (
        merge_block_reason(
            _unit(relation_type="precedes"), _unit(relation_type="causes")
        )
        == "relation_conflict"
    )


def test_lifecycle_mismatch_blocks_merge():
    assert (
        merge_block_reason(_unit(), _unit(lifecycle_status="disputed"))
        == "lifecycle_mismatch"
    )


async def test_canonicalization_persists_candidate_state(db_session):
    snapshot = await _accepted_source(db_session)
    await narrative_unit_materializer.materialize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    report = await narrative_canonicalizer.canonicalize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    unit = await db_session.scalar(select(NarrativeUnit))
    assert report.canonicalized == 1
    assert report.hard_negative_false_merges == 0
    assert (
        unit is not None
        and unit.unit_stage == "canonical"
        and unit.status == "candidate"
    )


async def test_exact_duplicates_publish_one_representative(db_session):
    snapshot = await _accepted_source(db_session)
    await narrative_unit_materializer.materialize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    original = await db_session.scalar(select(NarrativeUnit))
    duplicate = NarrativeUnit(
        owner_id=original.owner_id,
        novel_id=original.novel_id,
        source_snapshot_id=original.source_snapshot_id,
        source_judgment_id=original.source_judgment_id,
        source_candidate_id=original.source_candidate_id,
        primary_evidence_id=original.primary_evidence_id,
        domain_profile=original.domain_profile,
        ontology_profile=original.ontology_profile,
        unit_stage="draft",
        status="draft",
        lifecycle_status=original.lifecycle_status,
        canonical_id=None,
        version=2,
        subject_key=original.subject_key,
        relation_type=original.relation_type,
        question=original.question,
        answer=original.answer,
        confidence=original.confidence,
        evidence_count=original.evidence_count,
        content_hash=original.content_hash,
        evidence_manifest_checksum=original.evidence_manifest_checksum,
    )
    db_session.add(duplicate)
    await db_session.flush()
    await narrative_canonicalizer.canonicalize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    rows = list(
        (
            await db_session.scalars(select(NarrativeUnit).order_by(NarrativeUnit.id))
        ).all()
    )
    assert [(row.unit_stage, row.status) for row in rows] == [
        ("canonical", "candidate"),
        ("draft", "deprecated"),
    ]


async def test_rejected_source_deprecates_without_deleting(db_session):
    snapshot = await _accepted_source(db_session)
    await narrative_unit_materializer.materialize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    unit = await db_session.scalar(select(NarrativeUnit))
    from app.models.knowledge import KnowledgeRelationJudgment

    judgment = await db_session.get(KnowledgeRelationJudgment, unit.source_judgment_id)
    judgment.status = "rejected"
    counts = await sync_unit_lifecycle(db_session, snapshot_id=snapshot.id)
    assert counts["deprecated"] == 1
    assert unit.status == "deprecated" and unit.lifecycle_status == "deprecated"
