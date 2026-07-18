"""Integration: human overrides survive reanalysis with append-only supersession."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.models.clue import (
    ClueAnalysisVersion,
    ClueEvidenceRef,
    ClueOverride,
    MachineClue,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.schemas.clue import ClueLifecycleState
from app.services.clues.lifecycle import append_lifecycle_event, derived_state_for_clue
from app.services.clues.overrides import (
    human_annotate,
    human_confirm,
    human_reject,
)
from app.services.clues.versions import snapshot_manifest

pytestmark = pytest.mark.integration

HEX = "a" * 64


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _seed(db_session):
    owner = User(username="ovr-owner", email="ovr@example.test", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="override novel", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="C1", content="cue text")
    db_session.add(chapter)
    await db_session.flush()
    version = ClueAnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="v1",
        status="validated",
        source_snapshot_hash=HEX,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX,
        prompt_hash=HEX,
        schema_hash=HEX,
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
        model_lineage={},
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    machine = MachineClue(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-seal",
        title="Seal",
        summary="",
        package_hash=HEX,
        package_snapshot={},
        confidence=0.9,
        publication_status="published",
        first_cue_chapter=1,
        first_cue_source_start=0,
    )
    db_session.add(machine)
    await db_session.flush()
    content_hash = _h("cue text")
    db_session.add(
        ClueEvidenceRef(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id="clue-seal",
            machine_clue_id=machine.id,
            role="cue",
            evidence_id="ev1",
            evidence_identity=f"ev1:{chapter.id}:0:8:{content_hash}",
            chapter_id=chapter.id,
            narrative_chapter_number=1,
            source_start=0,
            source_end=8,
            content_hash=content_hash,
        )
    )
    await db_session.commit()
    return owner, novel, chapter, version, machine, content_hash


@pytest.mark.asyncio
async def test_confirm_reject_annotate_append_only(db_session):
    owner, novel, chapter, version, machine, content_hash = await _seed(db_session)
    evidence = [
        {
            "evidence_id": "ev1",
            "role": "cue",
            "chapter_id": chapter.id,
            "narrative_chapter_number": 1,
            "source_start": 0,
            "source_end": 8,
            "content_hash": content_hash,
        }
    ]
    override, life = await human_confirm(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-seal",
        author="owner",
        reason="looks real",
        evidence=evidence,
    )
    await db_session.commit()
    assert override.action == "confirm"
    state = await derived_state_for_clue(
        db_session, version_id=version.id, logical_clue_id="clue-seal"
    )
    assert state == ClueLifecycleState.ACTIVE

    note = await human_annotate(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-seal",
        author="owner",
        reason="memo",
        note="watch payoff",
    )
    await db_session.commit()
    state2 = await derived_state_for_clue(
        db_session, version_id=version.id, logical_clue_id="clue-seal"
    )
    assert state2 == ClueLifecycleState.ACTIVE
    assert note.field_name == "note"

    # Reject is terminal — from active.
    reject, _ = await human_reject(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-seal",
        author="owner",
        reason="false positive",
    )
    await db_session.commit()
    state3 = await derived_state_for_clue(
        db_session, version_id=version.id, logical_clue_id="clue-seal"
    )
    assert state3 == ClueLifecycleState.DISMISSED

    # Machine transition after human dismiss is blocked.
    with pytest.raises(Exception):
        await append_lifecycle_event(
            db_session,
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id="clue-seal",
            to_status=ClueLifecycleState.ACTIVE,
            actor_source="machine",
            reason="machine retry",
            evidence=evidence,
            event_key="machine-retry",
        )

    # Prior override rows remain; supersession is INSERT.
    rows = list(
        (
            await db_session.scalars(
                select(ClueOverride)
                .where(ClueOverride.novel_id == novel.id)
                .order_by(ClueOverride.id)
            )
        ).all()
    )
    assert len(rows) >= 3
    assert rows[0].reason == "looks real"
    # First row not mutated to superseded (append-only contract).
    assert rows[0].status == "active"


@pytest.mark.asyncio
async def test_reanalysis_preserves_human_rows(db_session):
    owner, novel, chapter, version, machine, content_hash = await _seed(db_session)
    note = await human_annotate(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-seal",
        author="owner",
        reason="keep",
        note="human note survives",
    )
    await db_session.commit()
    original_id = note.id

    # New machine version with different logical id but same evidence identity.
    v2 = ClueAnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="v2",
        status="validated",
        source_snapshot_hash=HEX,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX,
        prompt_hash=HEX,
        schema_hash=HEX,
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
        model_lineage={},
        price_snapshot={},
        manifest={},
    )
    db_session.add(v2)
    await db_session.flush()
    m2 = MachineClue(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=v2.id,
        logical_clue_id="clue-seal-v2",
        title="Seal v2",
        summary="",
        package_hash=HEX,
        package_snapshot={},
        confidence=0.95,
        publication_status="published",
        first_cue_chapter=1,
        first_cue_source_start=0,
    )
    db_session.add(m2)
    await db_session.flush()
    db_session.add(
        ClueEvidenceRef(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=v2.id,
            logical_clue_id="clue-seal-v2",
            machine_clue_id=m2.id,
            role="cue",
            evidence_id="ev1",
            evidence_identity=f"ev1:{chapter.id}:0:8:{content_hash}",
            chapter_id=chapter.id,
            narrative_chapter_number=1,
            source_start=0,
            source_end=8,
            content_hash=content_hash,
        )
    )
    await db_session.flush()
    manifest, checksum = await snapshot_manifest(db_session, version.id)
    version.manifest = manifest
    version.manifest_checksum = checksum
    manifest2, checksum2 = await snapshot_manifest(db_session, v2.id)
    v2.manifest = manifest2
    v2.manifest_checksum = checksum2
    await db_session.commit()

    from app.services.clues.versions import promote_version

    await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=version.id,
        expected_revision=0,
    )
    await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=v2.id,
        expected_revision=1,
    )

    # Original human row byte-stable.
    prior = await db_session.get(ClueOverride, original_id)
    assert prior is not None
    assert prior.value == {"note": "human note survives"}
    assert prior.reason == "keep"

    all_rows = list(
        (
            await db_session.scalars(
                select(ClueOverride).where(ClueOverride.novel_id == novel.id)
            )
        ).all()
    )
    # Relink may have appended a new row mapping to clue-seal-v2.
    assert any(r.id == original_id for r in all_rows)
    assert len(all_rows) >= 1
