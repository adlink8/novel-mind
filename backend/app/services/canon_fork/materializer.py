"""Deterministic Canon Fork materializer (Phase 35-05, REQ-FORK-01 / REQ-AGENT-03/04/07).

D-35-01..D-35-04 / the Agent Consumer Contract: a Canon Fork is consumed through
the versioned ``create-canon-fork`` Skill. The Agent output is **candidate-only**
until deterministic validation and the required Web Approval complete. FastAPI
owns state; this module owns the two server-authoritative transitions that
Phase 35 permits outside the pure contract layer:

- ``create_fork_proposal`` — the **candidate proposal gate**. It accepts only a
  server-sealed ``CanonForkManifest`` (35-02 snapshot: server-derived cutoff +
  exact source snapshot/hash, D-35-03) plus the delta intent, atomically creates
  a candidate ``CanonFork`` row (``status=candidate``, ``active=false``) and a
  pending Web ApprovalRequest (action = ``create_canon_fork``, ``payload_hash`` =
  canonical replay hash of the frozen proposal+delta payload, D-11/D-15).
  Nothing here materializes.
- ``materialize_approved_fork`` — the **deterministic Fork materializer**. It
  atomically verifies the approved ``create_canon_fork`` action and replay
  payload hash, the frozen fork manifest, the *current* source snapshot replay,
  the CanonDeltaArtifact lineage (base revision, content hash, evidence refs,
  owner/novel/branch), the run/artifact lineage and the owner/novel/branch/fork
  scope; then it materializes the candidate fork to ``approved``. ``active``
  stays false and Original Canon is never touched. Any forged/expired/cancelled/
  rejected approval, wrong branch/fork, stale base revision or snapshot, schema
  drift or forbidden Tool/action fails closed with no authoritative write.

Authority boundaries:
- Agent Service / browser / Pi can only propose; FastAPI owns state and the
  deterministic Fork materializer owns approved fork materialization. No shell,
  filesystem, ambient package, direct Original Canon or domain-table write path
  exists here.
- Forks are append-only (ORM events reject in-place lineage mutation); the
  materializer only moves the ``status`` projection (``candidate`` ->
  ``approved``) and never changes Original Canon, the active pointer or any
  other fork lineage field (D-35-03).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pydantic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.agent_runtime import ApprovalRequest, Artifact, ArtifactRevision, SkillRun
from app.models.canon_fork import CanonFork
from app.models.novel import Chapter
from app.models.user import User
from app.schemas.agent_runtime import CanonForkProposalArtifact
from app.services.canon_fork.snapshot import (
    CanonForkScopeError,
    CanonForkSnapshotService,
    chapter_content_hash,
)

CANON_FORK_APPROVAL_ACTION = "create_canon_fork"
CANON_FORK_APPROVAL_PREFIX = "canon-fork.v1:approval"
CANON_FORK_MATERIALIZATION_PREFIX = "canon-fork.v1:materialization"
CANON_FORK_APPROVAL_SCHEMA_VERSION = "canon-fork-proposal.v1"


class ForkProposalError(ValueError):
    """Candidate proposal gate violation (fail closed, no fork is materialized)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class ForkMaterializeError(ValueError):
    """Deterministic materialization gate violation (fail closed, no authoritative write)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ForkProposalError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


def canonical_fork_approval_hash(payload: dict[str, Any]) -> str:
    """Byte-replayable canonical hash of a frozen fork approval payload (D-15)."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(f"{CANON_FORK_APPROVAL_PREFIX}\n{encoded}".encode("utf-8")).hexdigest()


