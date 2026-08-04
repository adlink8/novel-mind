"""Explicit anchor repair after text/version changes (Phase 34-03, REQ-VIS-05).

D-34-03: text/version changes produce ``valid`` / ``needs_repair`` / ``invalid``
anchor status; a repair proposes a **new candidate anchor** and requires review.
Repair is an explicit candidate flow — it never silently relocates to a nearby
paragraph (D-34-01) and never mutates the published anchor content. This module
owns the three server-authoritative repair boundaries:

- ``classify_anchor_repair`` — pure, replayable classification of a published
  anchor against the *current* chapter text/version:
    * ``valid`` — the frozen source span/hash/snapshot still replays exactly;
    * ``needs_repair`` — the chapter text/version drifted (content hash changed
      or source snapshot id/hash changed); the anchor is stale but its lineage
      is intact;
    * ``invalid`` — the anchor itself is inconsistent (anchor hash does not
      replay from the frozen excerpt, malformed hashes, or the span does not
      replay against an *unchanged* chapter) and cannot be repaired.
  The classification carries the machine-readable evidence diff (previous/
  current content hash, previous/current snapshot, frozen span) so a repair
  proposal is auditable and replayable.
- ``AnchorRepairService.revalidate`` — owner/novel-scoped revalidation that
  loads the published anchor + current chapter and persists the status
  projection (``status`` is the single mutable column on a published anchor,
  D-34-03 explicit stale presentation). A stale anchor is never relocated.
- ``propose_anchor_repair`` / ``approve_anchor_repair`` — the explicit
  candidate flow. ``propose_anchor_repair`` only accepts a ``needs_repair``
  anchor and a caller-supplied exact new span + proposal-ready AssetRevision
  (same proposal gate as 34-05, D-34-01), then creates a repair proposal row +
  pending Web ApprovalRequest whose frozen payload carries the repair lineage
  (``repair_anchor_id`` / ``repair_of_anchor_key`` / reason / evidence).
  ``approve_anchor_repair`` re-verifies the old anchor is still stale, then
  delegates to the deterministic publisher (34-05) so the new valid anchor is
  created only from a server-authoritatively approved action with an exact
  span — the old anchor row is preserved as history, never overwritten.

Authority boundaries mirror ``publish.py``: Agent/browser/Pi can only propose;
FastAPI owns state and the deterministic publisher owns approved publication.
Cross-owner / cross-chapter / version / asset mismatches fail closed with no
authoritative write and no silent mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pydantic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.agent_runtime import ApprovalRequest
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import (
    IllustrationAnchor,
    IllustrationAnchorProposal,
)
from app.models.novel import Chapter
from app.schemas.illustration_anchor import (
    ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION,
    AnchorCopy,
    AnchorGateError,
    AnchorRange,
    AnchorStatus,
    IllustrationAnchorProposalContract,
    build_anchor_proposal_idempotency_key,
    canonical_anchor_hash,
    source_span_hash,
)
from app.services.illustration_anchors.publish import (
    ANCHOR_APPROVAL_ACTIONS,
    approval_payload_for_proposal,
    publish_anchor,
    validate_branch_scope,
)
from app.services.illustration_anchors.validation import AnchorValidationService
from app.services.illustrations.review import build_proposal_ref


class AnchorRepairError(ValueError):
    """Explicit repair gate violation (fail closed, no silent replacement)."""


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise AnchorRepairError("scope identifiers must be explicit positive integers")


# ---------------------------------------------------------------------------
# Pure classification (D-34-03): valid / needs_repair / invalid + evidence diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorRepairClassification:
    """Machine-readable revalidation outcome with the frozen evidence diff.

    ``status`` is the D-34-03 outcome; ``reason_code`` / ``detail`` explain it.
    The evidence fields freeze the previous (published) and current text/version
    hashes plus the immutable anchor span so a repair proposal is auditable and
    replayable. A stale anchor never gets new coordinates here — the span fields
    always mirror the frozen published span (no nearest-match relocation).
    """

    status: AnchorStatus
    reason_code: str | None
    detail: str | None
    previous_content_hash: str | None = None
    current_content_hash: str | None = None
    previous_snapshot_id: str | None = None
    current_snapshot_id: str | None = None
    previous_snapshot_hash: str | None = None
    current_snapshot_hash: str | None = None
    source_start: int | None = None
    source_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None


def classify_anchor_repair(
    *,
    anchor_hash: str,
    chapter_content_hash: str,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    excerpt: str,
    source_start: int,
    source_end: int,
    paragraph_start: int | None = None,
    paragraph_end: int | None = None,
    current_content: str | None = None,
    current_content_hash: str | None = None,
    current_snapshot_id: str | None = None,
    current_snapshot_hash: str | None = None,
) -> AnchorRepairClassification:
    """Classify a published anchor against the current text/version (D-34-03).

    Fail-closed order:
      1. internal consistency — malformed/incorrect anchor hash or incomplete
         source snapshot ⇒ ``invalid`` (an inconsistent anchor cannot be
         repaired);
      2. text/version drift — current content hash or source snapshot differs
         from the frozen value ⇒ ``needs_repair`` (stale, never relocated);
      3. exact span — against an *unchanged* chapter the frozen span must still
         replay the excerpt ⇒ ``valid``; otherwise ``invalid``.
    """
    if not _is_hex64(anchor_hash) or not _is_hex64(chapter_content_hash):
        return AnchorRepairClassification(
            status=AnchorStatus.INVALID,
            reason_code="malformed_anchor_hash",
            detail="anchor_hash and chapter_content_hash must be 64-hex hashes",
            previous_content_hash=chapter_content_hash,
            current_content_hash=current_content_hash,
            source_start=source_start,
            source_end=source_end,
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )
    if not _is_hex64(source_snapshot_hash) or not source_snapshot_id.strip():
        return AnchorRepairClassification(
            status=AnchorStatus.INVALID,
            reason_code="source_snapshot_incomplete",
            detail="source_snapshot_hash must be a 64-hex hash with a non-empty id",
            previous_content_hash=chapter_content_hash,
            current_content_hash=current_content_hash,
            source_start=source_start,
            source_end=source_end,
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )
    if anchor_hash != source_span_hash(excerpt):
        return AnchorRepairClassification(
            status=AnchorStatus.INVALID,
            reason_code="anchor_hash_mismatch",
            detail="anchor_hash does not replay from the frozen source excerpt",
            previous_content_hash=chapter_content_hash,
            current_content_hash=current_content_hash,
            source_start=source_start,
            source_end=source_end,
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )

    effective_current_hash: str | None = None
    if current_content is not None:
        effective_current_hash = sha256(current_content.encode("utf-8")).hexdigest()
    elif current_content_hash is not None:
        effective_current_hash = current_content_hash

    # Text/version drift is stale (needs_repair) — even when the excerpt happens
    # to still exist at the frozen span, the frozen chapter content version no
    # longer matches the current text (D-34-03).
    if (
        effective_current_hash is not None
        and effective_current_hash != chapter_content_hash
    ):
        return AnchorRepairClassification(
            status=AnchorStatus.NEEDS_REPAIR,
            reason_code="text_version_drift",
            detail=(
                "chapter text changed since the anchor was published; the anchor "
                "is stale and must not relocate to a nearby paragraph"
            ),
            previous_content_hash=chapter_content_hash,
            current_content_hash=effective_current_hash,
            previous_snapshot_id=source_snapshot_id,
            current_snapshot_id=current_snapshot_id,
            previous_snapshot_hash=source_snapshot_hash,
            current_snapshot_hash=current_snapshot_hash,
            source_start=source_start,
            source_end=source_end,
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )
    if (
        current_snapshot_id is not None
        and current_snapshot_id != source_snapshot_id
    ) or (
        current_snapshot_hash is not None
        and current_snapshot_hash != source_snapshot_hash
    ):
        return AnchorRepairClassification(
            status=AnchorStatus.NEEDS_REPAIR,
            reason_code="source_snapshot_drift",
            detail=(
                "source snapshot version changed since the anchor was published; "
                "the anchor is stale and requires an explicit repair review"
            ),
            previous_content_hash=chapter_content_hash,
            current_content_hash=effective_current_hash,
            previous_snapshot_id=source_snapshot_id,
            current_snapshot_id=current_snapshot_id,
            previous_snapshot_hash=source_snapshot_hash,
            current_snapshot_hash=current_snapshot_hash,
            source_start=source_start,
            source_end=source_end,
            paragraph_start=paragraph_start,
            paragraph_end=paragraph_end,
        )

    if current_content is not None:
        if source_start < 0 or source_end > len(current_content):
            return AnchorRepairClassification(
                status=AnchorStatus.INVALID,
                reason_code="source_range_out_of_bounds",
                detail="source span is outside the unchanged chapter content",
                previous_content_hash=chapter_content_hash,
                current_content_hash=effective_current_hash,
                source_start=source_start,
                source_end=source_end,
                paragraph_start=paragraph_start,
                paragraph_end=paragraph_end,
            )
        if current_content[source_start:source_end] != excerpt:
            return AnchorRepairClassification(
                status=AnchorStatus.INVALID,
                reason_code="source_range_mismatch",
                detail=(
                    "source span does not replay the excerpt against an unchanged "
                    "chapter; the anchor is inconsistent, not merely stale"
                ),
                previous_content_hash=chapter_content_hash,
                current_content_hash=effective_current_hash,
                source_start=source_start,
                source_end=source_end,
                paragraph_start=paragraph_start,
                paragraph_end=paragraph_end,
            )

    return AnchorRepairClassification(
        status=AnchorStatus.VALID,
        reason_code=None,
        detail=None,
        previous_content_hash=chapter_content_hash,
        current_content_hash=effective_current_hash,
        previous_snapshot_id=source_snapshot_id,
        current_snapshot_id=current_snapshot_id,
        previous_snapshot_hash=source_snapshot_hash,
        current_snapshot_hash=current_snapshot_hash,
        source_start=source_start,
        source_end=source_end,
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
    )


def repair_proposal_key(
    *,
    anchor_id: int,
    source_start: int,
    source_end: int,
    excerpt: str,
    asset_revision_id: int,
) -> str:
    """Deterministic versioned candidate key for one repair proposal.

    The key derives from the repaired anchor id + the caller-supplied exact new
    span + asset, so re-proposing the same repair replays (append-only) and a
    different span always creates a distinct candidate (D-34-03).
    """
    token = canonical_anchor_hash(
        {
            "anchor_id": anchor_id,
            "source_start": source_start,
            "source_end": source_end,
            "excerpt": excerpt,
            "asset_revision_id": asset_revision_id,
        }
    )[:16]
    return f"repair:{anchor_id}:{token}"


# ---------------------------------------------------------------------------
# Owner/novel-scoped revalidation service (explicit stale presentation)
# ---------------------------------------------------------------------------


class AnchorRepairService:
    """Server-side anchor revalidation (owner/novel scoped, D-34-03).

    ``revalidate`` loads the published anchor and the current chapter, classifies
    it (valid / needs_repair / invalid) and optionally persists the status
    projection — the single mutable column on a published anchor. A stale anchor
    is surfaced explicitly; it is never relocated and its content never mutates.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def revalidate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        anchor_id: int,
        chapter_content: str | None = None,
        current_snapshot_id: str | None = None,
        current_snapshot_hash: str | None = None,
        persist_status: bool = True,
    ) -> AnchorRepairClassification:
        _require_scope(owner_id=owner_id, novel_id=novel_id)
        anchor = await self._anchor(owner_id, novel_id, anchor_id)
        if anchor is None:
            raise AnchorRepairError("published anchor not found in the owner/novel scope")
        chapter = await self._chapter(owner_id, novel_id, anchor.chapter_id)
        if chapter is None:
            raise AnchorRepairError("anchor chapter not found in the owner/novel scope")
        if chapter.chapter_number != anchor.chapter_number:
            raise AnchorRepairError(
                "chapter_number drifted from the anchor lineage (stale revision fails closed)"
            )
        content = chapter_content if chapter_content is not None else chapter.content
        classification = classify_anchor_repair(
            anchor_hash=anchor.anchor_hash,
            chapter_content_hash=anchor.chapter_content_hash,
            source_snapshot_id=anchor.source_snapshot_id,
            source_snapshot_hash=anchor.source_snapshot_hash,
            excerpt=anchor.excerpt,
            source_start=anchor.source_start,
            source_end=anchor.source_end,
            paragraph_start=anchor.paragraph_start,
            paragraph_end=anchor.paragraph_end,
            current_content=content,
            current_content_hash=(
                sha256(content.encode("utf-8")).hexdigest() if content is not None else None
            ),
            current_snapshot_id=current_snapshot_id,
            current_snapshot_hash=current_snapshot_hash,
        )
        if persist_status and anchor.status != classification.status.value:
            anchor.status = classification.status.value
            await self._session.flush()
        return classification

    # --------------------------------------------------------------- queries

    async def _anchor(
        self, owner_id: int, novel_id: int, anchor_id: int
    ) -> IllustrationAnchor | None:
        return await self._session.scalar(
            select(IllustrationAnchor).where(
                IllustrationAnchor.id == anchor_id,
                IllustrationAnchor.owner_id == owner_id,
                IllustrationAnchor.novel_id == novel_id,
            )
        )

    async def _chapter(
        self, owner_id: int, novel_id: int, chapter_id: int
    ) -> Chapter | None:
        return await self._session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(
                Chapter.id == chapter_id,
                Chapter.novel_id == novel_id,
            )
        )


