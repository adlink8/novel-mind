"""PromptRevision review integration tests (Phase 32-04, REQ-VIS-03).

Covers the 32-VALIDATION.md review matrix:
- owner/novel/revision scope holds at the API boundary; cross-owner review and
  history access fail closed with an identical 404 (no owner leak);
- the review contract is append-only, explicit and idempotent: approve/reject
  move the projection, a repeated ``event_key`` replays without a second event
  and an illegal/out-of-state transition fails closed;
- the stale/hash approval gate blocks an approval for a prompt compiled
  against a superseded Visual Bible revision (fail closed, no silent reuse);
- approval only marks the PromptRevision as an approved Phase 33 input — the
  SceneSpec projection and the original source are never rewritten;
- the history endpoint surfaces events, staleness and approval-gate reason
  codes; the pure approval gate is unit-tested without a database.
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.scene_spec import (
    PROMPT_SCHEMA_VERSION,
    PromptRevisionContract,
    SpecReviewState,
    recompute_prompt_hash,
)
from app.services.prompt_compiler.revisions import (
    evaluate_prompt_approval_gate,
    recompute_input_hash_from_revision,
)
from tests.integration.scene_spec.test_scope import (
    _create_and_approve_visual_bible,
    _freeze_key_scene_set,
    _seed_owner,
    _snapshot_hash,
    _spec_preview_payload,
    _visual_bible_snapshot_hash,
    build_version_payload,
)

pytestmark = pytest.mark.integration

HEX64 = "a" * 64


def _hex(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_payload(
    *,
    action: str,
    from_review_state: str,
    event_key: str,
    reason: str = "人工审查",
) -> dict:
    return {
        "action": action,
        "actor_source": "human",
        "actor": "test-reviewer",
        "reason": reason,
        "event_key": event_key,
        "from_review_state": from_review_state,
    }


async def _seed_prompt_chain(
    client, sync_url: str, *, suffix: str, spec_key: str, prompt_key: str
) -> dict:
    """Seed owner → approved VB → frozen key scenes → scene spec → prompt.

    Returns the ids needed for prompt-review tests.
    """
    ids = _seed_owner(sync_url, suffix=suffix)
    headers = {"Authorization": f"Bearer {ids['token']}"}
    snapshot_hash = _snapshot_hash(ids)
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)
    vb = await _create_and_approve_visual_bible(
        client, ids, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        ids,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    spec_base = f"/api/novels/{ids['novel_id']}/scene-specs"
    created = await client.post(
        spec_base,
        json=_spec_preview_payload(frozen, spec_key=spec_key, vb_version_id=vb["id"]),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    spec_id = created.json()["spec"]["id"]

    prompt_base = f"/api/novels/{ids['novel_id']}/prompt-revisions"
    compiled = await client.post(
        prompt_base,
        json={
            "spec_id": spec_id,
            "prompt_key": prompt_key,
            "adapter_id": "mock-provider",
        },
        headers=headers,
    )
    assert compiled.status_code == 201, compiled.text
    revision_id = compiled.json()["revision"]["id"]
    return {
        "ids": ids,
        "headers": headers,
        "vb": vb,
        "frozen": frozen,
        "spec_id": spec_id,
        "revision_id": revision_id,
        "spec_base": spec_base,
        "prompt_base": prompt_base,
    }


# ---------------------------------------------------------------------------
# Pure approval gate (no database)
# ---------------------------------------------------------------------------


def _contract(**over):
    base = dict(
        schema_version=PROMPT_SCHEMA_VERSION,
        artifact_kind="prompt_revision",
        owner_id=1,
        novel_id=11,
        prompt_key="pk-1",
        revision_number=1,
        parent_prompt_revision_id=None,
        scene_spec_hash=HEX64,
        visual_bible_revision_hash=HEX64,
        source_snapshot_id="ss-main",
        source_snapshot_hash=HEX64,
        cutoff_chapter=3,
        schema_hash=HEX64,
        prompt_schema_hash=HEX64,
        compiler_version="1.0.0",
        adapter_id="mock-provider",
        adapter_version="1.0.0",
        config_hash=HEX64,
        input_hash="0" * 64,
        prompt_hash="0" * 64,
        sections={"subject": "Ayla by the keep"},
        negative_constraints=[],
        uncertainties=[],
        prompt_text="[subject]\nAyla by the keep",
        redacted_preview="[subject]\nAyla by the keep",
        review_state=SpecReviewState.CANDIDATE,
    )
    base.update(over)
    # model_construct bypasses field validation so the gate tests can inject
    # malformed hashes; the gate itself is the validator under test.
    contract = PromptRevisionContract.model_construct(**base)
    update: dict = {}
    if "input_hash" not in over:
        update["input_hash"] = recompute_input_hash_from_revision(contract)
    if "prompt_hash" not in over:
        update["prompt_hash"] = recompute_prompt_hash(contract)
    if update:
        contract = contract.model_copy(update=update)
    return contract


def test_approval_gate_rejects_stale_prompt():
    result = evaluate_prompt_approval_gate(revision=_contract(), stale=True)
    assert result.ok is False
    assert result.reason_code == "stale_prompt"


def test_approval_gate_rejects_malformed_hash():
    result = evaluate_prompt_approval_gate(
        revision=_contract(source_snapshot_hash="short"), stale=False
    )
    assert result.ok is False
    assert result.reason_code == "source_snapshot_hash_malformed"


def test_approval_gate_rejects_non_replayable_prompt_hash():
    result = evaluate_prompt_approval_gate(
        revision=_contract(prompt_hash="0" * 64), stale=False
    )
    assert result.ok is False
    assert result.reason_code == "prompt_hash_replay"


def test_approval_gate_rejects_input_hash_mismatch():
    result = evaluate_prompt_approval_gate(
        revision=_contract(input_hash="0" * 64), stale=False
    )
    assert result.ok is False
    assert result.reason_code == "input_hash_replay"


def test_approval_gate_rejects_hash_separation_violation():
    contract = _contract()
    same = contract.model_copy(update={"input_hash": contract.prompt_hash})
    result = evaluate_prompt_approval_gate(revision=same, stale=False)
    assert result.ok is False
    assert result.reason_code == "hash_separation"


def test_approval_gate_passes_fresh_replayable_prompt():
    contract = _contract()
    assert evaluate_prompt_approval_gate(revision=contract, stale=False).ok is True


def test_input_hash_replays_from_revision_lineage():
    contract = _contract()
    assert recompute_input_hash_from_revision(contract) == contract.input_hash


# ---------------------------------------------------------------------------
# Owner scope / IDOR matrix
# ---------------------------------------------------------------------------


async def test_cross_owner_review_and_history_are_404(api_client):
    client, _, sync_url = api_client
    chain_a = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"r_a_{uuid.uuid4().hex[:8]}",
        spec_key="spec-ra",
        prompt_key="pk-ra",
    )
    ids_b = _seed_owner(sync_url, suffix=f"r_b_{uuid.uuid4().hex[:8]}")
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}

    base_a = chain_a["prompt_base"]
    revision_id = chain_a["revision_id"]

    foreign_review = await client.post(
        f"{base_a}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="candidate",
            event_key=f"ev-{uuid.uuid4().hex[:8]}",
        ),
        headers=headers_b,
    )
    assert foreign_review.status_code == 404

    foreign_history = await client.get(
        f"{base_a}/{revision_id}/history", headers=headers_b
    )
    assert foreign_history.status_code == 404

    missing_novel = await client.get(
        "/api/novels/999999991/prompt-revisions", headers=headers_b
    )
    assert missing_novel.status_code == 404
    assert foreign_history.json() == missing_novel.json()


async def test_unauthenticated_review_route_rejects(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"r_anon_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_id']}/prompt-revisions"
    assert (await client.post(f"{base}/1/review", json={})).status_code == 401
    assert (await client.get(f"{base}/1/history")).status_code == 401


# ---------------------------------------------------------------------------
# Review contract: append-only, explicit, idempotent
# ---------------------------------------------------------------------------


async def test_approve_moves_projection_and_persists_event(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"rv_{uuid.uuid4().hex[:8]}",
        spec_key="spec-rv",
        prompt_key="pk-rv",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]

    history_before = await client.get(
        f"{base}/{revision_id}/history", headers=chain["headers"]
    )
    assert history_before.status_code == 200
    assert history_before.json()["revision"]["review_state"] == "candidate"
    assert history_before.json()["stale"] is False
    assert history_before.json()["review_events"] == []
    assert history_before.json()["approval_gate"] is not None
    assert history_before.json()["approval_gate"]["ok"] is True

    approved = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="candidate",
            event_key=f"ev-approve-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["revision"]["review_state"] == "approved"
    assert approved.json()["stale"] is False
    assert len(approved.json()["review_events"]) == 1
    event = approved.json()["review_events"][0]
    assert event["action"] == "approve"
    assert event["from_review_state"] == "candidate"
    assert event["to_review_state"] == "approved"
    # The server decided the legality; the client never supplies the result.
    assert event["actor"] == "test-reviewer"

    # History now carries the appended event.
    history_after = await client.get(
        f"{base}/{revision_id}/history", headers=chain["headers"]
    )
    assert len(history_after.json()["review_events"]) == 1


async def test_reject_moves_projection_and_stays_auditable(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"rj_{uuid.uuid4().hex[:8]}",
        spec_key="spec-rj",
        prompt_key="pk-rj",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]

    rejected = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="reject",
            from_review_state="candidate",
            event_key=f"ev-reject-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["revision"]["review_state"] == "rejected"

    # An approval after a rejection is an illegal transition → fail closed.
    blocked = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="rejected",
            event_key=f"ev-late-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert blocked.status_code == 409, blocked.text
    assert "illegal review action" in blocked.json()["detail"]


async def test_from_review_state_mismatch_fails_closed(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"fs_{uuid.uuid4().hex[:8]}",
        spec_key="spec-fs",
        prompt_key="pk-fs",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]

    mismatched = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="approved",
            event_key=f"ev-mismatch-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert mismatched.status_code == 409, mismatched.text
    assert "does not match" in mismatched.json()["detail"]

    # No event was appended by the failed action.
    history = await client.get(
        f"{base}/{revision_id}/history", headers=chain["headers"]
    )
    assert history.json()["revision"]["review_state"] == "candidate"
    assert history.json()["review_events"] == []


async def test_duplicate_event_key_replays_without_second_approval(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"id_{uuid.uuid4().hex[:8]}",
        spec_key="spec-id",
        prompt_key="pk-id",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]
    event_key = f"ev-dup-{uuid.uuid4().hex[:8]}"

    first = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve", from_review_state="candidate", event_key=event_key
        ),
        headers=chain["headers"],
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"]["review_state"] == "approved"

    # Retrying the SAME event_key must replay, never append a second approval.
    second = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve", from_review_state="candidate", event_key=event_key
        ),
        headers=chain["headers"],
    )
    assert second.status_code == 200, second.text
    assert second.json()["revision"]["review_state"] == "approved"
    assert len(second.json()["review_events"]) == 1


async def test_supersede_is_legal_from_approved(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"su_{uuid.uuid4().hex[:8]}",
        spec_key="spec-su",
        prompt_key="pk-su",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]

    approved = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="candidate",
            event_key=f"ev-a-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert approved.status_code == 200

    superseded = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="supersede",
            from_review_state="approved",
            event_key=f"ev-s-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert superseded.status_code == 200, superseded.text
    assert superseded.json()["revision"]["review_state"] == "superseded"
    assert len(superseded.json()["review_events"]) == 2


# ---------------------------------------------------------------------------
# Stale / hash approval gate
# ---------------------------------------------------------------------------


async def test_stale_prompt_approval_fails_closed(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"st_{uuid.uuid4().hex[:8]}",
        spec_key="spec-st",
        prompt_key="pk-st",
    )
    ids = chain["ids"]
    headers = chain["headers"]
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]
    vb_snapshot_hash = _visual_bible_snapshot_hash(ids)

    # Approve a NEW Visual Bible revision (different version_key + style).
    vb2_payload = build_version_payload(
        ids,
        snapshot_hash=vb_snapshot_hash,
        snapshot_id="ss-main",
        cutoff_chapter=3,
        version_key="vb-v2",
    )
    vb2_payload["version"]["style_profile"] = {
        "palette": "warm tones",
        "lighting": "golden hour",
    }
    from app.schemas.visual_bible import (
        VisualBibleVersionContract,
        recompute_manifest_hash,
    )

    vb2_version = VisualBibleVersionContract.model_validate(vb2_payload["version"])
    vb2_version = vb2_version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(vb2_version)}
    )
    vb2_payload["version"] = vb2_version.model_dump(mode="json")
    vb2_created = await client.post(
        f"/api/novels/{ids['novel_id']}/visual-bible",
        json=vb2_payload,
        headers=headers,
    )
    assert vb2_created.status_code == 201, vb2_created.text
    vb2_id = vb2_created.json()["version"]["id"]
    approved_v2 = await client.post(
        f"/api/novels/{ids['novel_id']}/visual-bible/{vb2_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "test-reviewer",
            "reason": "approve v2",
            "event_key": f"approve-v2-{uuid.uuid4().hex[:8]}",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert approved_v2.status_code == 200, approved_v2.text

    # The prompt was compiled against the OLD revision → now stale.
    history = await client.get(f"{base}/{revision_id}/history", headers=headers)
    assert history.status_code == 200
    assert history.json()["stale"] is True

    # Approval of a stale prompt must fail closed with a stable reason code.
    blocked = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="candidate",
            event_key=f"ev-stale-{uuid.uuid4().hex[:8]}",
        ),
        headers=headers,
    )
    assert blocked.status_code == 409, blocked.text
    assert "stale_prompt" in blocked.json()["detail"]

    # No event was appended; the candidate is still unreviewed.
    history_after = await client.get(f"{base}/{revision_id}/history", headers=headers)
    assert history_after.json()["revision"]["review_state"] == "candidate"
    assert history_after.json()["review_events"] == []
    assert history_after.json()["approval_gate"]["ok"] is False
    assert history_after.json()["approval_gate"]["reason_code"] == "stale_prompt"


# ---------------------------------------------------------------------------
# Approval is an approved input only; never rewrites SceneSpec or source
# ---------------------------------------------------------------------------


async def test_approval_never_rewrites_scene_spec_or_source(api_client):
    client, factory, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"np_{uuid.uuid4().hex[:8]}",
        spec_key="spec-np",
        prompt_key="pk-np",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]

    approved = await client.post(
        f"{base}/{revision_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="candidate",
            event_key=f"ev-np-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["revision"]["review_state"] == "approved"

    # The persisted SceneSpec stays a candidate — approval does not promote it.
    spec_detail = await client.get(
        f"{chain['spec_base']}/{chain['spec_id']}", headers=chain["headers"]
    )
    assert spec_detail.status_code == 200
    assert spec_detail.json()["spec"]["review_state"] == "candidate"

    # Authoritative chapter body is untouched.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import NullPool

    from app.models.novel import Chapter

    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        stored = session.get(Chapter, chain["ids"]["chapter_ids"][0])
        assert stored.content == chain["ids"]["contents"][0]
    engine.dispose()

    # The prompt revision row is immutable except its review_state projection:
    # the scene_spec_hash/prompt_hash columns are untouched.
    from sqlalchemy import select

    from app.models.prompt_revision import PromptRevision

    async with factory() as session:
        row = await session.scalar(
            select(PromptRevision).where(PromptRevision.id == revision_id)
        )
        assert row is not None
        assert row.review_state == "approved"
        assert len(row.scene_spec_hash) == 64
        assert len(row.prompt_hash) == 64
        await session.rollback()


# ---------------------------------------------------------------------------
# Edited candidate chain: explicit edit produces a fresh approvable revision
# ---------------------------------------------------------------------------


async def test_edited_candidate_can_be_approved(api_client):
    client, _, sync_url = api_client
    chain = await _seed_prompt_chain(
        client,
        sync_url,
        suffix=f"ed_{uuid.uuid4().hex[:8]}",
        spec_key="spec-ed",
        prompt_key="pk-ed",
    )
    base = chain["prompt_base"]
    revision_id = chain["revision_id"]
    prompt_key = f"pk-ed-child-{uuid.uuid4().hex[:8]}"

    edited = await client.post(
        f"{base}/{revision_id}/edit",
        json={
            "prompt_key": prompt_key,
            "detail_key": "user-lighting",
            "kind": "style",
            "text": "冷色调的顶光，强调斗篷的暗部",
            "author": "test-editor",
            "rationale": "人工补充光影解读",
        },
        headers=chain["headers"],
    )
    assert edited.status_code == 201, edited.text
    child_id = edited.json()["revision"]["id"]
    assert edited.json()["revision"]["parent_prompt_revision_id"] == revision_id
    assert edited.json()["revision"]["revision_number"] == 2
    assert edited.json()["diff"]["same"] is False

    # The edited candidate is fresh (same lineage) and approvable.
    history = await client.get(f"{base}/{child_id}/history", headers=chain["headers"])
    assert history.status_code == 200
    assert history.json()["stale"] is False
    assert history.json()["approval_gate"]["ok"] is True

    approved = await client.post(
        f"{base}/{child_id}/review",
        json=_review_payload(
            action="approve",
            from_review_state="candidate",
            event_key=f"ev-ed-{uuid.uuid4().hex[:8]}",
        ),
        headers=chain["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["revision"]["review_state"] == "approved"

    # The base candidate stays a candidate — edits never mutate the parent.
    base_history = await client.get(
        f"{base}/{revision_id}/history", headers=chain["headers"]
    )
    assert base_history.json()["revision"]["review_state"] == "candidate"
