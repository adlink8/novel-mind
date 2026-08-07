"""Visual Bible review and versioning envelope (Phase 30-04, REQ-VIS-01).

D-30-01..D-30-04: approval is an explicit, append-only human/machine action
that only moves a candidate revision's review projection. This module owns
the *review/versioning seam*:

- ``evaluate_approval_gate`` — the pure, replayable fail-closed approval gate:
  an approval cannot succeed while any ``canon_fact`` claim has no persisted
  evidence ref or any reference asset is not rights-cleared (``cleared`` is the
  only status that resolves rights). Everything else is ``rights_unresolved``.
- ``VisualBibleReviewService.append_event`` — server-side gated, append-only,
  idempotent review actions (approve/reject/edit/supersede/needs_relink).
  The service re-verifies owner/novel/version scope, re-runs the approval gate
  before an approval is appended, and persists an audit ``details`` payload
  (budget marker, lineage hashes, rights snapshot). Approval never touches
  Original Canon, ``Novel.cover_url`` or any active pointer.
- ``build_review_envelope`` / ``build_revision_ref`` — the versioned review
  envelope with history events, approval-gate reason codes, parent revision and
  an immutable revision ref that Phase 31/32 Scene Candidates can consume.

No provider call exists in Phase 30 (provider image generation is Phase 32-33),
so budget/cost/usage is explicitly persisted as ``not_applicable`` rather than
silently dropped. The low-level durable append stays in
``VisualBibleAuthorityService.apply_review`` (single append-only write path);
this module adds the gates and the envelope on top of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visual_bible import (
    VisualBibleReviewEvent,
    VisualBibleVersion,
    VisualClaim,
    VisualEvidenceRef as VisualEvidenceRefModel,
    VisualReferenceAsset,
)
from app.schemas.visual_bible import (
    StrictVisualBibleModel,
    VisualAuthority,
    VisualBibleVersionView,
    VisualReviewAction,
    VisualReviewEventInput,
    VisualReviewEventView,
    VisualReviewState,
    VisualRightsStatus,
)
from app.services.visual_bible.authority import (
    CandidateNotFoundError,
    GateViolationError,
    ScopeMismatchError,
    VisualBibleAuthorityService,
    load_version_view,
)

# Only ``cleared`` resolves rights for approval; unreviewed/pending/denied all
# leave a generated or reference asset unable to silently become canon.
RIGHTS_BLOCKED_FOR_APPROVAL: frozenset[VisualRightsStatus] = frozenset(
    {
        VisualRightsStatus.UNREVIEWED,
        VisualRightsStatus.PENDING,
        VisualRightsStatus.DENIED,
    }
)


# ---------------------------------------------------------------------------
# Pure, replayable approval gate (unit-testable without a database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalGateResult:
    """Fail-closed approval readiness with stable machine reason codes."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None
    unresolved_claims: tuple[str, ...] = ()
    unresolved_assets: tuple[str, ...] = ()


def evaluate_approval_gate(
    *,
    canon_claim_evidence_counts: Mapping[str, int],
    asset_rights: Mapping[str, VisualRightsStatus | str],
) -> ApprovalGateResult:
    """Fail closed unless every canon claim has evidence and every asset is
    rights-cleared. A version with no canon claims and no assets is clean."""
    no_evidence = [
        key for key, count in canon_claim_evidence_counts.items() if int(count) <= 0
    ]
    blocked_assets = [
        key
        for key, status in asset_rights.items()
        if VisualRightsStatus(status) in RIGHTS_BLOCKED_FOR_APPROVAL
    ]
    if no_evidence:
        return ApprovalGateResult(
            ok=False,
            reason_code="evidence_unresolved",
            detail=(
                f"{len(no_evidence)} canon_fact claim(s) have no persisted evidence ref"
            ),
            unresolved_claims=tuple(no_evidence),
        )
    if blocked_assets:
        return ApprovalGateResult(
            ok=False,
            reason_code="rights_unresolved",
            detail=(f"{len(blocked_assets)} reference asset(s) are not rights-cleared"),
            unresolved_assets=tuple(blocked_assets),
        )
    return ApprovalGateResult(ok=True)


# ---------------------------------------------------------------------------
# Review envelope contracts (immutable revision ref + gate view)
# ---------------------------------------------------------------------------


