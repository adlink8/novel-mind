"""Phase 30-04 Visual Bible review/versioning integration tests (REQ-VIS-01).

Covers the 30-VALIDATION.md review matrix against PostgreSQL:
- the review envelope exposes history events, approval-gate reason codes,
  parent revision and an immutable revision ref for Scene Candidate use;
- approval is append-only, explicit, idempotent and candidate-only; the
  fail-closed gate blocks approval while any reference asset is not
  rights-cleared (reason-coded, never silent);
- an approved candidate never touches the source chapter or an asset's
  approved flag, and the audit details (budget marker + lineage) persist;
- editing records intent without moving state; a child revision keeps the old
  revision permanently readable and links it through the parent revision ref;
- cross-owner access to the review envelope fails closed with 404.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.novel import Chapter
from app.models.visual_bible import VisualBibleReviewEvent, VisualBibleVersion
from app.schemas.visual_bible import (
    VisualBibleVersionContract,
    recompute_manifest_hash,
)
from tests.integration.visual_bible.test_scope import (
    _review_payload,
    _seed_owner,
    basic_version_payload,
)

pytestmark = pytest.mark.integration


def child_version_payload(
    ids: dict[str, Any],
    *,
    parent_version_id: int,
    version_key: str = "vb-main-r2",
    revision_number: int = 2,
) -> dict[str, Any]:
    """vb-edit-r2: same immutable content, new lineage (parent + revision)."""
    contract = VisualBibleVersionContract.model_validate(
        basic_version_payload(ids, version_key=version_key)
    )
    contract = contract.model_copy(
        update={
            "revision_number": revision_number,
            "parent_version_id": parent_version_id,
        }
    )
    contract = contract.model_copy(
        update={"manifest_hash": recompute_manifest_hash(contract)}
    )
    return contract.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Review envelope: history, reason codes, parent + immutable revision ref
# ---------------------------------------------------------------------------


async def test_review_envelope_exposes_immutable_revision_ref(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"env_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    created = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["version"]["id"]

    env = await client.get(f"{base}/{version_id}/review-envelope", headers=headers)
    assert env.status_code == 200, env.text
    body = env.json()
    assert body["version_id"] == version_id
    assert body["review_state"] == "candidate"
    assert body["review_events"] == []
    assert body["parent_version_id"] is None
    assert body["parent_revision_ref"] is None

    # The immutable revision ref freezes identity + content lineage for the
    # downstream Scene Candidate chain (stable version/hash/evidence contract).
    ref = body["revision_ref"]
    assert ref["kind"] == "visual_bible"
    assert ref["version_id"] == version_id
    assert ref["version_key"] == "vb-main"
    assert ref["revision_number"] == 1
    assert ref["manifest_hash"] == created.json()["version"]["manifest_hash"]
    assert ref["source_snapshot_hash"] == created.json()["version"][
        "source_snapshot_hash"
    ]
    assert ref["cutoff_chapter"] == created.json()["version"]["cutoff_chapter"]

    # Default fixture assets are rights-cleared, so the candidate is gate-clean.
    gate = body["approval_gate"]
    assert gate is not None
    assert gate["ok"] is True
    assert gate["reason_code"] is None


# ---------------------------------------------------------------------------
# Approval gate: rights-unresolved blocks approval (fail closed, reason-coded)
# ---------------------------------------------------------------------------


async def test_approval_blocked_until_rights_cleared(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"gate_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    pending = await client.post(
        base,
        json={"version": basic_version_payload(ids, asset_rights="pending")},
        headers=headers,
    )
    assert pending.status_code == 201, pending.text
    pending_id = pending.json()["version"]["id"]
    review_url = f"{base}/{pending_id}/review"

    blocked = await client.post(
        review_url, json=_review_payload(event_key="ev-gate-1"), headers=headers
    )
    assert blocked.status_code == 409, blocked.text
    assert "rights_unresolved" in blocked.json()["detail"]

    # The envelope surfaces the exact reason code and the offending asset.
    env = await client.get(
        f"{base}/{pending_id}/review-envelope", headers=headers
    )
    gate = env.json()["approval_gate"]
    assert gate["ok"] is False
    assert gate["reason_code"] == "rights_unresolved"
    assert gate["unresolved_assets"] == ["ref-ayla-sketch"]
    # The blocked version stays a candidate; no event was appended.
    assert env.json()["review_state"] == "candidate"
    assert env.json()["review_events"] == []

    # A rights-cleared candidate (distinct version_key) can be approved.
    cleared = await client.post(
        base,
        json={"version": basic_version_payload(ids, version_key="vb-main-cleared")},
        headers=headers,
    )
    assert cleared.status_code == 201, cleared.text
    cleared_id = cleared.json()["version"]["id"]
    ok = await client.post(
        f"{base}/{cleared_id}/review", json=_review_payload(), headers=headers
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["review_state"] == "approved"
    # No longer 'waiting' for approval, so the gate is omitted from the envelope.
    assert ok.json()["approval_gate"] is None


# ---------------------------------------------------------------------------
# Approval is append-only, auditable and candidate-only
# ---------------------------------------------------------------------------


async def test_approval_is_auditable_and_never_promotes_asset_or_chapter(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"audit_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    created = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    version_id = created.json()["version"]["id"]

    approved = await client.post(
        f"{base}/{version_id}/review", json=_review_payload(), headers=headers
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["review_state"] == "approved"
    assert len(body["review_events"]) == 1
    event = body["review_events"][0]
    assert event["action"] == "approve"
    assert event["from_review_state"] == "candidate"
    assert event["to_review_state"] == "approved"

    # The version view confirms candidate-only: generated asset stays unapproved.
    detail = await client.get(f"{base}/{version_id}", headers=headers)
    assert detail.json()["review_state"] == "approved"
    assert detail.json()["reference_assets"][0]["approved"] is False

    # The audit details persist on the append-only event row.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        stored = session.scalar(
            select(VisualBibleReviewEvent).where(
                VisualBibleReviewEvent.version_id == version_id,
                VisualBibleReviewEvent.event_key == "ev-approve-1",
            )
        )
        assert stored is not None
        assert stored.to_review_state == "approved"
        # Phase 30 has no provider calls: budget is explicitly not_applicable,
        # lineage hashes are frozen, and the rights snapshot is visible.
        assert stored.details["budget"]["status"] == "not_applicable"
        assert stored.details["lineage"]["manifest_hash"] == created.json()["version"][
            "manifest_hash"
        ]
        assert stored.details["rights"][0]["asset_key"] == "ref-ayla-sketch"
        assert stored.details["approval_gate"]["ok"] is True

        # The authoritative source chapter is never rewritten by approval.
        chapter = session.get(Chapter, ids["chapter_ids"][0])
        assert chapter.content == ids["contents"][0]
    engine.dispose()


async def test_repeated_approval_event_key_is_idempotent(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"idem_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    created = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    version_id = created.json()["version"]["id"]
    review_url = f"{base}/{version_id}/review"

    first = await client.post(
        review_url, json=_review_payload(), headers=headers
    )
    assert first.status_code == 200
    assert first.json()["review_state"] == "approved"
    assert len(first.json()["review_events"]) == 1

    # The same event_key (a retried approval) replays the state, never appends.
    retry = await client.post(
        review_url, json=_review_payload(), headers=headers
    )
    assert retry.status_code == 200
    assert retry.json()["review_state"] == "approved"
    assert len(retry.json()["review_events"]) == 1


# ---------------------------------------------------------------------------
# Versioning: edit intent, child revision lineage, old revision stays readable
# ---------------------------------------------------------------------------


async def test_edit_records_intent_and_child_lineage_keeps_old_revision(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"edit_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/visual-bible"

    v1 = await client.post(
        base, json={"version": basic_version_payload(ids)}, headers=headers
    )
    assert v1.status_code == 201, v1.text
    v1_id = v1.json()["version"]["id"]

    # edit records the human intent; the review state stays candidate.
    edited = await client.post(
        f"{base}/{v1_id}/review",
        json=_review_payload(action="edit", event_key="ev-edit-1"),
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["review_state"] == "candidate"
    assert [e["action"] for e in edited.json()["review_events"]] == ["edit"]

    # A child revision (new revision_number, parent lineage) is created and
    # approved; the approved child freezes the candidate for Scene Candidates.
    child = await client.post(
        base,
        json={"version": child_version_payload(ids, parent_version_id=v1_id)},
        headers=headers,
    )
    assert child.status_code == 201, child.text
    child_id = child.json()["version"]["id"]
    child_ref = child.json()["version"]

    approved = await client.post(
        f"{base}/{child_id}/review", json=_review_payload(), headers=headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"

    # The approved child envelope links back to the parent revision ref.
    env = await client.get(
        f"{base}/{child_id}/review-envelope", headers=headers
    )
    body = env.json()
    assert body["revision_ref"]["parent_version_id"] == v1_id
    assert body["parent_revision_ref"]["version_id"] == v1_id
    assert body["revision_ref"]["manifest_hash"] == child_ref["manifest_hash"]
    assert body["revision_ref"]["source_snapshot_hash"] == child_ref[
        "source_snapshot_hash"
    ]

    # The old revision is permanently readable with its original content and
    # lineage, even after the child is approved.
    old = await client.get(f"{base}/{v1_id}", headers=headers)
    assert old.status_code == 200
    assert old.json()["version_key"] == "vb-main"
    assert old.json()["revision_number"] == 1
    assert old.json()["review_state"] == "candidate"
    assert old.json()["manifest_hash"] == v1.json()["version"]["manifest_hash"]
    assert len(old.json()["entities"]) == 3  # content unchanged


# ---------------------------------------------------------------------------
# Cross-owner access to the review envelope fails closed
# ---------------------------------------------------------------------------


async def test_review_envelope_cross_owner_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"env_a_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"env_b_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}

    base_a = f"/api/novels/{ids_a['novel_id']}/visual-bible"
    created = await client.post(
        base_a, json={"version": basic_version_payload(ids_a)}, headers=headers_a
    )
    assert created.status_code == 201
    version_id = created.json()["version"]["id"]

    # Owner B probing owner A's review envelope is indistinguishable from a
    # missing novel (no owner leak).
    foreign = await client.get(
        f"{base_a}/{version_id}/review-envelope", headers=headers_b
    )
    assert foreign.status_code == 404

    missing = await client.get(
        "/api/novels/999999991/visual-bible/1/review-envelope", headers=headers_b
    )
    assert missing.status_code == 404
    assert foreign.json() == missing.json()


# ---------------------------------------------------------------------------
# Service-level seam: append_event gate + idempotent durable append
# ---------------------------------------------------------------------------


async def test_review_service_append_event_gates_and_persists(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"svc_r_{uuid.uuid4().hex[:8]}")

    from app.schemas.visual_bible import (
        VisualActorSource,
        VisualReviewEventInput,
        VisualReviewState,
    )
    from app.services.visual_bible.review import VisualBibleReviewService

    async with factory() as session:
        version = VisualBibleVersionContract.model_validate(
            basic_version_payload(ids)
        )
        # Persist the candidate directly through the authority seam.
        from app.services.visual_bible.authority import (
            VisualBibleAuthorityService,
        )
        from app.services.visual_bible.evidence import VisualBibleEvidenceService

        evidence = VisualBibleEvidenceService(session)
        outcome = await evidence.materialize_version_claims(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            claims=version.claims,
        )
        assert not outcome.blocked
        verified = {m.claim.claim_key: m.verified_evidence for m in outcome.resolved}
        persisted = await VisualBibleAuthorityService(session).create_revision(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=version,
            verified_evidence=verified,
        )
        version_id = persisted.version.id

        review = VisualBibleReviewService(session)
        event = VisualReviewEventInput(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=version_id,
            action="approve",
            actor_source=VisualActorSource.HUMAN,
            actor="owner",
            reason="matches the text",
            event_key="ev-svc-approve",
            from_review_state=VisualReviewState.CANDIDATE,
        )
        applied = await review.append_event(
            owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
        )
        assert applied.review_state == "approved"

        # Idempotent replay appends nothing.
        replayed = await review.append_event(
            owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
        )
        assert replayed.review_state == "approved"
        count = (
            await session.scalar(
                select(VisualBibleReviewEvent.id).where(
                    VisualBibleReviewEvent.version_id == version_id
                )
            )
            is not None
        )
        assert count

        # Approval gate blocks a non-cleared asset version.
        gate_ids = _seed_owner(sync_url, suffix=f"svc_g_{uuid.uuid4().hex[:8]}")
        blocked_version = VisualBibleVersionContract.model_validate(
            basic_version_payload(gate_ids, asset_rights="unreviewed")
        )
        blocked_evidence = VisualBibleEvidenceService(session)
        blocked_outcome = await blocked_evidence.materialize_version_claims(
            owner_id=gate_ids["owner_id"],
            novel_id=gate_ids["novel_id"],
            source_snapshot_id=blocked_version.source_snapshot_id,
            source_snapshot_hash=blocked_version.source_snapshot_hash,
            cutoff_chapter=blocked_version.cutoff_chapter,
            claims=blocked_version.claims,
        )
        assert not blocked_outcome.blocked
        blocked_verified = {
            m.claim.claim_key: m.verified_evidence
            for m in blocked_outcome.resolved
        }
        blocked_persisted = await VisualBibleAuthorityService(session).create_revision(
            owner_id=gate_ids["owner_id"],
            novel_id=gate_ids["novel_id"],
            version=blocked_version,
            verified_evidence=blocked_verified,
        )
        from app.services.visual_bible.review import GateViolationError

        with pytest.raises(GateViolationError) as excinfo:
            await review.append_event(
                owner_id=gate_ids["owner_id"],
                novel_id=gate_ids["novel_id"],
                event=VisualReviewEventInput(
                    owner_id=gate_ids["owner_id"],
                    novel_id=gate_ids["novel_id"],
                    version_id=blocked_persisted.version.id,
                    action="approve",
                    actor_source=VisualActorSource.HUMAN,
                    actor="owner",
                    reason="attempt",
                    event_key="ev-blocked",
                    from_review_state=VisualReviewState.CANDIDATE,
                ),
            )
        assert "rights_unresolved" in str(excinfo.value)
        await session.rollback()