async def _load_chapter(
    db: AsyncSession, *, owner_id: int, novel_id: int, chapter_id: int
) -> Chapter:
    chapter = await db.scalar(
        select(Chapter)
        .options(undefer(Chapter.content))
        .where(
            Chapter.id == chapter_id,
            Chapter.novel_id == novel_id,
        )
    )
    if chapter is None:
        raise AnchorRepairError("chapter not found in the owner/novel scope")
    return chapter


async def _load_asset(
    db: AsyncSession, *, owner_id: int, novel_id: int, asset_id: int
) -> AssetRevision:
    asset = await db.scalar(
        select(AssetRevision).where(
            AssetRevision.id == asset_id,
            AssetRevision.owner_id == owner_id,
            AssetRevision.novel_id == novel_id,
        )
    )
    if asset is None:
        raise AnchorRepairError("repair asset not found in the owner/novel scope")
    return asset


def _repair_contract(
    *,
    owner_id: int,
    novel_id: int,
    asset: AssetRevision,
    proposal_key: str,
    request: dict[str, Any],
) -> IllustrationAnchorProposalContract:
    """Construct the frozen repair candidate contract (exact span, D-34-01)."""
    return IllustrationAnchorProposalContract(
        owner_id=owner_id,
        novel_id=novel_id,
        chapter_id=int(request["chapter_id"]),
        chapter_number=int(request["chapter_number"]),
        proposal_key=proposal_key,
        source_snapshot_id=str(request["source_snapshot_id"]),
        source_snapshot_hash=str(request["source_snapshot_hash"]),
        range=AnchorRange(
            source_start=int(request["source_start"]),
            source_end=int(request["source_end"]),
            paragraph_start=request.get("paragraph_start"),
            paragraph_end=request.get("paragraph_end"),
        ),
        excerpt=str(request["excerpt"]),
        anchor_hash=str(request["anchor_hash"]),
        chapter_content_hash=str(request["chapter_content_hash"]),
        proposal_asset=build_proposal_ref(asset),
        presentation=AnchorCopy(
            caption=str(request["caption"]),
            alt_text=str(request["alt_text"]),
            citation=str(request["citation"]),
        ),
        # Placeholder filled by the caller from build_anchor_proposal_idempotency_key.
        idempotency_key="0" * 64,
    )