class VisualApprovalGateView(StrictVisualBibleModel):
    """Approval readiness surfaced on the envelope with a stable reason code."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None
    unresolved_claims: list[str] = Field(default_factory=list)
    unresolved_assets: list[str] = Field(default_factory=list)


class VisualRevisionRef(StrictVisualBibleModel):
    """Immutable versioned candidate artifact reference (Phase 31/32 consumer).

    Identity + content lineage only: ``version_id`` plus the manifest/source
    snapshot hashes and cutoff. Downstream Scene Candidates resolve to this ref
    and read the envelope for the live review state.
    """

    kind: Literal["visual_bible"] = "visual_bible"
    version_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=120)
    revision_number: int = Field(ge=1)
    parent_version_id: int | None = Field(default=None, gt=0)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)


class VisualBibleReviewEnvelope(StrictVisualBibleModel):
    """Review/versioning envelope: history, reason codes, lineage, revision ref."""

    version_id: int = Field(gt=0)
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=120)
    revision_number: int = Field(ge=1)
    parent_version_id: int | None = Field(default=None, gt=0)
    review_state: VisualReviewState
    revision_ref: VisualRevisionRef
    parent_revision_ref: VisualRevisionRef | None = None
    review_events: list[VisualReviewEventView] = Field(default_factory=list)
    approval_gate: VisualApprovalGateView | None = None


def build_revision_ref(
    version: VisualBibleVersion | VisualBibleVersionView,
) -> VisualRevisionRef:
    """Freeze the identity/content lineage of one version into a stable ref."""
    return VisualRevisionRef(
        version_id=version.id,
        version_key=version.version_key,
        revision_number=version.revision_number,
        parent_version_id=version.parent_version_id,
        manifest_hash=version.manifest_hash,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff_chapter=version.cutoff_chapter,
    )


def _gate_view(result: ApprovalGateResult) -> VisualApprovalGateView:
    return VisualApprovalGateView(
        ok=result.ok,
        reason_code=result.reason_code,
        detail=result.detail,
        unresolved_claims=list(result.unresolved_claims),
        unresolved_assets=list(result.unresolved_assets),
    )


# ---------------------------------------------------------------------------
# Server-side review service (gates + append-only delegation)
# ---------------------------------------------------------------------------


class VisualBibleReviewService:
    """Owner-scoped review seam: gated, append-only, explicit review actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_event(
        self,
        *,
        owner_id: int,
        novel_id: int,
        event: VisualReviewEventInput,
    ) -> VisualBibleVersion:
        """Append one review action after server-side scope/gate checks.

        The approval gate fails closed before any row is written: no evidence on
        a canon_fact claim or a non-cleared reference asset blocks approval with
        a stable reason code. A repeated ``event_key`` replays the existing
        state and never appends a second event (idempotent, D-30-04).
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if event.owner_id != owner_id or event.novel_id != novel_id:
            raise ScopeMismatchError("review event scope does not match request scope")

        version = await self._version(
            owner_id=owner_id, novel_id=novel_id, version_id=event.version_id
        )
        if version is None:
            raise CandidateNotFoundError(
                "candidate version not found in explicit owner/novel scope"
            )

        existing = await self._session.scalar(
            select(VisualBibleReviewEvent).where(
                VisualBibleReviewEvent.owner_id == owner_id,
                VisualBibleReviewEvent.novel_id == novel_id,
                VisualBibleReviewEvent.version_id == version.id,
                VisualBibleReviewEvent.event_key == event.event_key,
            )
        )
        if existing is not None:
            return version  # idempotent replay: no second event, no state change

        gate: ApprovalGateResult | None = None
        if event.action is VisualReviewAction.APPROVE:
            gate = await self._approval_gate(
                owner_id=owner_id, novel_id=novel_id, version_id=version.id
            )
            if not gate.ok:
                raise GateViolationError(
                    f"approval blocked by {gate.reason_code}: {gate.detail}"
                )

        details = await self._event_details(
            owner_id=owner_id,
            novel_id=novel_id,
            version=version,
            gate=gate,
        )
        authority = VisualBibleAuthorityService(self._session)
        return await authority.apply_review(
            owner_id=owner_id,
            novel_id=novel_id,
            event=event,
            details=details,
        )

    # ------------------------------------------------------------------ gates

    async def _approval_gate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> ApprovalGateResult:
        """Re-run the fail-closed gate against persisted rows (fresh authority)."""
        claim_rows = (
            await self._session.execute(
                select(
                    VisualClaim.id,
                    VisualClaim.claim_key,
                    func.count(VisualEvidenceRefModel.id).label("evidence_count"),
                )
                .outerjoin(
                    VisualEvidenceRefModel,
                    (VisualEvidenceRefModel.claim_id == VisualClaim.id)
                    & (VisualEvidenceRefModel.owner_id == owner_id)
                    & (VisualEvidenceRefModel.novel_id == novel_id)
                    & (VisualEvidenceRefModel.version_id == version_id),
                )
                .where(
                    VisualClaim.owner_id == owner_id,
                    VisualClaim.novel_id == novel_id,
                    VisualClaim.version_id == version_id,
                    VisualClaim.authority == VisualAuthority.CANON_FACT.value,
                )
                .group_by(VisualClaim.id, VisualClaim.claim_key)
            )
        ).all()
        counts = {claim_key: int(count) for _cid, claim_key, count in claim_rows}

        asset_rows = (
            await self._session.execute(
                select(
                    VisualReferenceAsset.asset_key,
                    VisualReferenceAsset.rights_status,
                ).where(
                    VisualReferenceAsset.owner_id == owner_id,
                    VisualReferenceAsset.novel_id == novel_id,
                    VisualReferenceAsset.version_id == version_id,
                )
            )
        ).all()
        asset_rights = {asset_key: status for asset_key, status in asset_rows}

        return evaluate_approval_gate(
            canon_claim_evidence_counts=counts,
            asset_rights=asset_rights,
        )

    async def _event_details(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version: VisualBibleVersion,
        gate: ApprovalGateResult | None,
    ) -> dict[str, Any]:
        """Explicit audit payload: budget marker, lineage hashes, rights snapshot.

        Phase 30 has no provider calls, so budget/cost/usage is explicitly
        ``not_applicable`` instead of silently missing (must-have: no silent
        degradation). The rights snapshot makes approval replayable.
        """
        asset_rows = (
            await self._session.execute(
                select(
                    VisualReferenceAsset.asset_key,
                    VisualReferenceAsset.rights_status,
                ).where(
                    VisualReferenceAsset.owner_id == owner_id,
                    VisualReferenceAsset.novel_id == novel_id,
                    VisualReferenceAsset.version_id == version.id,
                )
            )
        ).all()
        return {
            "budget": {
                "provider_calls": 0,
                "credits_used": 0,
                "status": "not_applicable",
            },
            "lineage": {
                "source_snapshot_hash": version.source_snapshot_hash,
                "manifest_hash": version.manifest_hash,
                "cutoff_chapter": version.cutoff_chapter,
            },
            "rights": [
                {"asset_key": asset_key, "rights_status": status}
                for asset_key, status in asset_rows
            ],
            "approval_gate": (
                None
                if gate is None
                else {
                    "ok": gate.ok,
                    "reason_code": gate.reason_code,
                    "unresolved_claims": list(gate.unresolved_claims),
                    "unresolved_assets": list(gate.unresolved_assets),
                }
            ),
        }

    # ------------------------------------------------------------------ helpers

    async def _version(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> VisualBibleVersion | None:
        return await self._session.scalar(
            select(VisualBibleVersion).where(
                VisualBibleVersion.owner_id == owner_id,
                VisualBibleVersion.novel_id == novel_id,
                VisualBibleVersion.id == version_id,
            )
        )

    @staticmethod
    def _require_scope(*, owner_id: int, novel_id: int) -> None:
        values = (owner_id, novel_id)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ScopeMismatchError(
                "scope identifiers must be explicit positive integers"
            )


# ---------------------------------------------------------------------------
# Owner-scoped envelope builder (history + reason codes + lineage + revision ref)
# ---------------------------------------------------------------------------


async def build_review_envelope(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> VisualBibleReviewEnvelope:
    """Read envelope for one owned version; 404-equivalent on scope."""
    view = await load_version_view(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    revision_ref = build_revision_ref(view)

    parent_revision_ref: VisualRevisionRef | None = None
    if view.parent_version_id is not None:
        try:
            parent_view = await load_version_view(
                session,
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=view.parent_version_id,
            )
            parent_revision_ref = build_revision_ref(parent_view)
        except CandidateNotFoundError:
            parent_revision_ref = None  # orphaned lineage stays visible, not fatal

    approval_gate: VisualApprovalGateView | None = None
    if view.review_state in (
        VisualReviewState.CANDIDATE,
        VisualReviewState.NEEDS_RELINK,
    ):
        service = VisualBibleReviewService(session)
        approval_gate = _gate_view(
            await service._approval_gate(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
        )

    return VisualBibleReviewEnvelope(
        version_id=view.id,
        owner_id=view.owner_id,
        novel_id=view.novel_id,
        version_key=view.version_key,
        revision_number=view.revision_number,
        parent_version_id=view.parent_version_id,
        review_state=view.review_state,
        revision_ref=revision_ref,
        parent_revision_ref=parent_revision_ref,
        review_events=view.review_events,
        approval_gate=approval_gate,
    )
