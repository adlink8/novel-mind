"""Explicit divergence override service (Phase 37-04, D-37-03 / REQ-CRE-06).

D-37-03 / REQ-FORK-03: a derivative candidate is only ever persisted as
``candidate | blocked | needs_override``; an allowed divergence is **only** an
explicit owner ``CanonDelta`` override. This service is the deterministic
authority for that review action:

- ``create_override`` freezes the owner-stated divergence (kind / reason /
  affected evidence / actor / time / evidence snapshot) against the sealed
  package allowlist and the candidate's gate verdict. A blocked/override
  candidate maps to exactly one pending override row; a clean ``candidate`` is
  never overridable and an override without reason or evidence is rejected
  (fail closed, T-37-04-01).
- ``approve_override`` is the **only** write path an approved override may
  take: it materializes the candidate draft into a **Fanfiction Canon**
  ``derivative_revisions`` row (immutable, CAS-guarded, kind
  ``agent_proposal``, ``approval_state=approved``) and emits the immutable
  ``PublishedDerivativeRevision`` DTO for the Phase 39 consumer. It never
  writes Original Canon / User Interpretation / Narrative Memory and never
  promotes an active pointer (D-37-02 / D-37-04 forbidden publish path).
- ``reject_override`` terminates a pending override without any revision.
- Approval requires an explicit owner approval note (approval): an approve
  without ``approval_reason`` is rejected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import (
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.models.derivative_override import DerivativeOverride
from app.models.derivative_project import DerivativeProject
from app.models.derivative_revision import DerivativeRevision
from app.services.derivative_editor.chapters import (
    canonicalize_markdown,
    markdown_checksum,
)
from app.services.derivative_editor.revisions import append_revision_row
from app.services.derivative_generation.candidate import DIVERGENCE_TYPES
from app.services.derivative_generation.gates import package_evidence_allowlist
from app.services.derivative_generation.published_revision import (
    build_published_derivative_revision,
)

# Stable override failure codes.
CODE_OVERRIDE_NOT_FOUND = "override_not_found"
CODE_CANDIDATE_NOT_FOUND = "candidate_not_found"
CODE_CANDIDATE_NOT_OVERRIDABLE = "candidate_not_overridable"
CODE_PROJECT_NOT_FOUND = "project_not_found"
CODE_CHAPTER_NOT_FOUND = "chapter_not_found"
CODE_CROSS_FORK_OVERRIDE = "cross_fork_override"
CODE_PACKAGE_HASH_MISMATCH = "package_hash_mismatch"
CODE_EVIDENCE_OUTSIDE_PACKAGE = "evidence_outside_package"
CODE_MISSING_REASON = "missing_reason"
CODE_MISSING_EVIDENCE = "missing_evidence"
CODE_MISSING_APPROVAL = "missing_approval"
CODE_MISSING_KIND = "missing_kind"
CODE_INVALID_KIND = "invalid_kind"
CODE_ALREADY_DECIDED = "already_decided"
CODE_PROJECT_ARCHIVED = "project_archived"

# Overridable verdicts: only a blocked or needs_override candidate (D-37-03).
OVERRIDABLE_VERDICTS = frozenset({"blocked", "needs_override"})

# Prefix for the deterministic override CanonDelta hash (byte-replayable).
OVERRIDE_HASH_PREFIX = "derivative-override.v1:delta"

# Override materialization revision kind/approval (deterministic service apply).
OVERRIDE_REVISION_KIND = "agent_proposal"


class OverrideError(ValueError):
    """Fail-closed override gate violation with an HTTP status code."""

    def __init__(self, code: str, detail: str, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class OverrideApprovalResult:
    """Approval outcome: the override row + the immutable published DTO."""

    override: DerivativeOverride
    published: Any
    status: str  # ``applied`` (new revision) or ``noop`` (idempotent replay)


def override_hash(*, kind: str, reason: str, affected_evidence: list[str], package_hash: str) -> str:
    """Deterministic CanonDelta hash for an override without a candidate delta."""
    encoded = json.dumps(
        {
            "kind": kind,
            "reason": reason,
            "affected_evidence": sorted(affected_evidence or []),
            "package_hash": package_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(f"{OVERRIDE_HASH_PREFIX}\n".encode("utf-8") + encoded).hexdigest()


# ---------------------------------------------------------------------------
# Owner-scoped loads (a foreign/missing id is an identical 404)
# ---------------------------------------------------------------------------


async def _load_scoped_candidate(
    db: AsyncSession, *, owner_id: int, novel_id: int, candidate_id: int
) -> tuple[DerivativeGenerationCandidate, DerivativeGenerationJob]:
    row = await db.scalar(
        select(DerivativeGenerationCandidate).where(
            DerivativeGenerationCandidate.id == candidate_id,
            DerivativeGenerationCandidate.owner_id == owner_id,
            DerivativeGenerationCandidate.novel_id == novel_id,
        )
    )
    if row is None:
        raise OverrideError(
            CODE_CANDIDATE_NOT_FOUND,
            "generation candidate not found in the owner/novel scope",
            status_code=404,
        )
    job = await db.scalar(
        select(DerivativeGenerationJob).where(
            DerivativeGenerationJob.id == row.job_id,
            DerivativeGenerationJob.owner_id == owner_id,
            DerivativeGenerationJob.novel_id == novel_id,
        )
    )
    if job is None:
        raise OverrideError(
            CODE_CANDIDATE_NOT_FOUND,
            "generation job lineage not found in the owner/novel scope",
            status_code=404,
        )
    return row, job


async def _load_scoped_package(
    db: AsyncSession, *, owner_id: int, novel_id: int, context_package_id: int
) -> ContextPackageRecord:
    row = await db.scalar(
        select(ContextPackageRecord).where(
            ContextPackageRecord.id == context_package_id,
            ContextPackageRecord.owner_id == owner_id,
            ContextPackageRecord.novel_id == novel_id,
        )
    )
    if row is None:
        raise OverrideError(
            CODE_PACKAGE_HASH_MISMATCH,
            "sealed context package not found in the owner/novel scope",
            status_code=404,
        )
    return row


async def _load_scoped_project(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> DerivativeProject:
    row = await db.scalar(
        select(DerivativeProject).where(
            DerivativeProject.id == project_id,
            DerivativeProject.owner_id == owner_id,
            DerivativeProject.novel_id == novel_id,
        )
    )
    if row is None:
        raise OverrideError(
            CODE_PROJECT_NOT_FOUND,
            "derivative project not found in the owner/novel scope",
            status_code=404,
        )
    return row


async def _load_scoped_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> DerivativeChapter:
    row = await db.scalar(
        select(DerivativeChapter).where(
            DerivativeChapter.id == chapter_id,
            DerivativeChapter.project_id == project_id,
            DerivativeChapter.owner_id == owner_id,
            DerivativeChapter.novel_id == novel_id,
        )
    )
    if row is None:
        raise OverrideError(
            CODE_CHAPTER_NOT_FOUND,
            "derivative chapter not found in the owner/novel/project scope",
            status_code=404,
        )
    return row


async def _load_scoped_override(
    db: AsyncSession, *, owner_id: int, novel_id: int, override_id: int
) -> DerivativeOverride:
    row = await db.scalar(
        select(DerivativeOverride).where(
            DerivativeOverride.id == override_id,
            DerivativeOverride.owner_id == owner_id,
            DerivativeOverride.novel_id == novel_id,
        )
    )
    if row is None:
        raise OverrideError(
            CODE_OVERRIDE_NOT_FOUND,
            "divergence override not found in the owner/novel scope",
            status_code=404,
        )
    return row


# ---------------------------------------------------------------------------
# Override surface construction
# ---------------------------------------------------------------------------


def _resolve_kind_and_evidence(
    *,
    candidate: DerivativeGenerationCandidate,
    package: ContextPackageRecord,
    requested_kind: str | None,
    requested_evidence: list[str],
) -> tuple[str, list[str]]:
    """D-37-03: derive kind from the candidate's CanonDelta, else owner-supplied.

    ``affected_evidence`` must always be non-empty and within the sealed package
    allowlist; a divergence that cites nothing or cites forged keys is rejected.
    """
    divergence = candidate.divergence or {}
    declared_type = divergence.get("divergence_type")
    if declared_type:
        if requested_kind is not None and requested_kind != declared_type:
            raise OverrideError(
                CODE_INVALID_KIND,
                f"override kind {requested_kind!r} contradicts the candidate's "
                f"declared CanonDelta type {declared_type!r}",
            )
        kind = str(declared_type)
        declared_evidence = [str(key) for key in (divergence.get("affected_evidence") or [])]
        if requested_evidence and not set(requested_evidence) <= set(declared_evidence):
            raise OverrideError(
                CODE_EVIDENCE_OUTSIDE_PACKAGE,
                "override evidence must be a subset of the candidate's declared "
                "CanonDelta affected_evidence",
            )
        evidence = requested_evidence or declared_evidence
    else:
        if requested_kind is None:
            raise OverrideError(
                CODE_MISSING_KIND,
                "the candidate declares no CanonDelta; override kind is required",
            )
        if requested_kind not in DIVERGENCE_TYPES:
            raise OverrideError(
                CODE_INVALID_KIND,
                f"unknown override kind {requested_kind!r}; allowed kinds are "
                f"{DIVERGENCE_TYPES}",
            )
        kind = requested_kind
        evidence = requested_evidence

    if not evidence:
        raise OverrideError(
            CODE_MISSING_EVIDENCE,
            "an override must affect at least one evidence key from the sealed package",
        )
    allowed = package_evidence_allowlist(dict(package.canonical_payload or {}))
    outside = sorted(set(evidence) - allowed)
    if outside:
        raise OverrideError(
            CODE_EVIDENCE_OUTSIDE_PACKAGE,
            f"override evidence outside the sealed package allowlist: {outside}",
        )
    return kind, evidence


def _build_evidence_snapshot(
    *,
    candidate: DerivativeGenerationCandidate,
    canon_delta_hash: str,
    kind: str,
    reason: str,
    affected_evidence: list[str],
) -> dict[str, Any]:
    """Frozen gate/candidate audit the approval decision is based on."""
    return {
        "gate_verdict": candidate.gate_verdict,
        "gate_reason": candidate.gate_reason,
        "canon_delta_hash": canon_delta_hash,
        "divergence": dict(candidate.divergence or {}),
        "kind": kind,
        "reason": reason,
        "affected_evidence": list(affected_evidence),
        "citation_keys": list(candidate.citation_keys or []),
        "package_hash": candidate.package_hash,
        "prompt_hash": candidate.prompt_hash,
    }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


async def create_override(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    candidate_id: int,
    reason: str,
    affected_evidence: list[str],
    kind: str | None = None,
    actor_id: int,
) -> DerivativeOverride:
    """Freeze one explicit owner divergence override for a blocked/override candidate.

    An override without a reason, without affected evidence, on a clean
    ``candidate`` verdict, on a foreign owner/novel scope or on a project bound
    to a different fork all fail closed (T-37-04-01).
    """
    reason_text = (reason or "").strip()
    if not reason_text:
        raise OverrideError(
            CODE_MISSING_REASON,
            "an explicit divergence override requires a reason",
        )
    candidate, job = await _load_scoped_candidate(
        db, owner_id=owner_id, novel_id=novel_id, candidate_id=candidate_id
    )
    if candidate.gate_verdict not in OVERRIDABLE_VERDICTS:
        raise OverrideError(
            CODE_CANDIDATE_NOT_OVERRIDABLE,
            f"candidate {candidate.id} is {candidate.gate_verdict!r}; only blocked "
            "or needs_override candidates accept an explicit override",
            status_code=409,
        )
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    if project.fork_id != job.fork_id:
        raise OverrideError(
            CODE_CROSS_FORK_OVERRIDE,
            f"override project {project.id} is bound to fork {project.fork_id} "
            f"but the candidate lineage is fork {job.fork_id}; divergence can "
            "only materialize into the same Fanfiction Canon fork scope",
            status_code=409,
        )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    package = await _load_scoped_package(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        context_package_id=job.context_package_id,
    )
    resolved_kind, evidence = _resolve_kind_and_evidence(
        candidate=candidate,
        package=package,
        requested_kind=kind,
        requested_evidence=list(affected_evidence or []),
    )
    delta_hash = candidate.canon_delta_hash or override_hash(
        kind=resolved_kind,
        reason=reason_text,
        affected_evidence=evidence,
        package_hash=candidate.package_hash,
    )
    snapshot = _build_evidence_snapshot(
        candidate=candidate,
        canon_delta_hash=delta_hash,
        kind=resolved_kind,
        reason=reason_text,
        affected_evidence=evidence,
    )
    row = DerivativeOverride(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        fork_id=job.fork_id,
        candidate_id=candidate.id,
        job_id=job.id,
        kind=resolved_kind,
        reason=reason_text,
        affected_evidence=evidence,
        canon_delta_hash=delta_hash,
        evidence_snapshot=snapshot,
        actor_id=actor_id,
        approval_state="pending",
    )
    db.add(row)
    await db.flush()
    return row


async def approve_override(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    override_id: int,
    approval_reason: str,
    actor_id: int,
) -> OverrideApprovalResult:
    """Explicit owner approval: materialize the override into a Fanfiction revision.

    The approval note is required (an approve without approval is rejected) and
    the write is the deterministic CAS-guarded ``agent_proposal`` revision apply
    — the same immutable lineage the derivative editor uses. No Original /
    Interpretation / NM write and no active-pointer promotion ever happens here.
    """
    approval = (approval_reason or "").strip()
    if not approval:
        raise OverrideError(
            CODE_MISSING_APPROVAL,
            "approving a divergence override requires an explicit approval note",
        )
    row = await _load_scoped_override(
        db, owner_id=owner_id, novel_id=novel_id, override_id=override_id
    )
    if row.approval_state != "pending":
        raise OverrideError(
            CODE_ALREADY_DECIDED,
            f"override {row.id} is already {row.approval_state!r}; a decided "
            "override cannot be re-approved",
            status_code=409,
        )
    candidate, _ = await _load_scoped_candidate(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_id=row.candidate_id,
    )
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=row.project_id
    )
    if project.status == "archived":
        raise OverrideError(
            CODE_PROJECT_ARCHIVED,
            f"project {project.id} is archived; divergence materialization is blocked",
            status_code=409,
        )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=row.chapter_id,
    )

    canonical = canonicalize_markdown(candidate.draft_text)
    checksum = markdown_checksum(canonical)
    revision_id: int | None = None
    version_id: int = chapter.revision
    applied = False

    if checksum == chapter.markdown_checksum:
        # Idempotent replay: the approved draft already equals the chapter head.
        latest = await db.scalar(
            select(DerivativeRevision).where(
                DerivativeRevision.chapter_id == chapter.id,
                DerivativeRevision.owner_id == owner_id,
            ).order_by(
                DerivativeRevision.revision_number.desc(),
                DerivativeRevision.id.desc(),
            ).limit(1)
        )
        revision_id = latest.id if latest is not None else None
    else:
        result = await db.execute(
            update(DerivativeChapter)
            .where(DerivativeChapter.id == chapter.id)
            .values(
                markdown=canonical,
                markdown_checksum=checksum,
                revision=DerivativeChapter.revision + 1,
                updated_at=func.now(),
            )
        )
        if result.rowcount == 0:
            raise OverrideError(
                "revision_conflict",
                f"chapter {chapter.id} changed concurrently; retry the approval",
                status_code=409,
            )
        await db.refresh(chapter)
        version_id = chapter.revision
        revision = await append_revision_row(
            db,
            chapter=chapter,
            revision_number=chapter.revision,
            kind=OVERRIDE_REVISION_KIND,
            content=canonical,
            checksum=checksum,
            actor_id=actor_id,
            reason=(
                f"divergence override:{row.id}:{row.kind}:{approval[:400]}"
            )[:400],
            approval_state="approved",
        )
        revision_id = revision.id
        applied = True

    if revision_id is None:
        raise OverrideError(
            "revision_not_found",
            "the override materialization has no head revision; retry the approval",
            status_code=409,
        )

    row.approval_state = "approved"
    row.approver_id = actor_id
    row.approved_at = func.now()
    row.approval_reason = approval
    await db.flush()
    # An UPDATE flush expires ``onupdate``/SQL-expression columns; re-select the
    # row before reading ``approved_at`` (MissingGreenlet guard, runner pattern).
    await db.refresh(row)

    published = build_published_derivative_revision(
        owner_id=owner_id,
        project_id=project.id,
        fork_id=project.fork_id,
        revision_id=revision_id,
        version_id=version_id,
        source_snapshot=project.source_snapshot_hash,
        manifest_hash=project.manifest_hash,
        citation_keys=list(candidate.citation_keys or []),
        approval_state=row.approval_state,
        approver_id=actor_id,
        approved_at=row.approved_at or datetime.now(),
        approval_reason=approval,
        override_kind=row.kind,
        override_reason=row.reason,
        gate_verdict=candidate.gate_verdict,
        gate_reason=candidate.gate_reason,
        canon_delta_hash=row.canon_delta_hash,
        evidence_snapshot=dict(row.evidence_snapshot or {}),
    )
    return OverrideApprovalResult(
        override=row,
        published=published,
        status="applied" if applied else "noop",
    )


async def reject_override(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    override_id: int,
    rejection_reason: str,
    actor_id: int,
) -> DerivativeOverride:
    """Terminate a pending override with an explicit rejection; no revision."""
    note = (rejection_reason or "").strip()
    row = await _load_scoped_override(
        db, owner_id=owner_id, novel_id=novel_id, override_id=override_id
    )
    if row.approval_state != "pending":
        raise OverrideError(
            CODE_ALREADY_DECIDED,
            f"override {row.id} is already {row.approval_state!r}",
            status_code=409,
        )
    row.approval_state = "rejected"
    row.approver_id = actor_id
    row.rejected_at = func.now()
    row.approval_reason = note or None
    await db.flush()
    await db.refresh(row)
    return row


async def list_overrides(
    db: AsyncSession, *, owner_id: int, novel_id: int
) -> list[DerivativeOverride]:
    return list(
        (
            await db.scalars(
                select(DerivativeOverride)
                .where(
                    DerivativeOverride.owner_id == owner_id,
                    DerivativeOverride.novel_id == novel_id,
                )
                .order_by(DerivativeOverride.id.desc())
            )
        ).all()
    )


async def get_override(
    db: AsyncSession, *, owner_id: int, novel_id: int, override_id: int
) -> DerivativeOverride:
    return await _load_scoped_override(
        db, owner_id=owner_id, novel_id=novel_id, override_id=override_id
    )


__all__ = [
    "CODE_ALREADY_DECIDED",
    "CODE_CANDIDATE_NOT_FOUND",
    "CODE_CANDIDATE_NOT_OVERRIDABLE",
    "CODE_CHAPTER_NOT_FOUND",
    "CODE_CROSS_FORK_OVERRIDE",
    "CODE_EVIDENCE_OUTSIDE_PACKAGE",
    "CODE_INVALID_KIND",
    "CODE_MISSING_APPROVAL",
    "CODE_MISSING_EVIDENCE",
    "CODE_MISSING_KIND",
    "CODE_MISSING_REASON",
    "CODE_OVERRIDE_NOT_FOUND",
    "CODE_PACKAGE_HASH_MISMATCH",
    "CODE_PROJECT_ARCHIVED",
    "CODE_PROJECT_NOT_FOUND",
    "OVERRIDE_REVISION_KIND",
    "OVERRIDABLE_VERDICTS",
    "OverrideApprovalResult",
    "OverrideError",
    "approve_override",
    "create_override",
    "get_override",
    "list_overrides",
    "override_hash",
    "reject_override",
]
