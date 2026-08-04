"""Agent derivative-edit proposal apply API (Phase 36-05, REQ-FORK-02 / REQ-AGENT-03/04/07).

``POST /api/agent/derivative-edit-proposals/{artifact_id}/apply`` is the
**agent_proposal** path owned by this module. It is the sole HTTP delegation
boundary for the deterministic Revision Service apply: it forwards only an
approved, validated ``DerivativeEditProposal`` artifact revision and the
deterministic Revision Service (``apply_agent_edit``) owns the append-only
``agent_proposal`` CAS apply. Agent Service / browser can never apply a
derivative edit directly, and a user autosave can never satisfy an
``apply_derivative_edit`` ApprovalRequest.

Every apply attempt:

- loads the Artifact + its immutable revision in the authenticated owner scope
  (foreign/missing id is an identical 404);
- replays the frozen ``DerivativeEditProposalArtifact`` envelope (wire schema,
  owner/novel/branch/skill_version/input_hash lineage, content hash,
  source snapshot) against the SkillRun;
- finds the server-authoritative ``apply_derivative_edit`` ApprovalRequest and
  requires an approved/approved_for_session decision whose payload hash replays
  from the envelope (forged/expired/cancelled/rejected → fail closed);
- CAS-applies through the deterministic Revision Service (stale base → 409 with
  the latest revision, never last-write-wins);
- emits only ``derivative.agent_proposal.applied|rejected`` events (never a
  ``derivative.user_autosave.*`` event).
"""

from __future__ import annotations

from typing import Any, Literal

import pydantic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import User
from app.models.agent_runtime import ApprovalRequest, Artifact, SkillRun
from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_project import DerivativeProject
from app.schemas.agent_runtime import DerivativeEditProposalArtifact
from app.schemas.derivative_chapter import DerivativeChapterView
from app.schemas.derivative_revision import DerivativeRevisionView
from app.services.derivative_editor.events import (
    DERIVATIVE_AGENT_PROPOSAL_APPLIED,
    DERIVATIVE_AGENT_PROPOSAL_REJECTED,
    build_agent_proposal_event,
    emit_derivative_event,
)
from app.services.derivative_editor.revisions import (
    DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION,
    DerivativeRevisionError,
    apply_agent_edit,
    build_derivative_edit_approval_payload,
    canonical_derivative_edit_approval_hash,
    derivative_edit_content_hash,
    to_chapter_view,
    to_revision_view,
)

router = APIRouter(dependencies=[Depends(require_user)])


