"""Phase 31-03 Key Scene human review / frozen set integration tests (REQ-VIS-02).

Covers the 31-VALIDATION.md review matrix (D-31-04 / D-31-01):
- owner/novel/set scope holds at the review/freeze/frozen API boundary;
  cross-owner access fails closed (no owner leak);
- candidate approve/reject appends an explicit decision, updates only the
  candidate review projection, and rejected candidates stay in the append-only
  audit history;
- repeated ``decision_key`` replays idempotently; a fresh duplicate approval
  from an already-approved candidate fails closed (illegal transition);
- approvals run the server-side evidence gate (persisted evidence + snapshot +
  cutoff must hold);
- freeze requires at least one approved candidate and re-verifies every
  approved candidate's evidence lineage; the frozen manifest contains ONLY
  approved candidates (rejected/unresolved stay out of downstream sets);
- freeze is append-only and idempotent; candidate review is blocked after the
  set is frozen; nothing promotes candidates to canon or rewrites source text.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.key_scene import SceneEvidenceRange, SceneReviewDecision
from app.schemas.key_scene import (
    KeySceneReviewAction,
    KeySceneReviewState,
    SceneReviewDecisionInput,
)
from app.services.key_scenes.review import (
    KeySceneReviewService,
    recompute_frozen_manifest_hash,
)

from tests.integration.key_scenes.test_candidates import _generate_payload, _seed_owner

pytestmark = pytest.mark.integration


def _review_payload(
    candidate_key: str,
    *,
    action: str = "approve",
    decision_key: str | None = None,
    from_review_state: str = "candidate",
    reason: str = "人工审查：批准",
) -> dict[str, Any]:
    return {
        "decision_key": decision_key or f"ds-{uuid.uuid4().hex[:16]}",
        "action": action,
        "actor_source": "human",
        "actor": "owner",
        "reason": reason,
        "from_review_state": from_review_state,
        "candidate_key": candidate_key,
    }


def _freeze_payload(reason: str = "人工审查：冻结关键场景集") -> dict[str, Any]:
    return {
        "actor_source": "human",
        "actor": "owner",
        "reason": reason,
    }


async def _generate_set(
    client, ids: dict[str, Any]
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Generate a candidate set and return (base, headers, set view)."""
    base = f"/api/novels/{ids['novel_id']}/key-scenes"
    headers = {"Authorization": f"Bearer {ids['token']}"}
    resp = await client.post(
        f"{base}/generate", json=_generate_payload(ids), headers=headers
    )
    assert resp.status_code == 201, resp.text
    return base, headers, resp.json()["set"]


# ---------------------------------------------------------------------------
# Owner scope / IDOR matrix (review/freeze/frozen)
# ---------------------------------------------------------------------------


async def test_review_freeze_frozen_are_owner_scoped_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"ra_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"rb_{uuid.uuid4().hex[:8]}")
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}

    base_a, _, set_view = await _generate_set(client, ids_a)
    set_id = set_view["id"]
    candidate_key = set_view["candidates"][0]["candidate_key"]

    review = await client.post(
        f"{base_a}/{set_id}/review",
        json=_review_payload(candidate_key),
        headers=headers_b,
    )
    assert review.status_code == 404
    freeze = await client.post(
        f"{base_a}/{set_id}/freeze", json=_freeze_payload(), headers=headers_b
    )
    assert freeze.status_code == 404
    frozen = await client.get(f"{base_a}/{set_id}/frozen", headers=headers_b)
    assert frozen.status_code == 404

    # B's own novel still reads normally (empty list) — no owner leak.
    ok_b = await client.get(
        f"/api/novels/{ids_b['novel_id']}/key-scenes", headers=headers_b
    )
    assert ok_b.status_code == 200
    assert ok_b.json()["total"] == 0