def canonical_fork_materialization_hash(
    *,
    fork_id: int,
    manifest_hash: str,
    delta_content_hash: str,
    approval_request_id: int,
) -> str:
    """Deterministic materialization hash (replayable, idempotent)."""
    payload = {
        "kind": f"{CANON_FORK_MATERIALIZATION_PREFIX}:materialization",
        "fork_id": fork_id,
        "manifest_hash": manifest_hash,
        "delta_content_hash": delta_content_hash,
        "approval_request_id": approval_request_id,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(f"{CANON_FORK_MATERIALIZATION_PREFIX}\n{encoded}".encode("utf-8")).hexdigest()


def build_fork_approval_payload(
    *,
    owner_id: int,
    novel_id: int,
    branch: str | None,
    fork_key: str,
    source_version_key: str,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    through_chapter: int,
    full_book_authorized: bool,
    cutoff_snapshot_hash: str,
    scope_hash: str,
    manifest_hash: str,
    delta_key: str,
    delta_content_hash: str,
) -> dict[str, Any]:
    """Frozen approval payload bound to the fork proposal + delta (D-15 replay hash source).

    The ApprovalRequest ``payload_hash`` and the materializer recomputation both
    replay from this canonical snapshot, so a forged or drifted decision can
    never materialize a fork. ``branch`` / ``fork_key`` are the server-derived
    scope bindings; the delta content hash seals the exact proposed derivative.
    """
    return {
        "artifact_kind": "canon_fork_proposal",
        "schema_version": CANON_FORK_APPROVAL_SCHEMA_VERSION,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "branch": branch,
        "fork_key": fork_key,
        "source_version_key": source_version_key,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_hash": source_snapshot_hash,
        "through_chapter": through_chapter,
        "full_book_authorized": full_book_authorized,
        "cutoff_snapshot_hash": cutoff_snapshot_hash,
        "scope_hash": scope_hash,
        "manifest_hash": manifest_hash,
        "delta_key": delta_key,
        "delta_content_hash": delta_content_hash,
    }


def delta_content_hash(content: str) -> str:
    """Deterministic content hash of a candidate delta (D-35-03 replay lineage)."""
    return chapter_content_hash(content)


# ---------------------------------------------------------------------------
# Candidate proposal creation (Agent -> candidate fork + pending ApprovalRequest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForkProposalCreateResult:
    """Proposal creation result: candidate fork + pending approval (+ replay flag)."""

    fork: CanonFork
    approval_request: ApprovalRequest
    delta_content_hash: str
    replayed: bool = False


async def _load_fork_user(db: AsyncSession, *, owner_id: int) -> User:
    user = await db.get(User, owner_id)
    if user is None:
        raise ForkProposalError("owner_not_found", "authenticated owner row missing")
    return user


async def create_fork_proposal(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    request: dict[str, Any],
) -> ForkProposalCreateResult:
    """Server-authoritative candidate fork proposal creation (D-35-03 / D-11 / D-15).

    - Derives and seals the frozen fork manifest via ``CanonForkSnapshotService``
      (server-derived cutoff + exact source snapshot/hash); a stale
      ``expected_source_snapshot_hash`` or an unauthorized full-book request
      fails closed with the 35-02 machine-readable code.
    - Replays an existing candidate fork with an identical manifest and an
      identical delta intent (one approval per fork/delta).
    - Creates the pending Web ApprovalRequest bound to the frozen approval
      payload hash (action = ``create_canon_fork``).
    Nothing here materializes; the deterministic Fork materializer owns approved
    fork materialization.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if request.get("fork_key") in (None, ""):
        raise ForkProposalError("invalid_fork_key", "fork_key must be non-empty")
    if request.get("delta_key") in (None, ""):
        raise ForkProposalError("invalid_delta_key", "delta_key must be non-empty")

    user = await _load_fork_user(db, owner_id=owner_id)
    service = CanonForkSnapshotService(db)
    try:
        manifest = await service.freeze_manifest(
            owner_id=owner_id,
            novel_id=novel_id,
            user=user,
            fork_key=str(request["fork_key"]),
            requested_cutoff_chapter=request.get("requested_cutoff_chapter"),
            full_book_requested=bool(request.get("full_book_requested", False)),
            expected_source_snapshot_hash=request.get(
                "expected_source_snapshot_hash"
            ),
        )
        fork, replayed_fork = await service.persist_fork(manifest=manifest)
    except CanonForkScopeError as exc:
        raise ForkProposalError(exc.code, exc.detail) from exc

    delta_key = str(request["delta_key"])
    delta_content = str(request["delta_content"])
    delta_hash = delta_content_hash(delta_content)
    payload = build_fork_approval_payload(
        owner_id=owner_id,
        novel_id=novel_id,
        branch=request.get("branch"),
        fork_key=fork.fork_key,
        source_version_key=fork.source_version_key,
        source_snapshot_id=fork.source_snapshot_id,
        source_snapshot_hash=fork.source_snapshot_hash,
        through_chapter=fork.through_chapter,
        full_book_authorized=fork.full_book_authorized,
        cutoff_snapshot_hash=fork.cutoff_snapshot_hash,
        scope_hash=fork.scope_hash,
        manifest_hash=fork.manifest_hash,
        delta_key=delta_key,
        delta_content_hash=delta_hash,
    )
    payload_hash = canonical_fork_approval_hash(payload)

    existing_approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.action == CANON_FORK_APPROVAL_ACTION,
            ApprovalRequest.fork_id == fork.id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
    if existing_approval is not None:
        if existing_approval.payload_hash == payload_hash:
            return ForkProposalCreateResult(
                fork=fork,
                approval_request=existing_approval,
                delta_content_hash=delta_hash,
                replayed=True,
            )
        raise ForkProposalError(
            "fork_delta_conflict",
            "this fork is already proposed under a different delta intent; "
            "replay the existing proposal instead of widening the scope",
        )

    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=int(request["run_id"]) if request.get("run_id") else None,
        skill_version_id=(
            int(request["skill_version_id"]) if request.get("skill_version_id") else None
        ),
        artifact_id=int(request["artifact_id"]) if request.get("artifact_id") else None,
        artifact_revision_id=(
            int(request["artifact_revision_id"])
            if request.get("artifact_revision_id")
            else None
        ),
        novel_id=novel_id,
        branch_id=None,
        fork_id=fork.id,
        action=CANON_FORK_APPROVAL_ACTION,
        payload_summary={
            "fork_key": fork.fork_key,
            "source_snapshot_hash": fork.source_snapshot_hash,
            "through_chapter": fork.through_chapter,
            "full_book_authorized": fork.full_book_authorized,
            "manifest_hash": fork.manifest_hash,
            "delta_key": delta_key,
            "delta_content_hash": delta_hash,
            "branch": request.get("branch"),
        },
        payload_hash=payload_hash,
        status="pending",
        expires_at=None,
    )
    db.add(approval)
    await db.flush()
    return ForkProposalCreateResult(
        fork=fork,
        approval_request=approval,
        delta_content_hash=delta_hash,
        replayed=replayed_fork,
    )


# ---------------------------------------------------------------------------
# Deterministic Fork materializer (Approval -> approved fork)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForkMaterializeOutcome:
    """Materialization result: the approved fork + deterministic hash (+ replay flag)."""

    fork: CanonFork
    materialization_hash: str
    replayed: bool = False


async def _load_artifact_revision(
    db: AsyncSession, *, owner_id: int, novel_id: int, revision_id: int
) -> ArtifactRevision:
    revision = await db.scalar(
        select(ArtifactRevision).where(
            ArtifactRevision.id == revision_id,
            ArtifactRevision.owner_id == owner_id,
            ArtifactRevision.novel_id == novel_id,
        )
    )
    if revision is None:
        raise ForkMaterializeError(
            "artifact_not_found",
            "the CanonForkProposal artifact revision was not found in the owner/novel scope",
        )
    return revision


async def _load_run(
    db: AsyncSession, *, run_id: int
) -> SkillRun:
    run = await db.get(SkillRun, run_id)
    if run is None:
        raise ForkMaterializeError(
            "run_not_found",
            "the SkillRun bound to the proposal artifact revision does not exist",
        )
    return run


async def _validate_materialization_context(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    fork: CanonFork,
    approval_request_id: int,
    artifact_revision_id: int,
) -> tuple[ApprovalRequest, CanonForkProposalArtifact, SkillRun]:
    """All fail-closed gates except the status mutation (shared by fresh + replay)."""
    if fork.status not in {"candidate", "approved"}:
        raise ForkMaterializeError(
            "fork_not_materializable",
            f"fork {fork.id} is {fork.status!r}; only candidate forks can be "
            "materialized (rejected/archived forks fail closed)",
        )

    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_request_id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
    if approval is None:
        raise ForkMaterializeError(
            "approval_not_found",
            "the create_canon_fork approval request was not found in the owner scope",
        )
    if approval.action != CANON_FORK_APPROVAL_ACTION:
        raise ForkMaterializeError(
            "approval_action_mismatch",
            f"approval action {approval.action!r} is not {CANON_FORK_APPROVAL_ACTION!r} "
            "(only the approved create_canon_fork action may materialize a fork)",
        )
    if approval.status not in {"approved", "approved_for_session"}:
        raise ForkMaterializeError(
            "approval_not_approved",
            f"approval request {approval.id} is {approval.status!r}; only an approved "
            "decision may reach the deterministic materializer (forged/expired/"
            "cancelled/rejected decisions fail closed)",
        )
    if approval.fork_id != fork.id:
        raise ForkMaterializeError(
            "approval_fork_mismatch",
            "the approval request is bound to a different fork (wrong branch/fork "
            "fails closed)",
        )

    revision = await _load_artifact_revision(
        db, owner_id=owner_id, novel_id=novel_id, revision_id=artifact_revision_id
    )
    artifact = await db.scalar(
        select(Artifact).where(
            Artifact.id == revision.artifact_id,
            Artifact.owner_id == owner_id,
            Artifact.novel_id == novel_id,
        )
    )
    if artifact is None:
        raise ForkMaterializeError(
            "artifact_not_found",
            "the artifact owning this revision was not found in the owner/novel scope",
        )
    if artifact.status == "rejected":
        raise ForkMaterializeError(
            "artifact_rejected",
            "the proposal artifact is rejected; a rejected proposal cannot be "
            "materialized (fail closed)",
        )

    try:
        model = CanonForkProposalArtifact.model_validate(revision.content)
    except pydantic.ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        raise ForkMaterializeError(
            "schema_drift",
            f"proposal artifact envelope failed strict wire validation ({loc}: "
            f"{first.get('msg', 'invalid')})",
        ) from exc

    run = await _load_run(db, run_id=artifact.run_id)
    if model.owner_id != owner_id or model.novel_id != novel_id:
        raise ForkMaterializeError(
            "delta_lineage_mismatch",
            "proposal envelope owner/novel lineage does not match the materialize scope",
        )
    if model.skill_version_id != run.skill_version_id:
        raise ForkMaterializeError(
            "delta_lineage_mismatch",
            "proposal envelope skill_version_id does not match the SkillRun lineage",
        )
    if model.input_hash != run.input_hash:
        raise ForkMaterializeError(
            "stale_revision",
            "proposal envelope input_hash does not replay the SkillRun input_hash "
            "(stale revision fails closed)",
        )
    if model.branch != run.branch:
        raise ForkMaterializeError(
            "branch_scope_mismatch",
            f"proposal envelope branch {model.branch!r} does not match the run "
            f"branch {run.branch!r} (wrong branch/fork fails closed)",
        )

    proposal = model.proposal
    delta = model.delta
    # Frozen manifest must replay from the fork row (immutability / stale base).
    manifest_fields = {
        "fork_key": (proposal.fork_key, fork.fork_key),
        "manifest_hash": (proposal.manifest_hash, fork.manifest_hash),
        "scope_hash": (proposal.scope_hash, fork.scope_hash),
        "source_snapshot_hash": (proposal.source_snapshot_hash, fork.source_snapshot_hash),
        "source_snapshot_id": (proposal.source_snapshot_id, fork.source_snapshot_id),
        "through_chapter": (proposal.through_chapter, fork.through_chapter),
        "cutoff_snapshot_hash": (proposal.cutoff_snapshot_hash, fork.cutoff_snapshot_hash),
    }
    for field, (actual, expected) in manifest_fields.items():
        if actual != expected:
            raise ForkMaterializeError(
                "proposal_fork_mismatch",
                f"proposal {field} does not replay the sealed fork manifest "
                f"(stale base revision fails closed)",
            )
    if delta.base_revision != fork.manifest_hash:
        raise ForkMaterializeError(
            "delta_base_mismatch",
            "delta base_revision does not match the sealed fork manifest hash "
            "(stale base revision fails closed)",
        )
    if delta.content_hash != delta_content_hash(delta.content):
        raise ForkMaterializeError(
            "delta_hash_mismatch",
            "delta content_hash does not replay from the delta content (schema "
            "drift fails closed)",
        )

    lineage_keys = {
        str(leaf.get("leaf_key"))
        for leaf in (fork.citation_lineage or [])
        if leaf.get("leaf_key")
    }
    if not lineage_keys:
        raise ForkMaterializeError(
            "empty_lineage",
            "the sealed fork has no citation lineage; a fork without source leaves "
            "cannot be materialized",
        )
    for ref in delta.evidence_refs or []:
        if str(ref) not in lineage_keys:
            raise ForkMaterializeError(
                "delta_evidence_mismatch",
                f"delta evidence ref {ref!r} is not within the frozen citation "
                "lineage (evidence drift fails closed)",
            )

    # Original Canon snapshot replay: any chapter drift since the fork was sealed
    # makes the proposal stale and must fail closed (no silent relocation).
    snapshot_hash, _ = await CanonForkSnapshotService(db).load_source_snapshot(
        owner_id=owner_id, novel_id=novel_id
    )
    if snapshot_hash != fork.source_snapshot_hash:
        raise ForkMaterializeError(
            "stale_source_snapshot",
            "the novel's source snapshot no longer replays the sealed fork "
            "snapshot; the source changed since the fork was proposed (stale "
            "snapshot fails closed)",
        )

    # Approval payload replay: the materializer recomputes the frozen approval
    # payload from the fork row + the artifact delta and requires an exact match.
    replayed_payload = build_fork_approval_payload(
        owner_id=owner_id,
        novel_id=novel_id,
        branch=model.branch,
        fork_key=fork.fork_key,
        source_version_key=fork.source_version_key,
        source_snapshot_id=fork.source_snapshot_id,
        source_snapshot_hash=fork.source_snapshot_hash,
        through_chapter=fork.through_chapter,
        full_book_authorized=fork.full_book_authorized,
        cutoff_snapshot_hash=fork.cutoff_snapshot_hash,
        scope_hash=fork.scope_hash,
        manifest_hash=fork.manifest_hash,
        delta_key=delta.delta_key,
        delta_content_hash=delta.content_hash,
    )
    replayed_hash = canonical_fork_approval_hash(replayed_payload)
    if not approval.payload_hash or approval.payload_hash != replayed_hash:
        raise ForkMaterializeError(
            "approval_payload_mismatch",
            "approval payload hash does not replay from the sealed fork + delta "
            "payload (schema drift / forged approval fails closed)",
        )

    return approval, model, run


async def materialize_approved_fork(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    fork_id: int,
    approval_request_id: int,
    artifact_revision_id: int,
) -> ForkMaterializeOutcome:
    """Deterministic Fork materializer: the only path that approves a candidate fork.

    Atomically verifies, in one transaction, the approved ``create_canon_fork``
    approval, the frozen fork manifest, the source snapshot replay, the
    CanonDeltaArtifact lineage and the owner/novel/branch/fork scope; then it
    materializes the candidate fork to ``approved``. ``active`` stays false and
    Original Canon is never changed. A replay of an already approved fork returns
    the existing outcome (idempotent materialization).
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    fork = await db.scalar(
        select(CanonFork).where(
            CanonFork.id == fork_id,
            CanonFork.owner_id == owner_id,
            CanonFork.novel_id == novel_id,
        )
    )
    if fork is None:
        raise ForkMaterializeError(
            "fork_not_found", "canon fork not found in the owner/novel scope"
        )

    approval, model, _run = await _validate_materialization_context(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        fork=fork,
        approval_request_id=approval_request_id,
        artifact_revision_id=artifact_revision_id,
    )
    materialization_hash = canonical_fork_materialization_hash(
        fork_id=fork.id,
        manifest_hash=fork.manifest_hash,
        delta_content_hash=model.delta.content_hash,
        approval_request_id=approval.id,
    )
    if fork.status == "approved":
        return ForkMaterializeOutcome(
            fork=fork, materialization_hash=materialization_hash, replayed=True
        )

    # The only authoritative mutation: the status projection (append-only fork).
    fork.status = "approved"
    await db.flush()
    return ForkMaterializeOutcome(
        fork=fork, materialization_hash=materialization_hash, replayed=False
    )


__all__ = [
    "CANON_FORK_APPROVAL_ACTION",
    "CANON_FORK_APPROVAL_PREFIX",
    "CANON_FORK_APPROVAL_SCHEMA_VERSION",
    "CANON_FORK_MATERIALIZATION_PREFIX",
    "ForkMaterializeError",
    "ForkMaterializeOutcome",
    "ForkProposalCreateResult",
    "ForkProposalError",
    "build_fork_approval_payload",
    "canonical_fork_approval_hash",
    "canonical_fork_materialization_hash",
    "create_fork_proposal",
    "delta_content_hash",
    "materialize_approved_fork",
]