class DerivativeEditApplyRequest(BaseModel):
    """Explicit artifact revision selection (optional; defaults to the current).

    The client can never widen the scope — owner/novel/branch/project/chapter/
    base_revision/content are all re-validated by the deterministic Revision
    Service from the frozen artifact revision.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_revision_id: int | None = Field(default=None, gt=0)


class DerivativeEditApplyResponse(BaseModel):
    """Deterministic apply acknowledgement for an approved agent proposal."""

    model_config = ConfigDict(extra="forbid")

    applied: bool
    status: Literal["applied", "noop"]
    event: str
    artifact_id: int
    artifact_revision_id: int
    proposal_key: str
    base_revision: int
    validator_report: dict[str, Any]
    chapter: DerivativeChapterView
    revision: DerivativeRevisionView


def _fail(status_code: int, code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": detail}
    )


async def _load_artifact_revision(
    db: AsyncSession,
    *,
    artifact_id: int,
    owner_id: int,
    revision_id: int | None,
) -> tuple[Artifact, Any]:
    """Load the Artifact + the exact immutable revision in the owner scope."""
    from app.models.agent_runtime import ArtifactRevision

    artifact = await db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_id == owner_id,
        )
    )
    if artifact is None:
        raise _fail(404, "artifact_not_found", "artifact not found in the owner scope")
    revision = await db.get(ArtifactRevision, revision_id or artifact.current_revision_id)
    if revision is None or revision.artifact_id != artifact.id:
        raise _fail(
            404, "artifact_revision_not_found", "artifact revision not found in scope"
        )
    return artifact, revision


def _validate_envelope(revision) -> tuple[DerivativeEditProposalArtifact, dict[str, Any]]:
    try:
        model = DerivativeEditProposalArtifact.model_validate(revision.content)
    except pydantic.ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        raise _fail(
            400,
            "schema_drift",
            f"derivative edit proposal envelope failed strict wire validation "
            f"({loc}: {first.get('msg', 'invalid')})",
        ) from exc
    return model, dict(revision.content)


def _replay_lineage(
    *,
    model: DerivativeEditProposalArtifact,
    run: SkillRun,
    owner_id: int,
    novel_id: int,
) -> None:
    if model.owner_id != owner_id or model.novel_id != novel_id:
        raise _fail(
            409,
            "proposal_lineage_mismatch",
            "proposal envelope owner/novel lineage does not match the apply scope",
        )
    if model.skill_version_id != run.skill_version_id:
        raise _fail(
            409,
            "proposal_lineage_mismatch",
            "proposal envelope skill_version_id does not match the SkillRun lineage",
        )
    if model.input_hash != run.input_hash:
        raise _fail(
            409,
            "stale_revision",
            "proposal envelope input_hash does not replay the SkillRun input_hash "
            "(stale revision fails closed)",
        )
    if model.branch != run.branch:
        raise _fail(
            409,
            "branch_scope_mismatch",
            f"proposal envelope branch {model.branch!r} does not match the run "
            f"branch {run.branch!r} (wrong branch/fork fails closed)",
        )


async def _find_approved_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    run_id: int,
    model: DerivativeEditProposalArtifact,
    project: DerivativeProject,
) -> ApprovalRequest:
    """Find the approved apply_derivative_edit ApprovalRequest for this run and
    verify its payload hash replays from the frozen proposal envelope."""
    payload = model.proposal
    replayed_payload = build_derivative_edit_approval_payload(
        owner_id=model.owner_id,
        novel_id=model.novel_id,
        branch=model.branch,
        fork=payload.fork,
        proposal_key=payload.proposal_key,
        project_id=payload.project_id,
        chapter_id=payload.chapter_id,
        base_revision=payload.base_revision,
        content_hash=payload.content_hash,
        source_snapshot_hash=project.source_snapshot_hash,
    )
    replayed_hash = canonical_derivative_edit_approval_hash(replayed_payload)

    rows = list(
        (
            await db.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.action == DERIVATIVE_AGENT_EDIT_APPROVAL_ACTION,
                    ApprovalRequest.owner_id == owner_id,
                    ApprovalRequest.run_id == run_id,
                )
            )
        ).all()
    )
    matched = [row for row in rows if row.payload_hash == replayed_hash]
    if not matched:
        raise _fail(
            409,
            "approval_not_found",
            "no apply_derivative_edit approval replays this proposal payload "
            "(forged/drifted approval fails closed)",
        )
    approval = matched[0]
    if approval.status not in {"approved", "approved_for_session"}:
        raise _fail(
            409,
            "approval_not_approved",
            f"approval request {approval.id} is {approval.status!r}; only an "
            "approved decision may reach the deterministic Revision Service "
            "(forged/expired/cancelled/rejected decisions fail closed)",
        )
    if approval.fork_id is not None and approval.fork_id != project.fork_id:
        raise _fail(
            409,
            "approval_fork_mismatch",
            "the approval request is bound to a different fork (wrong branch/fork "
            "fails closed)",
        )
    return approval


@router.post(
    "/derivative-edit-proposals/{artifact_id}/apply",
    response_model=DerivativeEditApplyResponse,
)
async def apply_derivative_edit_proposal(
    artifact_id: int,
    body: DerivativeEditApplyRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeEditApplyResponse:
    """Deterministic apply boundary for an approved DerivativeEditProposal.

    Only the deterministic Revision Service may apply the approved proposal as
    an append-only ``agent_proposal`` revision (CAS, no last-write-wins); the
    agent_proposal path emits only ``derivative.agent_proposal.applied`` or
    ``derivative.agent_proposal.rejected``.
    """
    owner_id = current_user.id
    try:
        artifact, revision = await _load_artifact_revision(
            db,
            artifact_id=artifact_id,
            owner_id=owner_id,
            revision_id=body.artifact_revision_id if body else None,
        )
        model, _raw = _validate_envelope(revision)
        run = await db.get(SkillRun, artifact.run_id)
        if run is None:
            raise _fail(409, "run_not_found", "the SkillRun bound to the artifact is missing")
        _replay_lineage(model=model, run=run, owner_id=owner_id, novel_id=artifact.novel_id)

        payload = model.proposal
        if derivative_edit_content_hash(payload.content) != payload.content_hash:
            raise _fail(
                409,
                "content_hash_mismatch",
                "proposal content_hash does not replay from the proposal content "
                "(schema drift fails closed)",
            )

        project = await db.scalar(
            select(DerivativeProject).where(
                DerivativeProject.id == payload.project_id,
                DerivativeProject.owner_id == owner_id,
                DerivativeProject.novel_id == artifact.novel_id,
            )
        )
        if project is None:
            raise _fail(
                404,
                "project_not_found",
                "derivative project not found in the owner/novel scope",
            )
        if project.space != "fanfiction_canon":
            raise _fail(
                409,
                "wrong_authority_space",
                f"derivative project {project.id} is in space {project.space!r}; "
                "only fanfiction_canon projects accept derivative edits",
            )
        if payload.source_snapshot_hash != project.source_snapshot_hash:
            raise _fail(
                409,
                "source_snapshot_mismatch",
                "proposal source snapshot hash does not replay the project's frozen "
                "fork lineage (wrong branch/fork fails closed)",
            )

        chapter = await db.scalar(
            select(DerivativeChapter).where(
                DerivativeChapter.id == payload.chapter_id,
                DerivativeChapter.project_id == project.id,
                DerivativeChapter.owner_id == owner_id,
                DerivativeChapter.novel_id == artifact.novel_id,
            )
        )
        if chapter is None:
            raise _fail(
                404,
                "chapter_not_found",
                "derivative chapter not found in the owner/novel/project scope",
            )

        approval = await _find_approved_approval(
            db, owner_id=owner_id, run_id=artifact.run_id, model=model, project=project
        )

        # Deterministic Revision Service CAS apply (the only authoritative write).
        try:
            chapter_view, revision_view, status_str = await apply_agent_edit(
                db,
                owner_id=owner_id,
                novel_id=artifact.novel_id,
                project_id=project.id,
                chapter_id=chapter.id,
                content=payload.content,
                base_revision=payload.base_revision,
                actor_id=owner_id,
                reason=f"agent_proposal:{payload.proposal_key}:approval:{approval.id}",
            )
        except DerivativeRevisionError as exc:
            raise _fail(
                exc.status_code,
                exc.code,
                exc.detail,
            ) from exc

        event = build_agent_proposal_event(
            event=DERIVATIVE_AGENT_PROPOSAL_APPLIED,
            owner_id=owner_id,
            novel_id=artifact.novel_id,
            project_id=project.id,
            chapter_id=chapter.id,
            proposal_key=payload.proposal_key,
            base_revision=payload.base_revision,
            status=status_str,
        )
        emit_derivative_event(event)

        validator_report = {
            "verdict": "pass",
            "reason_codes": [],
            "lineage": {
                "owner_id": model.owner_id,
                "novel_id": model.novel_id,
                "branch": model.branch,
                "skill_version_id": model.skill_version_id,
                "input_hash_replayed": True,
            },
            "approval": {
                "request_id": approval.id,
                "action": approval.action,
                "payload_hash_replayed": True,
            },
            "base_revision": payload.base_revision,
        }
        return DerivativeEditApplyResponse(
            applied=True,
            status=status_str,
            event=DERIVATIVE_AGENT_PROPOSAL_APPLIED,
            artifact_id=artifact.id,
            artifact_revision_id=revision.id,
            proposal_key=payload.proposal_key,
            base_revision=payload.base_revision,
            validator_report=validator_report,
            chapter=chapter_view,
            revision=revision_view,
        )
    except HTTPException as exc:
        event_rejected = build_agent_proposal_event(
            event=DERIVATIVE_AGENT_PROPOSAL_REJECTED,
            owner_id=owner_id,
            novel_id=0,  # not yet resolved on the failure path
            project_id=0,
            chapter_id=0,
            proposal_key="unknown",
            base_revision=0,
            status="rejected",
        )
        emit_derivative_event(event_rejected)
        raise exc


__all__ = ["router"]