async def test_review_freeze_unauthenticated_rejects(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rau_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_id']}/key-scenes"
    resp = await client.post(f"{base}/1/review", json=_review_payload("k"))
    assert resp.status_code == 401
    resp2 = await client.post(f"{base}/1/freeze", json=_freeze_payload())
    assert resp2.status_code == 401


# ---------------------------------------------------------------------------
# Candidate review: explicit decisions, idempotency, fail-closed transitions
# ---------------------------------------------------------------------------


async def test_approve_candidate_appends_decision_and_updates_state(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"ap_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]
    assert target["review_state"] == "candidate"

    decision_key = f"ds-approve-{uuid.uuid4().hex[:16]}"
    resp = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(
            target["candidate_key"],
            action="approve",
            decision_key=decision_key,
        ),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["set"]
    assert body["review_state"] == "candidate"  # set itself not frozen yet
    approved = next(
        c for c in body["candidates"] if c["candidate_key"] == target["candidate_key"]
    )
    assert approved["review_state"] == "approved"

    decisions = body["review_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["decision_key"] == decision_key
    assert decisions[0]["action"] == "approve"
    assert decisions[0]["actor_source"] == "human"
    assert decisions[0]["candidate_key"] == target["candidate_key"]
    assert decisions[0]["from_review_state"] == "candidate"
    assert decisions[0]["to_review_state"] == "approved"


async def test_reject_candidate_keeps_audit_history(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rj_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    resp = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(
            target["candidate_key"],
            action="reject",
            reason="场景视觉表现不足",
        ),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["set"]
    rejected = next(
        c for c in body["candidates"] if c["candidate_key"] == target["candidate_key"]
    )
    assert rejected["review_state"] == "rejected"
    # The rejected candidate stays in the candidate list (auditable history).
    assert any(
        c["candidate_key"] == target["candidate_key"] for c in body["candidates"]
    )
    assert body["review_decisions"][0]["action"] == "reject"
    assert body["review_decisions"][0]["to_review_state"] == "rejected"


async def test_repeated_decision_key_is_idempotent(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"idem_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]
    decision_key = f"ds-idem-{uuid.uuid4().hex[:16]}"

    first = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(
            target["candidate_key"], action="approve", decision_key=decision_key
        ),
        headers=headers,
    )
    assert first.status_code == 200
    second = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(
            target["candidate_key"], action="approve", decision_key=decision_key
        ),
        headers=headers,
    )
    assert second.status_code == 200
    body = second.json()["set"]
    assert body["review_decisions"] and len(body["review_decisions"]) == 1
    approved = next(
        c for c in body["candidates"] if c["candidate_key"] == target["candidate_key"]
    )
    assert approved["review_state"] == "approved"

    # Durable layer confirms exactly one decision row (no duplicate approval).
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        count = (
            session.query(SceneReviewDecision)
            .filter_by(
                decision_key=decision_key,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                set_id=set_id,
            )
            .count()
        )
        assert count == 1
    engine.dispose()


async def test_duplicate_approval_with_fresh_key_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"dup_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    ok = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(target["candidate_key"], action="approve"),
        headers=headers,
    )
    assert ok.status_code == 200

    # A second approval with a NEW key is an illegal transition (approved only
    # allows supersede/needs_relink) → fail closed.
    dup = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(target["candidate_key"], action="approve"),
        headers=headers,
    )
    assert dup.status_code == 409, dup.text
    assert (
        "illegal" in dup.json()["detail"] or "from_review_state" in dup.json()["detail"]
    )


async def test_from_review_state_mismatch_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"st_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    resp = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(
            target["candidate_key"],
            action="approve",
            from_review_state="approved",
        ),
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "illegal" in resp.json()["detail"] or "from" in resp.json()["detail"]


async def test_review_for_unknown_candidate_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"nc_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]

    resp = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload("ks-not-in-set-0", action="approve"),
        headers=headers,
    )
    assert resp.status_code == 404, resp.text


