"""Hash-verified illustration anchor validation (Phase 34-01, REQ-VIS-05).

D-34-01 / D-34-03: an approved illustration stays consistent between the
reader and every export through a hash-verified anchor bound to
owner/novel/chapter, an immutable source snapshot, exact source coordinates
and the proposal-ready AssetRevision. This module owns the pure, replayable
exact-anchor validator:

- ``validate_exact_source`` — fail-closed hash/offset/version validation of an
  anchor source span. A hash or range mismatch is ``invalid``; the validator
  never searches for the excerpt and never auto-relocates to a nearby paragraph
  (D-34-01).
- ``AnchorValidationService.validate_exact`` — server-side owner/novel scope
  guard around the pure gate. The service only produces ``proposed`` or
  ``invalid``; publish/repair status is owned by the 34-05 deterministic
  publish transaction.

Everything here is read-only: nothing writes to the database, nothing switches
an active pointer and nothing becomes reader/export visible before the
deterministic publish transaction (D-34-01/02).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.illustration import AssetRevision
from app.schemas.illustration import IllustrationApprovalState, IllustrationRightsStatus
from app.schemas.illustration_anchor import (
    AnchorStatus,
    AnchorValidationResult,
    IllustrationAnchorProposalContract,
    validate_anchor_proposal_contract,
)

# Only ``cleared`` rights resolve an anchor proposal; unreviewed/pending/denied
# all leave a proposal unable to become reader/export visible (D-33-03).
_RIGHTS_BLOCKED_FOR_PROPOSAL: frozenset[str] = frozenset(
    {
        IllustrationRightsStatus.UNREVIEWED.value,
        IllustrationRightsStatus.PENDING.value,
        IllustrationRightsStatus.DENIED.value,
    }
)


class AnchorValidationGateError(ValueError):
    """A fail-closed gate violation while validating an anchor proposal."""


class AnchorValidationService:
    """Server-side exact-anchor validation (owner/novel scoped, read-only).

    The service produces only ``proposed`` or ``invalid``; a valid published
    anchor is created by the deterministic publish transaction (34-05).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate_exact(
        self,
        *,
        owner_id: int,
        novel_id: int,
        proposal: IllustrationAnchorProposalContract,
        chapter_content: str | None = None,
    ) -> AnchorValidationResult:
        """Re-validate an anchor proposal against persisted asset state.

        - owner/novel scope must be explicit positive integers;
        - the proposal asset must be persisted, proposal-ready and cleared;
        - the exact source hash/range/version must replay (``proposed`` or
          ``invalid``, never a nearest-match relocation).
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if proposal.owner_id != owner_id or proposal.novel_id != novel_id:
            raise AnchorValidationGateError(
                "anchor proposal scope does not match request scope"
            )
        asset = await self._asset(owner_id, novel_id, proposal.proposal_asset.id)
        if asset is None:
            raise AnchorValidationGateError(
                "proposal asset not found in the owner/novel scope"
            )
        if asset.approval_state != IllustrationApprovalState.PROPOSAL_READY.value:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="asset_not_proposal_ready",
                detail=(
                    "an anchor proposal only accepts a proposal-ready AssetRevision "
                    "(D-33-03/34-01)"
                ),
            )
        if asset.rights_status in _RIGHTS_BLOCKED_FOR_PROPOSAL:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="asset_rights_unresolved",
                detail=(
                    "asset rights must be cleared before an anchor proposal can proceed"
                ),
            )
        if proposal.proposal_asset.bytes_hash != asset.bytes_hash:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="asset_hash_drift",
                detail="proposal asset snapshot does not match the persisted asset",
            )
        return validate_anchor_proposal_contract(
            proposal, chapter_content=chapter_content
        )

    # --------------------------------------------------------------- queries

    async def _asset(
        self, owner_id: int, novel_id: int, asset_id: int
    ) -> AssetRevision | None:
        return await self._session.scalar(
            select(AssetRevision).where(
                AssetRevision.owner_id == owner_id,
                AssetRevision.novel_id == novel_id,
                AssetRevision.id == asset_id,
            )
        )

    @staticmethod
    def _require_scope(*, owner_id: int, novel_id: int) -> None:
        values = (owner_id, novel_id)
        if any(type(value) is not int or value <= 0 for value in values):
            raise AnchorValidationGateError(
                "scope identifiers must be explicit positive integers"
            )


# Re-export the pure gate so callers can validate without a database and keep
# a single canonical entry point for the anchor contract (D-34-01).
def validate_exact_proposal(
    proposal: IllustrationAnchorProposalContract,
    *,
    chapter_content: str | None = None,
    asset: Any | None = None,
) -> AnchorValidationResult:
    """Pure proposal gate with optional persisted-asset drift check.

    Mirrors ``AnchorValidationService.validate_exact`` for unit tests without a
    database: when ``asset`` is given (an ORM ``AssetRevision`` or a mapping
    with ``approval_state`` / ``rights_status`` / ``bytes_hash``), the same
    proposal-ready/rights/hash gates fail closed.
    """
    if asset is not None:
        approval_state = (
            asset.get("approval_state")
            if isinstance(asset, dict)
            else getattr(asset, "approval_state", None)
        )
        rights_status = (
            asset.get("rights_status")
            if isinstance(asset, dict)
            else getattr(asset, "rights_status", None)
        )
        bytes_hash = (
            asset.get("bytes_hash")
            if isinstance(asset, dict)
            else getattr(asset, "bytes_hash", None)
        )
        if approval_state != IllustrationApprovalState.PROPOSAL_READY.value:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="asset_not_proposal_ready",
                detail="an anchor proposal only accepts a proposal-ready AssetRevision",
            )
        if rights_status in _RIGHTS_BLOCKED_FOR_PROPOSAL:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="asset_rights_unresolved",
                detail="asset rights must be cleared before an anchor proposal",
            )
        if bytes_hash is not None and proposal.proposal_asset.bytes_hash != bytes_hash:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="asset_hash_drift",
                detail="proposal asset snapshot does not match the persisted asset",
            )
    return validate_anchor_proposal_contract(proposal, chapter_content=chapter_content)


__all__ = [
    "AnchorValidationGateError",
    "AnchorValidationService",
    "validate_exact_proposal",
]
