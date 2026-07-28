"""Spoiler-safe clue API projection and ownership contracts."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisVersion,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    ClueOverride,
    MachineClue,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.schemas.clue import ClueVersionSource
from app.services.clues.query import build_clue_version_view

pytestmark = pytest.mark.integration

HEX = "a" * 64


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _seed(db_session):
    owner = User(
        username="clue-spoiler", email="clue-spoiler@example.test", hashed_password="x"
    )
    other = User(
        username="clue-other", email="clue-other@example.test", hashed_password="x"
    )
    db_session.add_all([owner, other])
    await db_session.flush()
    novel = Novel(
        owner_id=owner.id, title="spoiler novel", status="ready", reading_progress={}
    )
    db_session.add(novel)
    await db_session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=1, title="C1", content="early cue"),
        Chapter(
            novel_id=novel.id, chapter_number=5, title="C5", content="secret payoff"
        ),
    ]
    db_session.add_all(chapters)
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
        logical_clue_id="clue-payoff",
        title="The letter",
        summary="recovers later",
        package_hash=HEX,
        package_snapshot={},
        confidence=0.95,
        publication_status="published",
        first_cue_chapter=1,
        first_cue_source_start=0,
    )
    future_only = MachineClue(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-future-only",
        title="SECRET FUTURE CLUE",
        summary="must not leak",
        package_hash=HEX,
        package_snapshot={},
        confidence=0.9,
        publication_status="published",
        first_cue_chapter=5,
        first_cue_source_start=0,
    )
    db_session.add_all([machine, future_only])
    await db_session.flush()

    ch1, ch5 = chapters
    h1 = _h("early cue")
    h5 = _h("secret payoff")
    db_session.add_all(
        [
            ClueEvidenceRef(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-payoff",
                machine_clue_id=machine.id,
                role="cue",
                evidence_id="ev-cue",
                evidence_identity=f"ev-cue:{ch1.id}:0:9:{h1}",
                chapter_id=ch1.id,
                narrative_chapter_number=1,
                source_start=0,
                source_end=9,
                content_hash=h1,
                excerpt="early cue",
            ),
            ClueEvidenceRef(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-payoff",
                machine_clue_id=machine.id,
                role="payoff",
                evidence_id="ev-pay",
                evidence_identity=f"ev-pay:{ch5.id}:0:13:{h5}",
                chapter_id=ch5.id,
                narrative_chapter_number=5,
                source_start=0,
                source_end=13,
                content_hash=h5,
                excerpt="secret payoff",
            ),
            ClueEvidenceRef(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-future-only",
                machine_clue_id=future_only.id,
                role="cue",
                evidence_id="ev-future",
                evidence_identity=f"ev-future:{ch5.id}:0:13:{h5}",
                chapter_id=ch5.id,
                narrative_chapter_number=5,
                source_start=0,
                source_end=13,
                content_hash=h5,
                excerpt="secret payoff",
            ),
        ]
    )
    # Lifecycle: candidate→active→reinforced→paid_off
    db_session.add_all(
        [
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-payoff",
                machine_clue_id=machine.id,
                from_status="candidate",
                to_status="active",
                actor_source="machine",
                reason="cue",
                event_key="e1",
                evidence_identities=[f"ev-cue:{ch1.id}:0:9:{h1}"],
                cue_chapter=1,
                cue_source_start=0,
                gate_audit={},
            ),
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-payoff",
                machine_clue_id=machine.id,
                from_status="active",
                to_status="reinforced",
                actor_source="machine",
                reason="reinf",
                event_key="e2",
                evidence_identities=[f"ev-reinf:{ch1.id}:1:5:{h1}"],
                gate_audit={},
            ),
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-payoff",
                machine_clue_id=machine.id,
                from_status="reinforced",
                to_status="paid_off",
                actor_source="machine",
                reason="pay",
                event_key="e3",
                evidence_identities=[
                    f"ev-cue:{ch1.id}:0:9:{h1}",
                    f"ev-pay:{ch5.id}:0:13:{h5}",
                ],
                cue_chapter=1,
                cue_source_start=0,
                payoff_chapter=5,
                payoff_source_start=0,
                gate_audit={},
            ),
        ]
    )
    db_session.add(
        ClueOverride(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id="clue-future-only",
            action="annotate",
            field_name="note",
            value={"note": "SECRET OVERRIDE NOTE"},
            author="owner",
            reason="x",
            status="active",
        )
    )
    db_session.add(
        ClueActivePointer(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            revision=1,
            manifest_checksum=HEX,
        )
    )
    await db_session.commit()
    return owner, other, novel, chapters, version


@pytest.mark.asyncio
async def test_default_hides_future_and_paid_off(db_session):
    owner, _, novel, chapters, version = await _seed(db_session)
    view = await build_clue_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=ClueVersionSource.ACTIVE,
        request_full_book=False,
    )
    assert view is not None
    ids = [c.logical_clue_id for c in view.clues]
    assert "clue-payoff" in ids
    assert "clue-future-only" not in ids
    payoff = next(c for c in view.clues if c.logical_clue_id == "clue-payoff")
    # Before chapter 5, paid_off must not leak.
    assert payoff.derived_state.value in {"active", "reinforced"}
    assert payoff.derived_state.value != "paid_off"
    dumped = view.model_dump_json()
    assert "SECRET FUTURE CLUE" not in dumped
    assert "SECRET OVERRIDE NOTE" not in dumped
    assert "paid_off" not in (view.counts.get("by_state") or {})


@pytest.mark.asyncio
async def test_full_book_requires_persisted_preference(db_session):
    owner, _, novel, chapters, version = await _seed(db_session)
    novel.reading_progress = {"chapter_id": chapters[0].id, "timeline_full_book": False}
    await db_session.commit()
    denied = await build_clue_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=ClueVersionSource.ACTIVE,
        request_full_book=True,
    )
    assert denied is not None
    assert all(c.logical_clue_id != "clue-future-only" for c in denied.clues)

    novel.reading_progress = {"chapter_id": chapters[0].id, "timeline_full_book": True}
    await db_session.commit()
    allowed = await build_clue_version_view(
        db_session,
        novel=novel,
        owner_id=owner.id,
        source=ClueVersionSource.ACTIVE,
        request_full_book=True,
    )
    assert allowed is not None
    ids = [c.logical_clue_id for c in allowed.clues]
    assert "clue-future-only" in ids
    payoff = next(c for c in allowed.clues if c.logical_clue_id == "clue-payoff")
    assert payoff.derived_state.value == "paid_off"


@pytest.mark.asyncio
async def test_api_owner_scope_404(db_session, auth_client, monkeypatch):
    testuser = await db_session.scalar(select(User).where(User.username == "testuser"))
    assert testuser is not None
    owned = Novel(owner_id=testuser.id, title="empty clues", status="ready")
    db_session.add(owned)
    await db_session.commit()
    owned_id = owned.id

    ok = await auth_client.get(f"/api/clues/{owned_id}")
    assert ok.status_code == 200
    body = ok.json()
    assert body["active"] is None

    # Missing novel / version IDs return 404 without leaking existence details.
    missing_novel = await auth_client.get("/api/clues/999999001")
    assert missing_novel.status_code == 404
    missing_version = await auth_client.get(f"/api/clues/{owned_id}/versions/999999")
    assert missing_version.status_code == 404
    missing_action = await auth_client.post(
        f"/api/clues/{owned_id}/clues/no-such-clue/actions",
        json={"action": "annotate", "reason": "x", "note": "n"},
    )
    assert missing_action.status_code == 404
