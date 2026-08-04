"""Phase 38-05 Agent boundary: publish_derivative_visual approval -> deterministic review seam.

The Phase 38 deterministic domain services (38-01..38-04:
``fork`` / ``scene_spec`` / ``assets`` / ``consistency`` / ``review`` /
``published_assets``) stay free of the Agent approval vocabulary. This module is
the **38-05** agent-boundary layer that binds those capabilities to the
versioned ``illustrate-derivative-scene`` Skill:

- ``request_publish_derivative_visual`` — the ``publish_derivative_visual``
  action: for **one** stored derivative candidate asset creates one pending Web
  ApprovalRequest whose ``payload_hash`` binds the candidate's frozen lineage
  (``asset_id`` / ``content_hash`` / ``scene_spec_hash`` /
  ``divergence_manifest_hash`` / ``consistency_verdict`` /
  ``source_snapshot_hash`` / ``fork_id``). A blocked candidate (identity drift /
  undeclared divergence) can never be requested; a wrong owner/novel/fork scope
  fails closed.
- ``consume_publish_derivative_visual_approval`` — the deterministic publisher:
  the only Agent-path write, it verifies the approved publish approval + identical
  payload hash + fork scope before delegating to the deterministic review seam
  (``review_candidate_asset`` -> ``apply_derivative_asset_review``) that moves the
  candidate ``review_state`` to ``approved`` — the asset becomes reader-visible
  through ``published_assets``.

Rules (D-38-03/D-38-04 / REQ-FORK-04 / REQ-AGENT-02/03/04/07):

- Agent output is candidate-only; publication requires an independent
  ``publish_derivative_visual`` Web ApprovalRequest consumed by the deterministic
  review seam.
- The ApprovalRequest binds the candidate's frozen lineage; a forged/stale
  payload hash, a wrong fork scope or a non-approved status fails closed.
- The Original Visual Bible rows are immutable (REQ-FORK-04): no approval and no
  publisher ever writes an Original row.
- A ``blocked`` candidate (identity drift / undeclared divergence) has an empty
  legal review transition set — the review seam can never approve it.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import ApprovalRequest
from app.models.derivative_visual import DerivativeVisualCandidateAsset
from app.schemas.derivative_visual_asset import (
    DerivativeAssetReviewAction,
    DerivativeAssetReviewEventInput,
    DerivativeVisualAssetState,
    PublishedDerivativeVisualAsset,
)
from app.services.derivative_visual.assets import (
    DerivativeCandidateScopeError,
    load_candidate,
)
from app.services.derivative_visual.review import (
    DerivativeReviewSeamError,
    review_candidate_asset,
)

# Phase 38-05 Agent approval action (versioned illustrate-derivative-scene Skill).
PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION = "publish_derivative_visual"

# Prefix for the approval hash binding (byte-replayable).
APPROVAL_HASH_PREFIX = "derivative-visual.v1:approval"

# Stable fail-closed codes for the agent approval / publish boundary.
CODE_APPROVAL_NOT_FOUND = "approval_not_found"
CODE_APPROVAL_NOT_APPROVED = "approval_not_approved"
CODE_APPROVAL_HASH_MISMATCH = "approval_hash_mismatch"
CODE_CANDIDATE_NOT_APPROVABLE = "candidate_not_approvable"
CODE_CANDIDATE_SCOPE_MISMATCH = "candidate_scope_mismatch"
CODE_SCENE_SPEC_HASH_MISMATCH = "scene_spec_hash_mismatch"
CODE_FORK_SCOPE_MISMATCH = "fork_scope_mismatch"
CODE_REVIEW_BLOCKED = "review_blocked"

# A candidate can only be published from an explicitly approvable review state;
# ``blocked`` (identity drift / undeclared divergence) is terminal and can never
# be approved (D-38-03 / LEGAL_DERIVATIVE_ASSET_TRANSITIONS).
APPROVABLE_CANDIDATE_STATES: frozenset[str] = frozenset(
    {"candidate", "needs_review"}
)


class DerivativeVisualBoundaryError(ValueError):
    """Fail-closed 38-05 boundary violation (stable code for the facade)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def derivative_visual_approval_payload_hash(
    candidate: DerivativeVisualCandidateAsset,
) -> str:
    """Canonical ApprovalRequest payload hash binding the candidate's frozen lineage.

    Byte-replayable from the persisted candidate row; the 38-05
    ``publish_derivative_visual`` ApprovalRequest and the deterministic review
    seam bind the exact same value so forged/stale approval and payload drift
    fail closed.
    """
    payload = {
        "candidate_id": candidate.id,
        "asset_id": candidate.asset_id,
        "asset_key": candidate.asset_key,
        "content_hash": candidate.content_hash,
        "scene_spec_hash": candidate.scene_spec_hash,
        "divergence_manifest_hash": candidate.divergence_manifest_hash,
        "consistency_verdict": candidate.consistency_verdict,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "fork_id": candidate.fork_id,
        "review_state": candidate.review_state,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(f"{APPROVAL_HASH_PREFIX}\n".encode("utf-8") + encoded).hexdigest()


async def find_publish_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    fork_id: int,
    payload_hash: str,
) -> ApprovalRequest | None:
    """Latest ``publish_derivative_visual`` ApprovalRequest with the identical hash."""
    return await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.owner_id == owner_id,
            ApprovalRequest.action == PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION,
            ApprovalRequest.fork_id == fork_id,
            ApprovalRequest.payload_hash == payload_hash,
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