# ---------------------------------------------------------------------------
# Explicit repair candidate proposal (append-only, requires review)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorRepairProposeResult:
    """Repair proposal outcome: candidate row + pending approval + evidence."""

    proposal: IllustrationAnchorProposal
    approval_request: ApprovalRequest
    repaired_anchor: IllustrationAnchor
    classification: AnchorRepairClassification
    replayed: bool = False


async def propose_anchor_repair(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    anchor_id: int,
    request: dict[str, Any],
    action: str,
) -> AnchorRepairProposeResult:
    """Explicit repair candidate proposal (D-34-03, candidate-only).

    - The repaired anchor must revalidate to ``needs_repair`` against the
      *current* chapter text/version; a ``valid`` anchor (nothing to repair) and
      an ``invalid`` anchor (inconsistent, cannot be repaired) fail closed.
    - The caller must supply an exact new source span that replays against the
      current chapter and a proposal-ready AssetRevision (same gate as 34-05) —
      the service never searches for the excerpt and never auto-relocates.
    - The candidate proposal is append-only; its frozen approval payload carries
      the repair lineage (anchor id/key, reason, evidence diff). Nothing here
      publishes; the deterministic publisher owns approved repair publication.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if action not in ANCHOR_APPROVAL_ACTIONS:
        raise AnchorRepairError(
            f"unknown Phase 34 repair action {action!r}; allowed: "
            f"{sorted(ANCHOR_APPROVAL_ACTIONS)}"
        )
    anchor = await db.scalar(
        select(IllustrationAnchor).where(
            IllustrationAnchor.id == anchor_id,
            IllustrationAnchor.owner_id == owner_id,
            IllustrationAnchor.novel_id == novel_id,
        )
    )
    if anchor is None:
        raise AnchorRepairError("published anchor not found in the owner/novel scope")
    if int(request["chapter_id"]) != anchor.chapter_id:
        raise AnchorRepairError(
            "repair chapter does not match the anchor chapter (cross-chapter repair fails closed)"
        )
    if int(request["chapter_number"]) != anchor.chapter_number:
        raise AnchorRepairError(
            "repair chapter_number does not match the anchor lineage (wrong version fails closed)"
        )
    chapter = await _load_chapter(
        db, owner_id=owner_id, novel_id=novel_id, chapter_id=anchor.chapter_id
    )
    asset = await _load_asset(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        asset_id=int(request["asset_revision_id"]),
    )
    classification = await AnchorRepairService(db).revalidate(
        owner_id=owner_id,
        novel_id=novel_id,
        anchor_id=anchor.id,
        chapter_content=chapter.content,
        persist_status=False,
    )
    if classification.status is not AnchorStatus.NEEDS_REPAIR:
        raise AnchorRepairError(
            f"anchor {anchor.id} revalidates as {classification.status.value!r}; "
            "only a needs_repair anchor can be repaired (fail closed)"
        )

    authority_space = (
        "derivative" if (request.get("branch") or request.get("fork")) else "original"
    )
    branch = request.get("branch")
    fork = request.get("fork") if authority_space == "derivative" else None
    proposal_key = repair_proposal_key(
        anchor_id=anchor.id,
        source_start=int(request["source_start"]),
        source_end=int(request["source_end"]),
        excerpt=str(request["excerpt"]),
        asset_revision_id=int(request["asset_revision_id"]),
    )
    try:
        validate_branch_scope(authority_space=authority_space, branch=branch, fork=fork)
        contract = _repair_contract(
            owner_id=owner_id,
            novel_id=novel_id,
            asset=asset,
            proposal_key=proposal_key,
            request=request,
        )
    except (AnchorGateError, pydantic.ValidationError) as exc:
        raise AnchorRepairError(f"anchor repair gate blocked: {exc}") from exc
    idempotency_key = build_anchor_proposal_idempotency_key(contract)
    contract = contract.model_copy(update={"idempotency_key": idempotency_key})

    # Exact source hash/range/version gate (proposal-ready asset + cleared rights).
    validation = await AnchorValidationService(db).validate_exact(
        owner_id=owner_id,
        novel_id=novel_id,
        proposal=contract,
        chapter_content=chapter.content,
    )
    if not validation.ok:
        raise AnchorRepairError(
            f"anchor repair gate blocked ({validation.reason_code}): "
            f"{validation.detail or 'invalid exact source span'}"
        )

    payload = approval_payload_for_proposal(
        contract,
        action=action,
        authority_space=authority_space,
        branch=branch,
        fork=fork,
    )
    # Frozen repair lineage (D-34-03): who/what/why this candidate repairs.
    payload.update(
        {
            "repair_anchor_id": anchor.id,
            "repair_of_anchor_key": anchor.anchor_key,
            "repair_previous_status": classification.status.value,
            "repair_reason_code": classification.reason_code,
            "repair_reason_detail": classification.detail,
            "repair_previous_content_hash": classification.previous_content_hash,
            "repair_current_content_hash": classification.current_content_hash,
        }
    )
    payload_hash = canonical_anchor_hash(payload)

    existing = await db.scalar(
        select(IllustrationAnchorProposal).where(
            IllustrationAnchorProposal.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        approval = await db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.id == existing.approval_request_id
            )
        )
        if approval is None or approval.action != action:
            raise AnchorRepairError(
                "a repair proposal with this span/asset already exists under a "
                "different Phase 34 action; replay the existing proposal instead"
            )
        return AnchorRepairProposeResult(
            proposal=existing,
            approval_request=approval,
            repaired_anchor=anchor,
            classification=classification,
            replayed=True,
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
        action=action,
        payload_summary={
            "repair_of_anchor_key": anchor.anchor_key,
            "repair_anchor_id": anchor.id,
            "chapter_number": contract.chapter_number,
            "source_start": contract.range.source_start,
            "source_end": contract.range.source_end,
            "anchor_hash": contract.anchor_hash,
            "asset_revision_id": contract.proposal_asset.id,
            "caption": contract.presentation.caption,
            "reason_code": classification.reason_code,
            "authority_space": authority_space,
            "branch": branch,
            "fork": fork,
        },
        payload_hash=payload_hash,
        status="pending",
        expires_at=None,
    )
    db.add(approval)
    await db.flush()

    row = IllustrationAnchorProposal(
        owner_id=owner_id,
        novel_id=novel_id,
        chapter_id=contract.chapter_id,
        chapter_number=contract.chapter_number,
        proposal_key=contract.proposal_key,
        source_snapshot_id=contract.source_snapshot_id,
        source_snapshot_hash=contract.source_snapshot_hash,
        paragraph_start=contract.range.paragraph_start,
        paragraph_end=contract.range.paragraph_end,
        source_start=contract.range.source_start,
        source_end=contract.range.source_end,
        excerpt=contract.excerpt,
        anchor_hash=contract.anchor_hash,
        chapter_content_hash=contract.chapter_content_hash,
        proposal_asset_revision_id=contract.proposal_asset.id,
        approval_request_id=approval.id,
        status="pending_approval",
        caption=contract.presentation.caption,
        alt_text=contract.presentation.alt_text,
        citation=contract.presentation.citation,
        canonical_payload=payload,
        canonical_payload_hash=payload_hash,
        idempotency_key=idempotency_key,
        projection_hash=payload_hash,
        schema_version=ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION,
    )
    db.add(row)
    await db.flush()
    return AnchorRepairProposeResult(
        proposal=row,
        approval_request=approval,
        repaired_anchor=anchor,
        classification=classification,
    )


# ---------------------------------------------------------------------------
# Approved repair application (deterministic publisher, old anchor preserved)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorRepairApproveResult:
    """Approved repair outcome: new published anchor + preserved old anchor."""

    anchor: IllustrationAnchor
    repaired_anchor: IllustrationAnchor
    classification: AnchorRepairClassification


async def approve_anchor_repair(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    proposal_id: int,
) -> AnchorRepairApproveResult:
    """Apply an approved repair: publish the candidate, preserve the old anchor.

    - The proposal must carry the repair lineage (it must have been created by
      ``propose_anchor_repair``, not a plain proposal).
    - The repaired old anchor must still revalidate to ``needs_repair``; a
      reverted (now ``valid``) or inconsistent (``invalid``) anchor fails closed.
    - The deterministic publisher (34-05) re-verifies the server-authoritative
      approved action, the proposal-ready asset and the exact new span against
      the current chapter, then creates the new ``valid`` anchor + frozen
      manifest. The old anchor row is preserved as history — no silent
      mutation, no deletion, no relocation.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    proposal = await db.scalar(
        select(IllustrationAnchorProposal).where(
            IllustrationAnchorProposal.id == proposal_id,
            IllustrationAnchorProposal.owner_id == owner_id,
            IllustrationAnchorProposal.novel_id == novel_id,
        )
    )
    if proposal is None:
        raise AnchorRepairError("repair proposal not found in the owner/novel scope")
    payload = dict(proposal.canonical_payload or {})
    repair_anchor_id = payload.get("repair_anchor_id")
    repair_of_anchor_key = payload.get("repair_of_anchor_key")
    if not repair_anchor_id or not repair_of_anchor_key:
        raise AnchorRepairError(
            "proposal is not a repair candidate (no repair lineage); publish the "
            "proposal through the deterministic publish surface instead"
        )
    old_anchor = await db.scalar(
        select(IllustrationAnchor).where(
            IllustrationAnchor.id == int(repair_anchor_id),
            IllustrationAnchor.owner_id == owner_id,
            IllustrationAnchor.novel_id == novel_id,
        )
    )
    if old_anchor is None or old_anchor.anchor_key != repair_of_anchor_key:
        raise AnchorRepairError(
            "repaired anchor not found in the owner/novel scope (lineage mismatch fails closed)"
        )
    chapter = await _load_chapter(
        db, owner_id=owner_id, novel_id=novel_id, chapter_id=old_anchor.chapter_id
    )
    classification = await AnchorRepairService(db).revalidate(
        owner_id=owner_id,
        novel_id=novel_id,
        anchor_id=old_anchor.id,
        chapter_content=chapter.content,
        persist_status=True,
    )
    if classification.status is not AnchorStatus.NEEDS_REPAIR:
        raise AnchorRepairError(
            f"repaired anchor revalidates as {classification.status.value!r}; only "
            "a needs_repair anchor can be repaired (fail closed)"
        )
    # The deterministic publisher re-verifies the approved action, the
    # proposal-ready asset and the exact new span against the current chapter.
    new_anchor = await publish_anchor(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        proposal_id=proposal.id,
    )
    return AnchorRepairApproveResult(
        anchor=new_anchor,
        repaired_anchor=old_anchor,
        classification=classification,
    )


__all__ = [
    "AnchorRepairApproveResult",
    "AnchorRepairClassification",
    "AnchorRepairError",
    "AnchorRepairProposeResult",
    "AnchorRepairService",
    "approve_anchor_repair",
    "classify_anchor_repair",
    "propose_anchor_repair",
    "repair_proposal_key",
]