async def test_approval_gate_blocks_candidate_without_evidence(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"gate_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    # Remove the candidate's persisted evidence via Core delete (bypasses the
    # ORM append-only guard) to prove approval fails closed without evidence.
    async with factory() as session:
        await session.execute(
            delete(SceneEvidenceRange).where(
                SceneEvidenceRange.owner_id == ids["owner_id"],
                SceneEvidenceRange.novel_id == ids["novel_id"],
                SceneEvidenceRange.set_id == set_id,
            )
        )
        await session.commit()

    resp = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(target["candidate_key"], action="approve"),
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "evidence_missing" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Freeze: server-side gate + frozen manifest (approved candidates only)
# ---------------------------------------------------------------------------


async def test_freeze_requires_at_least_one_approved_candidate(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fz0_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]

    resp = await client.post(
        f"{base}/{set_id}/freeze", json=_freeze_payload(), headers=headers
    )
    assert resp.status_code == 409, resp.text
    assert "no_approved_candidates" in resp.json()["detail"]


async def test_freeze_builds_frozen_set_with_only_approved_candidates(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fz_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    by_key = {c["candidate_key"]: c for c in set_view["candidates"]}
    approve_key, reject_key, unresolved_key = list(by_key)[:3]

    ok_approve = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(approve_key, action="approve"),
        headers=headers,
    )
    assert ok_approve.status_code == 200
    ok_reject = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(reject_key, action="reject", reason="重复密度过高"),
        headers=headers,
    )
    assert ok_reject.status_code == 200

    resp = await client.post(
        f"{base}/{set_id}/freeze", json=_freeze_payload(), headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["set"]["review_state"] == "approved"
    frozen = body["frozen"]
    assert frozen["review_state"] == "approved"
    assert frozen["source_snapshot_hash"] == ids["snapshot_hash"]
    assert len(frozen["candidates"]) == 1
    assert frozen["candidates"][0]["candidate_key"] == approve_key
    assert frozen["candidates"][0]["review_state"] == "approved"
    assert len(frozen["manifest_hash"]) == 64

    # The frozen view is recomputable and deterministic. Candidate rows are
    # immutable, so the approved subset is derived from the decision history.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        from app.models.key_scene import SceneCandidate as CandidateRow

        from app.models.key_scene import (
            SceneCandidateSet as SetRow,
            SceneReviewDecision as DecisionRow,
        )

        set_row = session.get(SetRow, set_id)
        decision_rows = (
            session.query(DecisionRow)
            .filter(
                DecisionRow.owner_id == ids["owner_id"],
                DecisionRow.novel_id == ids["novel_id"],
                DecisionRow.set_id == set_id,
            )
            .order_by(DecisionRow.id.asc())
            .all()
        )
        from app.services.key_scenes.candidates import derive_candidate_review_states

        effective = derive_candidate_review_states(decision_rows)
        approved_keys = [key for key, state in effective.items() if state == "approved"]
        approved_rows = (
            session.query(CandidateRow)
            .filter(
                CandidateRow.owner_id == ids["owner_id"],
                CandidateRow.novel_id == ids["novel_id"],
                CandidateRow.set_id == set_id,
                CandidateRow.candidate_key.in_(approved_keys),
            )
            .order_by(CandidateRow.candidate_order.asc())
            .all()
        )
        recomputed = recompute_frozen_manifest_hash(
            set_row=set_row, approved_candidates=approved_rows
        )
        assert recomputed == frozen["manifest_hash"]
    engine.dispose()

    # GET frozen returns the same approved subset.
    frozen_get = await client.get(f"{base}/{set_id}/frozen", headers=headers)
    assert frozen_get.status_code == 200
    assert frozen_get.json()["manifest_hash"] == frozen["manifest_hash"]
    assert len(frozen_get.json()["candidates"]) == 1


async def test_freeze_is_idempotent(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fzi_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    ok = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(target["candidate_key"], action="approve"),
        headers=headers,
    )
    assert ok.status_code == 200

    first = await client.post(
        f"{base}/{set_id}/freeze", json=_freeze_payload(), headers=headers
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"{base}/{set_id}/freeze", json=_freeze_payload(), headers=headers
    )
    assert second.status_code == 200, second.text
    assert (
        second.json()["frozen"]["manifest_hash"]
        == first.json()["frozen"]["manifest_hash"]
    )

    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        count = (
            session.query(SceneReviewDecision)
            .filter_by(
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                set_id=set_id,
                action="approve",
                candidate_key=None,
            )
            .count()
        )
        assert count == 1  # append-only: re-freeze never adds a second decision
    engine.dispose()


async def test_candidate_review_blocked_after_freeze(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fzb_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    by_key = {c["candidate_key"]: c for c in set_view["candidates"]}
    approve_key, unresolved_key = list(by_key)[:2]

    ok = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(approve_key, action="approve"),
        headers=headers,
    )
    assert ok.status_code == 200
    freeze = await client.post(
        f"{base}/{set_id}/freeze", json=_freeze_payload(), headers=headers
    )
    assert freeze.status_code == 200

    # A frozen set is immutable: further candidate review fails closed.
    blocked = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(unresolved_key, action="approve"),
        headers=headers,
    )
    assert blocked.status_code == 409, blocked.text
    assert "frozen" in blocked.json()["detail"]


async def test_freeze_gate_rechecks_approved_candidate_evidence(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fze_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    ok = await client.post(
        f"{base}/{set_id}/review",
        json=_review_payload(target["candidate_key"], action="approve"),
        headers=headers,
    )
    assert ok.status_code == 200

    # Drift the approved candidate's evidence so freeze must fail closed.
    async with factory() as session:
        await session.execute(
            delete(SceneEvidenceRange).where(
                SceneEvidenceRange.owner_id == ids["owner_id"],
                SceneEvidenceRange.novel_id == ids["novel_id"],
                SceneEvidenceRange.set_id == set_id,
            )
        )
        await session.commit()

    resp = await client.post(
        f"{base}/{set_id}/freeze", json=_freeze_payload(), headers=headers
    )
    assert resp.status_code == 409, resp.text
    assert "evidence_missing" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Service-level seam
# ---------------------------------------------------------------------------


async def test_service_append_decision_and_freeze(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"svc_{uuid.uuid4().hex[:8]}")
    base, headers, set_view = await _generate_set(client, ids)
    set_id = set_view["id"]
    target = set_view["candidates"][0]

    decision = SceneReviewDecisionInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        set_id=set_id,
        decision_key=f"ds-svc-{uuid.uuid4().hex[:16]}",
        action=KeySceneReviewAction.APPROVE,
        actor_source="human",
        actor="owner",
        reason="服务级审查",
        from_review_state=KeySceneReviewState.CANDIDATE,
        candidate_key=target["candidate_key"],
    )
    async with factory() as session:
        review = KeySceneReviewService(session)
        set_row = await review.append_decision(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            decision=decision,
        )
        assert set_row.id == set_id
        await session.commit()

        frozen_row, frozen = await review.freeze(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            set_id=set_id,
            actor="owner",
            reason="服务级冻结",
        )
        assert frozen_row.review_state == "approved"
        assert [c.candidate_key for c in frozen.candidates] == [target["candidate_key"]]
        await session.rollback()