async def request_publish_derivative_visual(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    candidate_asset_id: int,
    scene_spec_hash: str,
    actor_id: int,
    approval_note: str | None = None,
    branch: str | None = None,
    fork: str | None = None,
    run_id: int | None = None,
    skill_version_id: int | None = None,
    artifact_id: int | None = None,
    artifact_revision_id: int | None = None,
) -> dict[str, Any]:
    """Server-authoritative ``publish_derivative_visual`` action (38-05, candidate-only).

    Loads one stored derivative candidate asset in the owner/novel scope, verifies
    its frozen Scene Spec lineage and that its review state is approvable
    (``candidate`` / ``needs_review``; a ``blocked`` candidate can never be
    published), then creates one pending Web ApprovalRequest (action=
    ``publish_derivative_visual``) whose payload_hash binds the candidate's frozen
    lineage (D-11/D-15). Same candidate + identical lineage replay the existing
    approval (idempotent). It never publishes and never writes an Original row —
    only an approved approval consumed by ``consume_publish_derivative_visual_approval``
    moves the candidate to ``approved`` via the deterministic review seam.
    """
    try:
        candidate = await load_candidate(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_id=candidate_asset_id,
        )
    except DerivativeCandidateScopeError as exc:
        raise DerivativeVisualBoundaryError(
            CODE_CANDIDATE_SCOPE_MISMATCH, str(exc)
        ) from None

    if candidate.scene_spec_hash != scene_spec_hash:
        raise DerivativeVisualBoundaryError(
            CODE_SCENE_SPEC_HASH_MISMATCH,
            "supplied scene_spec_hash does not replay the candidate's frozen "
            "Scene Spec lineage",
        )
    if candidate.review_state not in APPROVABLE_CANDIDATE_STATES:
        raise DerivativeVisualBoundaryError(
            CODE_CANDIDATE_NOT_APPROVABLE,
            f"candidate {candidate.id} is {candidate.review_state!r}; only "
            "candidate/needs_review states can be published (a blocked candidate "
            "can never be approved)",
        )

    payload_hash = derivative_visual_approval_payload_hash(candidate)

    # 幂等重放：同一候选 + 相同 frozen lineage 已存在 pending/decided approval →
    # 复用既有 approval（same-hash replay，一次 approval）。
    existing = await find_publish_approval(
        db,
        owner_id=owner_id,
        fork_id=candidate.fork_id,
        payload_hash=payload_hash,
    )
    if existing is not None:
        return _approval_view_for_tool(candidate, existing, replayed=True)

    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=run_id,
        skill_version_id=skill_version_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
        novel_id=novel_id,
        branch_id=None,
        fork_id=candidate.fork_id,
        action=PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION,
        payload_summary={
            "candidate_id": candidate.id,
            "asset_id": candidate.asset_id,
            "asset_key": candidate.asset_key,
            "fork_id": candidate.fork_id,
            "visual_version_id": candidate.visual_version_id,
            "content_hash": candidate.content_hash,
            "scene_spec_hash": candidate.scene_spec_hash,
            "divergence_manifest_hash": candidate.divergence_manifest_hash,
            "consistency_verdict": candidate.consistency_verdict,
            "source_snapshot_hash": candidate.source_snapshot_hash,
            "branch": branch,
            "fork": fork,
            "approval_note": (approval_note or "")[:400],
        },
        payload_hash=payload_hash,
        status="pending",
        expires_at=None,
    )
    db.add(approval)
    await db.flush()
    return _approval_view_for_tool(candidate, approval, replayed=False)


