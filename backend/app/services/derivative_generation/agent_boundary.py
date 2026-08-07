"""Phase 37-05 Agent boundary: divergence approval -> revalidation -> publish.

The Phase 37 deterministic domain services (37-01..37-04:
``context_package`` / ``runner`` / ``gates`` / ``overrides`` /
``published_revision``) stay free of the Agent approval vocabulary. This module
is the **37-05** agent-boundary layer that binds those capabilities to the
versioned ``continue-derivative-story`` Skill:

- ``request_divergence_override`` — the ``allow_divergence`` action: creates one
  pending ``DerivativeOverride`` + one pending Web ApprovalRequest whose
  ``payload_hash`` binds the exact ``draft_hash`` + ``canon_delta_hash``.
- ``request_publish_derivative_revision`` — the ``publish_derivative_revision``
  action: only after an approved ``allow_divergence`` approval with the
  **identical** payload hash and a successful full revalidation creates a
  separate pending Web ApprovalRequest bound to the same exact hashes.
- ``consume_publish_approval`` — the deterministic revision publisher: the only
  Agent-path write, it verifies the approved publish approval + identical hash
  binding + approved divergence step before delegating to the deterministic
  ``overrides.approve_override`` materializer.

Rules (D-37-03/D-37-05 / REQ-FORK-03 / REQ-FORK-06 / REQ-AGENT-02/03/04/07):

- Agent output is candidate-only; a divergence request requires an
  ``allow_divergence`` approval and **full revalidation** before a separate
  ``publish_derivative_revision`` approval, consumed in that order.
- Both ApprovalRequests bind the same exact ``draft_hash`` and
  ``canon_delta_hash``; approval reuse, hash drift or a skipped step fails
  closed.
- Neither approval may write Original Canon; the deterministic publisher
  materializes into a Fanfiction Canon ``derivative_revisions`` row only.
- BranchSuggestion stays disabled-by-default and never auto-forks or reuses
  either approval (enforced by candidate.py / gates.py / schemas).
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import ApprovalRequest
from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import (
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.models.derivative_override import DerivativeOverride
from app.services.derivative_generation.candidate import (
    CANDIDATE_HASH_PREFIX,
    CandidateDraft,
    GateVerdict,
    apply_deterministic_gates,
)
from app.services.derivative_generation.context_package import (
    verify_package_hash,
)
from app.services.derivative_generation.overrides import (
    CODE_ALREADY_DECIDED,
    CODE_CANDIDATE_NOT_FOUND,
    CODE_PACKAGE_HASH_MISMATCH,
    OverrideApprovalResult,
    OverrideError,
    approve_override,
    create_override,
    load_candidate_for_agent,
    load_override_for_agent,
)

# Phase 37-05 Agent approval actions (versioned continue-derivative-story Skill).
ALLOW_DIVERGENCE_APPROVAL_ACTION = "allow_divergence"
PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION = "publish_derivative_revision"

# Prefix for the identical-hash ApprovalRequest binding (byte-replayable).
APPROVAL_HASH_PREFIX = "derivative-divergence.v1:approval"

# Stable fail-closed codes for the agent approval / publish boundary.
CODE_APPROVAL_NOT_FOUND = "approval_not_found"
CODE_APPROVAL_NOT_APPROVED = "approval_not_approved"
CODE_APPROVAL_HASH_MISMATCH = "approval_hash_mismatch"
CODE_REVALIDATION_FAILED = "revalidation_failed"


def draft_hash_for_candidate(candidate: DerivativeGenerationCandidate) -> str:
    """Canonical draft hash over the candidate's frozen structured output.

    Byte-replayable from the persisted candidate row; the two Phase 37-05
    ApprovalRequests (``allow_divergence`` and ``publish_derivative_revision``)
    bind the exact same value so approval reuse / hash drift fail closed.
    """
    payload = {
        "schema_version": candidate.schema_version,
        "intent": candidate.intent,
        "draft_text": candidate.draft_text,
        "summary": candidate.summary,
        "citation_keys": list(candidate.citation_keys or []),
        "divergence": dict(candidate.divergence or {}),
        "branch_suggestions": list(candidate.branch_suggestions or []),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(f"{CANDIDATE_HASH_PREFIX}\n".encode("utf-8") + encoded).hexdigest()


def rebuild_candidate_draft(candidate: DerivativeGenerationCandidate) -> CandidateDraft:
    """Rebuild the strict CandidateDraft from the persisted row (for revalidation)."""
    return CandidateDraft(
        schema_version=candidate.schema_version,
        intent=candidate.intent,
        draft_text=candidate.draft_text,
        summary=candidate.summary,
        citation_keys=list(candidate.citation_keys or []),
        divergence=dict(candidate.divergence) if candidate.divergence else None,
        branch_suggestions=list(candidate.branch_suggestions or []),
    )


def divergence_approval_payload_hash(*, draft_hash: str, canon_delta_hash: str) -> str:
    """Canonical ApprovalRequest payload hash binding exact draft + delta hashes.

    Both Phase 37-05 approvals must carry the identical payload_hash; a
    mismatch or a skipped step fails closed (identical-hash binding, D-37-03).
    """
    encoded = json.dumps(
        {"draft_hash": draft_hash, "canon_delta_hash": canon_delta_hash},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(f"{APPROVAL_HASH_PREFIX}\n".encode("utf-8") + encoded).hexdigest()


async def find_approved_divergence_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    fork_id: int,
    payload_hash: str,
) -> ApprovalRequest | None:
    """Find the approved ``allow_divergence`` ApprovalRequest with identical hashes.

    Returns the latest approved divergence approval whose payload_hash exactly
    equals the publish approval's; ``None`` means the divergence step was
    skipped or not approved (fail closed).
    """
    return await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.owner_id == owner_id,
            ApprovalRequest.action == ALLOW_DIVERGENCE_APPROVAL_ACTION,
            ApprovalRequest.fork_id == fork_id,
            ApprovalRequest.payload_hash == payload_hash,
            ApprovalRequest.status == "approved",
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


async def find_override_for_candidate(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    candidate_id: int,
) -> DerivativeOverride | None:
    """Latest override row for one candidate (idempotent replay lookup)."""
    return await db.scalar(
        select(DerivativeOverride)
        .where(
            DerivativeOverride.owner_id == owner_id,
            DerivativeOverride.novel_id == novel_id,
            DerivativeOverride.candidate_id == candidate_id,
        )
        .order_by(DerivativeOverride.id.desc())
        .limit(1)
    )


async def find_allow_divergence_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    fork_id: int,
    payload_hash: str,
) -> ApprovalRequest | None:
    """Latest ``allow_divergence`` ApprovalRequest with the identical payload hash."""
    return await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.owner_id == owner_id,
            ApprovalRequest.action == ALLOW_DIVERGENCE_APPROVAL_ACTION,
            ApprovalRequest.fork_id == fork_id,
            ApprovalRequest.payload_hash == payload_hash,
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


async def revalidate_approved_divergence(
    db: AsyncSession,
    *,
    candidate: DerivativeGenerationCandidate,
) -> None:
    """Rerun the deterministic gates against the sealed package (full revalidation).

    D-37-03: after the ``allow_divergence`` approval, the same candidate is
    revalidated against its frozen package before any publish approval may be
    created. A blocked/mismatched result fails closed (``revalidation_failed``).
    """
    job = await db.scalar(
        select(DerivativeGenerationJob).where(
            DerivativeGenerationJob.id == candidate.job_id
        )
    )
    if job is None:
        raise OverrideError(
            CODE_CANDIDATE_NOT_FOUND,
            "generation job lineage disappeared",
            status_code=409,
        )
    package = await db.scalar(
        select(ContextPackageRecord).where(
            ContextPackageRecord.id == job.context_package_id,
            ContextPackageRecord.owner_id == candidate.owner_id,
            ContextPackageRecord.novel_id == candidate.novel_id,
        )
    )
    if package is None:
        raise OverrideError(
            CODE_PACKAGE_HASH_MISMATCH,
            "sealed context package disappeared from the owner/novel scope",
            status_code=404,
        )
    payload = dict(package.canonical_payload or {})
    try:
        verify_package_hash(payload, job.package_hash)
    except Exception as exc:  # noqa: BLE001 - stable reason required
        raise OverrideError(
            CODE_PACKAGE_HASH_MISMATCH,
            f"package hash no longer replays: {exc}",
            status_code=409,
        ) from exc
    draft = rebuild_candidate_draft(candidate)
    result = apply_deterministic_gates(
        draft,
        payload,
        expected_package_hash=job.package_hash,
        package_intent=job.intent,
    )
    if result.verdict is not GateVerdict.NEEDS_OVERRIDE:
        raise OverrideError(
            CODE_REVALIDATION_FAILED,
            f"revalidation verdict {result.verdict.value!r} (reason "
            f"{result.reason!r}); expected needs_override for the approved "
            "divergence — no publish approval is issued",
            status_code=409,
        )


async def request_divergence_override(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    candidate_id: int,
    reason: str,
    draft_hash: str,
    canon_delta_hash: str,
    actor_id: int,
    affected_evidence: list[str] | None = None,
    kind: str | None = None,
    branch: str | None = None,
    fork: str | None = None,
    run_id: int | None = None,
    skill_version_id: int | None = None,
    artifact_id: int | None = None,
    artifact_revision_id: int | None = None,
) -> dict[str, Any]:
    """Server-authoritative ``allow_divergence`` action (37-05, candidate-only).

    Creates one pending ``DerivativeOverride`` + one pending Web
    ApprovalRequest (action=``allow_divergence``) whose payload_hash binds the
    exact ``draft_hash`` + ``canon_delta_hash`` (D-11/D-15). The supplied
    hashes must replay the candidate's frozen lineage (drift fails closed).
    Same candidate + identical hashes replay the existing override and approval
    (idempotent). It never publishes and never writes Original Canon — only a
    separate ``publish_derivative_revision`` approval consumed by
    ``consume_publish_approval`` materializes.
    """
    candidate = await load_candidate_for_agent(
        db, owner_id=owner_id, novel_id=novel_id, candidate_id=candidate_id
    )
    if draft_hash_for_candidate(candidate) != draft_hash:
        raise OverrideError(
            "draft_hash_mismatch",
            "supplied draft_hash does not replay the candidate's frozen "
            "structured output",
        )
    payload_hash = divergence_approval_payload_hash(
        draft_hash=draft_hash, canon_delta_hash=canon_delta_hash
    )
    # 幂等重放：同一候选已存在 override 且 canon_delta_hash 一致 → 复用既有
    # override + 既有 allow_divergence approval（same-hash replay，一次 approval）。
    existing = await find_override_for_candidate(
        db, owner_id=owner_id, novel_id=novel_id, candidate_id=candidate.id
    )
    if existing is not None:
        if existing.canon_delta_hash != canon_delta_hash:
            raise OverrideError(
                "canon_delta_hash_mismatch",
                "candidate already has an override bound to a different "
                "CanonDelta lineage",
            )
        approval = await find_allow_divergence_approval(
            db, owner_id=owner_id, fork_id=existing.fork_id, payload_hash=payload_hash
        )
        replayed = approval is not None
        if approval is None:
            approval = ApprovalRequest(
                owner_id=owner_id,
                run_id=run_id,
                skill_version_id=skill_version_id,
                artifact_id=artifact_id,
                artifact_revision_id=artifact_revision_id,
                novel_id=novel_id,
                branch_id=None,
                fork_id=existing.fork_id,
                action=ALLOW_DIVERGENCE_APPROVAL_ACTION,
                payload_summary={
                    "candidate_id": candidate.id,
                    "job_id": candidate.job_id,
                    "override_id": existing.id,
                    "project_id": existing.project_id,
                    "chapter_id": existing.chapter_id,
                    "fork_id": existing.fork_id,
                    "draft_hash": draft_hash,
                    "canon_delta_hash": canon_delta_hash,
                    "branch": branch,
                    "fork": fork,
                    "reason": reason[:400],
                },
                payload_hash=payload_hash,
                status="pending",
                expires_at=None,
            )
            db.add(approval)
            await db.flush()
        return {
            "override_id": existing.id,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "project_id": existing.project_id,
            "chapter_id": existing.chapter_id,
            "fork_id": existing.fork_id,
            "candidate_id": candidate.id,
            "job_id": candidate.job_id,
            "kind": existing.kind,
            "canon_delta_hash": existing.canon_delta_hash,
            "approval_request_id": approval.id,
            "approval_action": approval.action,
            "approval_status": approval.status,
            "approval_payload_hash": approval.payload_hash,
            "status": "candidate",
            "candidate_only": True,
            "replayed": replayed,
        }
    override = await create_override(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        chapter_id=chapter_id,
        candidate_id=candidate_id,
        reason=reason,
        affected_evidence=list(affected_evidence or []),
        kind=kind,
        actor_id=actor_id,
    )
    if override.canon_delta_hash != canon_delta_hash:
        raise OverrideError(
            "canon_delta_hash_mismatch",
            "supplied canon_delta_hash does not replay the override's frozen "
            "CanonDelta lineage",
        )
    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=run_id,
        skill_version_id=skill_version_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
        novel_id=novel_id,
        branch_id=None,
        fork_id=override.fork_id,
        action=ALLOW_DIVERGENCE_APPROVAL_ACTION,
        payload_summary={
            "candidate_id": candidate.id,
            "job_id": candidate.job_id,
            "override_id": override.id,
            "project_id": override.project_id,
            "chapter_id": override.chapter_id,
            "fork_id": override.fork_id,
            "draft_hash": draft_hash,
            "canon_delta_hash": canon_delta_hash,
            "branch": branch,
            "fork": fork,
            "reason": reason[:400],
        },
        payload_hash=payload_hash,
        status="pending",
        expires_at=None,
    )
    db.add(approval)
    await db.flush()
    return {
        "override_id": override.id,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "project_id": override.project_id,
        "chapter_id": override.chapter_id,
        "fork_id": override.fork_id,
        "candidate_id": candidate.id,
        "job_id": candidate.job_id,
        "kind": override.kind,
        "canon_delta_hash": override.canon_delta_hash,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "status": "candidate",
        "candidate_only": True,
        "replayed": False,
    }


async def request_publish_derivative_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    override_id: int,
    draft_hash: str,
    canon_delta_hash: str,
    actor_id: int,
    approval_note: str | None = None,
    branch: str | None = None,
    fork: str | None = None,
    run_id: int | None = None,
    skill_version_id: int | None = None,
    artifact_id: int | None = None,
    artifact_revision_id: int | None = None,
) -> dict[str, Any]:
    """Server-authoritative ``publish_derivative_revision`` action (37-05).

    Only after an approved ``allow_divergence`` approval with the **identical**
    payload hash and a successful full revalidation does this create a separate
    pending Web ApprovalRequest (action=``publish_derivative_revision``) bound
    to the same exact ``draft_hash`` + ``canon_delta_hash``. The divergence
    approval is never reused; a skipped/unapproved step, hash drift or
    revalidation failure fails closed. It never publishes.
    """
    override = await load_override_for_agent(
        db, owner_id=owner_id, novel_id=novel_id, override_id=override_id
    )
    if override.approval_state != "pending":
        raise OverrideError(
            CODE_ALREADY_DECIDED,
            f"override {override.id} is {override.approval_state!r}; the "
            "divergence must still be pending before a publish approval is issued",
            status_code=409,
        )
    payload_hash = divergence_approval_payload_hash(
        draft_hash=draft_hash, canon_delta_hash=canon_delta_hash
    )
    divergence_approval = await find_approved_divergence_approval(
        db, owner_id=owner_id, fork_id=override.fork_id, payload_hash=payload_hash
    )
    if divergence_approval is None:
        raise OverrideError(
            CODE_APPROVAL_NOT_APPROVED,
            "no matching approved allow_divergence approval with identical "
            "hashes exists; the divergence step was skipped or not approved",
            status_code=409,
        )
    candidate = await load_candidate_for_agent(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_id=override.candidate_id,
    )
    if draft_hash_for_candidate(candidate) != draft_hash:
        raise OverrideError(
            "draft_hash_mismatch",
            "supplied draft_hash no longer replays the candidate's frozen "
            "structured output",
        )
    if override.canon_delta_hash != canon_delta_hash:
        raise OverrideError(
            "canon_delta_hash_mismatch",
            "supplied canon_delta_hash no longer replays the override lineage",
        )
    await revalidate_approved_divergence(db, candidate=candidate)
    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=run_id,
        skill_version_id=skill_version_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
        novel_id=novel_id,
        branch_id=None,
        fork_id=override.fork_id,
        action=PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION,
        payload_summary={
            "override_id": override.id,
            "candidate_id": candidate.id,
            "job_id": candidate.job_id,
            "project_id": override.project_id,
            "chapter_id": override.chapter_id,
            "fork_id": override.fork_id,
            "draft_hash": draft_hash,
            "canon_delta_hash": canon_delta_hash,
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
    return {
        "override_id": override.id,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "project_id": override.project_id,
        "chapter_id": override.chapter_id,
        "fork_id": override.fork_id,
        "candidate_id": candidate.id,
        "job_id": candidate.job_id,
        "divergence_approval_id": divergence_approval.id,
        "divergence_approval_status": divergence_approval.status,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "status": "candidate",
        "candidate_only": True,
    }


async def consume_publish_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    override_id: int,
    publish_approval_id: int,
    approval_note: str,
    actor_id: int,
) -> OverrideApprovalResult:
    """Deterministic publisher: consume an approved publish_derivative_revision approval.

    This is the **only** Agent-path write the materialization may take
    (D-37-03/D-37-04). It verifies, in order:

    - the ``publish_derivative_revision`` ApprovalRequest exists, is owned and
      is ``approved``;
    - the override is still ``pending`` (not already decided);
    - a matching ``allow_divergence`` ApprovalRequest exists and is ``approved``
      (skipped step -> fail closed) with the **identical** payload hash;
    - the publish approval payload still replays the override lineage
      (``draft_hash`` / ``canon_delta_hash``; forged/stale approval -> fail
      closed).

    Only then does it delegate to ``overrides.approve_override`` (the
    deterministic, CAS-guarded, Fanfiction Canon-only materializer). It never
    writes Original Canon, User Interpretation or Narrative Memory and never
    promotes a pointer.
    """
    publish_approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == publish_approval_id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
    if publish_approval is None:
        raise OverrideError(
            CODE_APPROVAL_NOT_FOUND,
            "publish_derivative_revision approval not found in the owner scope",
            status_code=404,
        )
    if publish_approval.action != PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION:
        raise OverrideError(
            CODE_APPROVAL_NOT_FOUND,
            f"approval {publish_approval.id} is {publish_approval.action!r}; "
            "not a publish_derivative_revision approval",
            status_code=404,
        )
    if publish_approval.status != "approved":
        raise OverrideError(
            CODE_APPROVAL_NOT_APPROVED,
            f"publish approval {publish_approval.id} is "
            f"{publish_approval.status!r}; only an approved publish approval "
            "can be consumed",
            status_code=409,
        )
    row = await load_override_for_agent(
        db, owner_id=owner_id, novel_id=novel_id, override_id=override_id
    )
    if row.approval_state != "pending":
        raise OverrideError(
            CODE_ALREADY_DECIDED,
            f"override {row.id} is already {row.approval_state!r}; a decided "
            "override cannot be published again",
            status_code=409,
        )
    divergence_approval = await find_approved_divergence_approval(
        db,
        owner_id=owner_id,
        fork_id=row.fork_id,
        payload_hash=publish_approval.payload_hash,
    )
    if divergence_approval is None:
        raise OverrideError(
            CODE_APPROVAL_NOT_APPROVED,
            "no matching approved allow_divergence approval with identical "
            "hashes exists; the divergence step was skipped or not approved",
            status_code=409,
        )
    candidate = await load_candidate_for_agent(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_id=row.candidate_id,
    )
    summary = dict(publish_approval.payload_summary or {})
    if summary.get("draft_hash") != draft_hash_for_candidate(candidate):
        raise OverrideError(
            CODE_APPROVAL_HASH_MISMATCH,
            "publish approval draft_hash no longer replays the override lineage",
            status_code=409,
        )
    if summary.get("canon_delta_hash") != row.canon_delta_hash:
        raise OverrideError(
            CODE_APPROVAL_HASH_MISMATCH,
            "publish approval canon_delta_hash no longer replays the override lineage",
            status_code=409,
        )
    return await approve_override(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        override_id=override_id,
        approval_reason=approval_note or "publish_derivative_revision approved",
        actor_id=actor_id,
    )


__all__ = [
    "ALLOW_DIVERGENCE_APPROVAL_ACTION",
    "APPROVAL_HASH_PREFIX",
    "CODE_APPROVAL_HASH_MISMATCH",
    "CODE_APPROVAL_NOT_APPROVED",
    "CODE_APPROVAL_NOT_FOUND",
    "CODE_REVALIDATION_FAILED",
    "PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION",
    "consume_publish_approval",
    "divergence_approval_payload_hash",
    "draft_hash_for_candidate",
    "find_allow_divergence_approval",
    "find_approved_divergence_approval",
    "find_override_for_candidate",
    "rebuild_candidate_draft",
    "request_divergence_override",
    "request_publish_derivative_revision",
    "revalidate_approved_divergence",
]