async def consume_publish_derivative_visual_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    candidate_asset_id: int,
    approval_id: int,
    reason: str,
    actor_id: int,
) -> PublishedDerivativeVisualAsset:
    """Deterministic publisher: consume an approved publish_derivative_visual approval.

    This is the **only** Agent-path write the publication may take
    (D-38-03/D-38-04). It verifies, in order:

    - the ``publish_derivative_visual`` ApprovalRequest exists, is owned and is
      ``approved``;
    - the candidate still exists in the owner/novel scope;
    - the candidate ``fork_id`` matches the approval ``fork_id`` (wrong branch/
      fork scope -> fail closed);
    - the approval payload still replays the candidate's frozen lineage
      (forged/stale approval -> fail closed).

    Only then does it delegate to the deterministic review seam
    (``review_candidate_asset`` -> ``apply_derivative_asset_review``) which moves
    the candidate ``review_state`` to ``approved`` and returns the published
    asset envelope. It never writes Original Visual Bible rows and never promotes
    a pointer (REQ-FORK-04).
    """
    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
    if approval is None:
        raise DerivativeVisualBoundaryError(
            CODE_APPROVAL_NOT_FOUND,
            "publish_derivative_visual approval not found in the owner scope",
        )
    if approval.action != PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION:
        raise DerivativeVisualBoundaryError(
            CODE_APPROVAL_NOT_FOUND,
            f"approval {approval.id} is {approval.action!r}; not a "
            "publish_derivative_visual approval",
        )
    if approval.status != "approved":
        raise DerivativeVisualBoundaryError(
            CODE_APPROVAL_NOT_APPROVED,
            f"publish approval {approval.id} is {approval.status!r}; only an "
            "approved publish approval can be consumed",
        )

    try:
        candidate = await load_candidate(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_id=candidate_asset_id,
        )
    except DerivativeCandidateScopeError as exc:
        raise DerivativeVisualBoundaryError(
            CODE_CANDIDATE_SCOPE_MISMATCH, str(exc)
        ) from None

    if candidate.fork_id != approval.fork_id:
        raise DerivativeVisualBoundaryError(
            CODE_FORK_SCOPE_MISMATCH,
            f"approval fork_id {approval.fork_id} does not match the candidate "
            f"fork_id {candidate.fork_id} (wrong fork scope)",
        )
    if derivative_visual_approval_payload_hash(candidate) != approval.payload_hash:
        raise DerivativeVisualBoundaryError(
            CODE_APPROVAL_HASH_MISMATCH,
            "publish approval payload no longer replays the candidate's frozen "
            "lineage (forged/stale approval)",
        )

    # Delegate to the deterministic review seam: explicit approve transition
    # whose legality the candidate state machine owns (a blocked candidate has an
    # empty legal transition set and fails closed here).
    event = DerivativeAssetReviewEventInput(
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_id=candidate.id,
        action=DerivativeAssetReviewAction.APPROVE,
        actor_source="machine",
        actor=f"deterministic_publisher:{actor_id}",
        reason=reason or "publish_derivative_visual approved",
        event_key=f"publish_derivative_visual:{approval.id}",
        from_review_state=DerivativeVisualAssetState(candidate.review_state),
        details={
            "approval_id": approval.id,
            "approval_action": approval.action,
            "approval_payload_hash": approval.payload_hash,
        },
    )
    try:
        result = await review_candidate_asset(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            event=event,
        )
    except DerivativeReviewSeamError as exc:
        raise DerivativeVisualBoundaryError(CODE_REVIEW_BLOCKED, str(exc)) from exc
    if not isinstance(result, PublishedDerivativeVisualAsset):
        raise DerivativeVisualBoundaryError(
            CODE_REVIEW_BLOCKED,
            f"review seam did not publish candidate {candidate.id}; state is "
            f"{result.review.review_state.value!r}",
        )
    return result


def _approval_view_for_tool(
    candidate: DerivativeVisualCandidateAsset,
    approval: ApprovalRequest,
    *,
    replayed: bool,
) -> dict[str, Any]:
    """Candidate + ApprovalRequest ORM -> JSON-safe tool response.

    candidate-only：review_state 恒为 candidate/needs_review、绝不 approved/
    published；approval_request_id / payload_hash 供 Web 审批轮询与确定性 review
    seam 引用。
    """
    return {
        "candidate_asset_id": candidate.id,
        "owner_id": candidate.owner_id,
        "novel_id": candidate.novel_id,
        "fork_id": candidate.fork_id,
        "visual_version_id": candidate.visual_version_id,
        "asset_id": candidate.asset_id,
        "asset_key": candidate.asset_key,
        "content_hash": candidate.content_hash,
        "scene_spec_hash": candidate.scene_spec_hash,
        "divergence_manifest_hash": candidate.divergence_manifest_hash,
        "consistency_verdict": candidate.consistency_verdict,
        "review_state": candidate.review_state,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "status": "candidate",
        "candidate_only": True,
        "replayed": bool(replayed),
    }


__all__ = [
    "APPROVABLE_CANDIDATE_STATES",
    "APPROVAL_HASH_PREFIX",
    "CODE_APPROVAL_HASH_MISMATCH",
    "CODE_APPROVAL_NOT_APPROVED",
    "CODE_APPROVAL_NOT_FOUND",
    "CODE_CANDIDATE_NOT_APPROVABLE",
    "CODE_CANDIDATE_SCOPE_MISMATCH",
    "CODE_FORK_SCOPE_MISMATCH",
    "CODE_REVIEW_BLOCKED",
    "CODE_SCENE_SPEC_HASH_MISMATCH",
    "DerivativeVisualBoundaryError",
    "PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION",
    "consume_publish_derivative_visual_approval",
    "derivative_visual_approval_payload_hash",
    "find_publish_approval",
    "request_publish_derivative_visual",
]
