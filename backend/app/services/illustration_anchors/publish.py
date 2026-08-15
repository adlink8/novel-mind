"""Hash-verified illustration anchor deterministic services (Phase 34-05, REQ-VIS-05).

D-34-01 / D-34-03 / REQ-AGENT-03/04/07: an approved illustration stays consistent
between the reader and every export through a hash-verified anchor bound to
owner/novel/chapter, an immutable source snapshot, exact source coordinates and
the proposal-ready AssetRevision. This module owns the two server-authoritative
transitions that Phase 34 permits outside the pure validator:

- ``create_anchor_proposal`` — the **candidate proposal gate**. It accepts only a
  proposal-ready AssetRevision (Phase 33 handoff) with cleared rights and an exact
  source hash/range/version (D-34-01), then atomically creates a ``proposed``
  IllustrationAnchorProposal row + a pending Web ApprovalRequest
  (action = ``publish_illustration`` / ``attach_illustration_to_text``,
  ``payload_hash`` = canonical replay hash of the frozen proposal payload, D-11/D-15)
  and moves the proposal to ``pending_approval``. Nothing here publishes.
- ``publish_anchor`` — the **deterministic publisher**. It atomically verifies the
  approved action and payload hash, the proposal-ready asset, the exact source
  hash/range/version against the *current* chapter content, and the owner/novel/
  authority-space/branch/fork scope; then it creates the published anchor row
  (status ``valid``), freezes the publish manifest (D-34-04) and moves the proposal
  to ``valid``. Any forged/expired/cancelled/rejected approval, stale hash, wrong
  branch/fork, missing branch scope or schema drift fails closed with no
  authoritative write (D-34-01).

Authority boundaries:
- Agent Service / browser / Pi can only propose; FastAPI owns state and the
  deterministic publisher owns approved publication. No shell, filesystem, ambient
  package or direct Original Canon / domain-table write path exists here.
- Proposals are append-only (ORM events reject in-place content mutation); the
  publisher only moves the status / approval / published-asset / manifest
  projection. Repairs propose a new candidate anchor (D-34-03).
- The published asset revision is the proposal-ready AssetRevision itself: the
  anchor freezes the asset ref (id, bytes hash, mime) into the manifest, so the
  reader/export consume exactly the approved binary without duplicating domain
  rows or mutating the append-only AssetRevision table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from app.schemas.illustration import IllustrationApprovalState
from app.schemas.illustration_anchor import (
    ILLUSTRATION_ANCHOR_ARTIFACT_KIND,
    ILLUSTRATION_ANCHOR_PROPOSAL_ARTIFACT_KIND,
    ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION,
    ILLUSTRATION_ANCHOR_SCHEMA_VERSION,
    AnchorCopy,
    AnchorGateError,
    AnchorPublishManifest,
    AnchorRange,
    AnchorStatus,
    IllustrationAnchorProposalContract,
    PublishedAssetRef,
    anchor_publish_manifest_hash,
    build_anchor_proposal_idempotency_key,
    canonical_anchor_hash,
)
from app.services.illustration_anchors.validation import (
    AnchorValidationService,
)
from app.services.illustrations.review import build_proposal_ref

# The only Phase 34 actions an Agent may request; both require a Web
# ApprovalRequest (D-11/D-15) and are owned by the deterministic publisher.
ANCHOR_APPROVAL_ACTIONS: frozenset[str] = frozenset(
    {"publish_illustration", "attach_illustration_to_text"}
)


class AnchorProposalError(ValueError):
    """Candidate proposal gate violation (fail closed, no row becomes valid)."""


class AnchorPublishError(ValueError):
    """Deterministic publish gate violation (fail closed, no authoritative write)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise AnchorProposalError(
            "scope identifiers must be explicit positive integers"
        )


# ---------------------------------------------------------------------------
# Candidate proposal creation (Agent -> proposal + pending ApprovalRequest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorProposalCreateResult:
    """Proposal creation result: candidate row + pending approval (+ replay flag)."""

    proposal: IllustrationAnchorProposal
    approval_request: ApprovalRequest
    replayed: bool = False


def approval_payload_for_proposal(
    proposal: IllustrationAnchorProposalContract,
    *,
    action: str,
    authority_space: str,
    branch: str | None,
    fork: str | None,
) -> dict[str, Any]:
    """Frozen approval payload bound to the proposal (D-15 replay hash source).

    The payload is the canonical snapshot the Web approval decides on; the
    ApprovalRequest ``payload_hash`` and the proposal ``canonical_payload_hash``
    both replay from it, so a forged or drifted decision can never reach the
    publisher. ``authority_space`` / ``branch`` / ``fork`` are the
    server-derived scope bindings (the contract itself carries the source span).
    """
    if action not in ANCHOR_APPROVAL_ACTIONS:
        raise AnchorProposalError(
            f"unknown Phase 34 approval action {action!r}; allowed: "
            f"{sorted(ANCHOR_APPROVAL_ACTIONS)}"
        )
    return {
        "artifact_kind": ILLUSTRATION_ANCHOR_PROPOSAL_ARTIFACT_KIND,
        "schema_version": ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION,
        "owner_id": proposal.owner_id,
        "novel_id": proposal.novel_id,
        "chapter_id": proposal.chapter_id,
        "chapter_number": proposal.chapter_number,
        "proposal_key": proposal.proposal_key,
        "authority_space": authority_space,
        "branch": branch,
        "fork": fork,
        "source_snapshot_id": proposal.source_snapshot_id,
        "source_snapshot_hash": proposal.source_snapshot_hash,
        "paragraph_start": proposal.range.paragraph_start,
        "paragraph_end": proposal.range.paragraph_end,
        "source_start": proposal.range.source_start,
        "source_end": proposal.range.source_end,
        "excerpt": proposal.excerpt,
        "anchor_hash": proposal.anchor_hash,
        "chapter_content_hash": proposal.chapter_content_hash,
        "proposal_asset_revision_id": proposal.proposal_asset.id,
        "caption": proposal.presentation.caption,
        "alt_text": proposal.presentation.alt_text,
        "citation": proposal.presentation.citation,
        "requested_action": action,
    }


def validate_branch_scope(
    *,
    authority_space: str,
    branch: str | None,
    fork: str | None,
) -> None:
    """Fail-closed branch/fork scope gate (D-34-01 derivative mode).

    Derivative mode requires both branch and fork; original mode forbids both.
    A missing branch scope in derivative mode or a forged branch/fork in original
    mode blocks the proposal/publish with no authoritative write.
    """
    if authority_space == "derivative":
        if not branch or not fork:
            raise AnchorGateError(
                "derivative mode requires both branch and fork (missing branch "
                "scope fails closed)"
            )
    else:  # original authority space
        if branch or fork:
            raise AnchorGateError(
                "original mode forbids branch/fork (wrong scope fails closed)"
            )


def _build_proposal_contract(
    *,
    owner_id: int,
    novel_id: int,
    asset: AssetRevision,
    request: dict[str, Any],
    authority_space: str,
    branch: str | None,
    fork: str | None,
) -> IllustrationAnchorProposalContract:
    """Construct the frozen proposal contract from an owner/novel-scoped request.

    The nested ``FrozenAssetRevisionView`` (``build_proposal_ref``) fails closed
    unless the asset is actually proposal_ready with cleared rights (Phase 33
    handoff, D-34-01). ``authority_space`` / ``branch`` / ``fork`` are the
    server-derived scope bindings (computed by the caller) — the Agent can never
    widen scope from the request body. The idempotency key is computed by the
    caller from the contract and replays the span/asset (D-34-01).
    """
    range_ = AnchorRange(
        source_start=int(request["source_start"]),
        source_end=int(request["source_end"]),
        paragraph_start=request.get("paragraph_start"),
        paragraph_end=request.get("paragraph_end"),
    )
    return IllustrationAnchorProposalContract(
        owner_id=owner_id,
        novel_id=novel_id,
        chapter_id=int(request["chapter_id"]),
        chapter_number=int(request["chapter_number"]),
        proposal_key=str(request["proposal_key"]),
        source_snapshot_id=str(request["source_snapshot_id"]),
        source_snapshot_hash=str(request["source_snapshot_hash"]),
        range=range_,
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
        raise AnchorProposalError("chapter not found in the owner/novel scope")
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
        raise AnchorProposalError("proposal asset not found in the owner/novel scope")
    return asset


async def create_anchor_proposal(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    request: dict[str, Any],
    action: str,
) -> AnchorProposalCreateResult:
    """Server-authoritative candidate proposal creation (D-34-01 / D-11 / D-15).

    - Verifies owner/novel scope, the exact source span against the *current*
      chapter content and the proposal-ready asset (rights cleared).
    - Replays an existing proposal with the same idempotency key when the action
      matches (one proposal per span/asset; re-append only replays).
    - Creates the pending Web ApprovalRequest bound to the frozen approval
      payload hash and moves the proposal to ``pending_approval``.
    Nothing here publishes; the deterministic publisher owns approved publication.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if action not in ANCHOR_APPROVAL_ACTIONS:
        raise AnchorProposalError(
            f"unknown Phase 34 approval action {action!r}; allowed: "
            f"{sorted(ANCHOR_APPROVAL_ACTIONS)}"
        )
    if int(request.get("chapter_number", 0)) <= 0:
        raise AnchorProposalError("chapter_number must be a positive integer")

    chapter = await _load_chapter(
        db, owner_id=owner_id, novel_id=novel_id, chapter_id=int(request["chapter_id"])
    )
    if chapter.chapter_number != int(request["chapter_number"]):
        raise AnchorProposalError(
            "chapter_number does not match the chapter lineage (wrong scope)"
        )
    asset = await _load_asset(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        asset_id=int(request["asset_revision_id"]),
    )
    # Server-derived scope: derivative mode requires branch + fork, original
    # forbids both (the Agent can never widen scope from the request body).
    authority_space = (
        "derivative" if (request.get("branch") or request.get("fork")) else "original"
    )
    branch = request.get("branch")
    fork = request.get("fork") if authority_space == "derivative" else None
    try:
        validate_branch_scope(authority_space=authority_space, branch=branch, fork=fork)
        proposal = _build_proposal_contract(
            owner_id=owner_id,
            novel_id=novel_id,
            asset=asset,
            request=request,
            authority_space=authority_space,
            branch=branch,
            fork=fork,
        )
    except (AnchorGateError, pydantic.ValidationError) as exc:
        # build_proposal_ref fails closed on an unapproved/unresolved asset and
        # the branch/fork scope gate fails closed on an incomplete derivative
        # scope; surface a stable proposal-gate reason instead of a raw error.
        raise AnchorProposalError(f"anchor proposal gate blocked: {exc}") from exc
    idempotency_key = build_anchor_proposal_idempotency_key(proposal)
    proposal = proposal.model_copy(update={"idempotency_key": idempotency_key})

    # Exact source hash/range/version gate (proposal-ready asset + cleared rights).
    validation = await AnchorValidationService(db).validate_exact(
        owner_id=owner_id,
        novel_id=novel_id,
        proposal=proposal,
        chapter_content=chapter.content,
    )
    if not validation.ok:
        raise AnchorProposalError(
            f"anchor proposal gate blocked ({validation.reason_code}): "
            f"{validation.detail or 'invalid exact source span'}"
        )

    payload = approval_payload_for_proposal(
        proposal,
        action=action,
        authority_space=authority_space,
        branch=branch,
        fork=fork,
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
            raise AnchorProposalError(
                "a proposal with this span/asset already exists under a different "
                "Phase 34 action; replay the existing proposal instead"
            )
        return AnchorProposalCreateResult(
            proposal=existing, approval_request=approval, replayed=True
        )

    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=int(request["run_id"]) if request.get("run_id") else None,
        skill_version_id=(
            int(request["skill_version_id"])
            if request.get("skill_version_id")
            else None
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
            "proposal_key": proposal.proposal_key,
            "chapter_number": proposal.chapter_number,
            "source_start": proposal.range.source_start,
            "source_end": proposal.range.source_end,
            "anchor_hash": proposal.anchor_hash,
            "proposal_asset_revision_id": proposal.proposal_asset.id,
            "caption": proposal.presentation.caption,
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
        chapter_id=proposal.chapter_id,
        chapter_number=proposal.chapter_number,
        proposal_key=proposal.proposal_key,
        source_snapshot_id=proposal.source_snapshot_id,
        source_snapshot_hash=proposal.source_snapshot_hash,
        paragraph_start=proposal.range.paragraph_start,
        paragraph_end=proposal.range.paragraph_end,
        source_start=proposal.range.source_start,
        source_end=proposal.range.source_end,
        excerpt=proposal.excerpt,
        anchor_hash=proposal.anchor_hash,
        chapter_content_hash=proposal.chapter_content_hash,
        proposal_asset_revision_id=proposal.proposal_asset.id,
        approval_request_id=approval.id,
        status="pending_approval",
        caption=proposal.presentation.caption,
        alt_text=proposal.presentation.alt_text,
        citation=proposal.presentation.citation,
        canonical_payload=payload,
        canonical_payload_hash=payload_hash,
        idempotency_key=idempotency_key,
        projection_hash=payload_hash,
        schema_version=ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION,
    )
    db.add(row)
    await db.flush()
    return AnchorProposalCreateResult(proposal=row, approval_request=approval)


# ---------------------------------------------------------------------------
# Deterministic publisher (Approval -> published anchor + frozen manifest)
# ---------------------------------------------------------------------------


def _anchor_idempotency_key(
    *,
    owner_id: int,
    novel_id: int,
    anchor_key: str,
    proposal_id: int,
    published_asset_revision_id: int,
    publish_manifest_hash: str,
    approval_request_id: int,
) -> str:
    return canonical_anchor_hash(
        {
            "artifact_kind": ILLUSTRATION_ANCHOR_ARTIFACT_KIND,
            "schema_version": ILLUSTRATION_ANCHOR_SCHEMA_VERSION,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "anchor_key": anchor_key,
            "proposal_id": proposal_id,
            "published_asset_revision_id": published_asset_revision_id,
            "publish_manifest_hash": publish_manifest_hash,
            "approval_request_id": approval_request_id,
        }
    )


def _verify_approved_action(
    approval: ApprovalRequest, *, proposal: IllustrationAnchorProposal
) -> None:
    """Approval gate: server-authoritative approved action + replay payload hash.

    Fails closed on a forged (non-approved) decision, an expired/cancelled/
    rejected decision, an action outside the Phase 34 allowlist or a payload
    hash that does not replay from the frozen proposal payload (D-11/D-15).
    """
    if approval.action not in ANCHOR_APPROVAL_ACTIONS:
        raise AnchorPublishError(
            f"approval action {approval.action!r} is not a Phase 34 anchor action"
        )
    if approval.status not in {"approved", "approved_for_session"}:
        raise AnchorPublishError(
            f"approval request {approval.id} is {approval.status!r}; only an "
            "approved action may reach the deterministic publisher (forged/"
            "expired/cancelled/rejected decisions fail closed)"
        )
    if not approval.payload_hash or not proposal.canonical_payload_hash:
        raise AnchorPublishError("approval payload hash is missing (fail closed)")
    if approval.payload_hash != proposal.canonical_payload_hash:
        raise AnchorPublishError(
            "approval payload hash does not replay from the proposal payload "
            "(schema drift / forged approval fails closed)"
        )
    replay = canonical_anchor_hash(dict(proposal.canonical_payload or {}))
    if replay != proposal.canonical_payload_hash:
        raise AnchorPublishError(
            "proposal canonical payload does not replay its stored hash "
            "(schema drift fails closed)"
        )


def _payload_field(payload: dict[str, Any], key: str) -> Any:
    return payload.get(key)


async def publish_anchor(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    proposal_id: int,
) -> IllustrationAnchor:
    """Deterministic publisher: the only path that creates a valid anchor.

    Atomically verifies, in one transaction:
      1. the proposal exists in the owner/novel scope and is ``pending_approval``;
      2. the bound Web approval is server-authoritatively approved with a
         replayable payload hash and a Phase 34 action (forged/expired/cancelled/
         rejected/pending decisions fail closed);
      3. the frozen proposal payload replays its canonical hash and the
         authority-space/branch/fork scope is legal;
      4. the proposal-ready asset is still proposal_ready with cleared rights;
      5. the exact source hash/range/version replays against the *current*
         chapter content (a drifted chapter is stale, never relocated, D-34-01).
    Then creates the published ``valid`` anchor row + frozen publish manifest
    (D-34-04) and moves the proposal to ``valid``. A replay of an already
    published proposal returns the existing anchor.
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
        raise AnchorPublishError("anchor proposal not found in the owner/novel scope")

    if (
        proposal.status == AnchorStatus.VALID.value
        and proposal.published_asset_revision_id
    ):
        existing = await db.scalar(
            select(IllustrationAnchor).where(
                IllustrationAnchor.proposal_id == proposal.id,
                IllustrationAnchor.owner_id == owner_id,
                IllustrationAnchor.novel_id == novel_id,
            )
        )
        if existing is not None:
            return existing
        raise AnchorPublishError(
            "proposal is already valid but no published anchor exists (inconsistent state)"
        )
    if proposal.status != AnchorStatus.PENDING_APPROVAL.value:
        raise AnchorPublishError(
            f"proposal {proposal.id} is {proposal.status!r}; only pending_approval "
            "proposals can be published (fail closed)"
        )
    if proposal.approval_request_id is None:
        raise AnchorPublishError(
            "proposal has no bound Web approval request (approval forgery fails closed)"
        )
    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == proposal.approval_request_id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
    if approval is None:
        raise AnchorPublishError("bound approval request not found in owner scope")
    _verify_approved_action(approval, proposal=proposal)

    payload = dict(proposal.canonical_payload or {})
    authority_space = str(_payload_field(payload, "authority_space") or "original")
    branch = _payload_field(payload, "branch")
    fork = _payload_field(payload, "fork")
    try:
        validate_branch_scope(authority_space=authority_space, branch=branch, fork=fork)
    except AnchorGateError as exc:
        raise AnchorPublishError(f"branch scope gate failed: {exc}") from exc

    chapter = await _load_chapter(
        db, owner_id=owner_id, novel_id=novel_id, chapter_id=proposal.chapter_id
    )
    if chapter.chapter_number != proposal.chapter_number:
        raise AnchorPublishError(
            "chapter_number drifted from the proposal lineage (stale revision fails closed)"
        )
    asset = await _load_asset(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        asset_id=proposal.proposal_asset_revision_id,
    )
    if asset.approval_state != IllustrationApprovalState.PROPOSAL_READY.value:
        raise AnchorPublishError(
            "proposal asset is no longer proposal_ready (unapproved asset fails closed)"
        )
    if asset.rights_status != "cleared":
        raise AnchorPublishError(
            "proposal asset rights must be cleared before publication"
        )

    contract = IllustrationAnchorProposalContract.model_validate(
        {
            "owner_id": proposal.owner_id,
            "novel_id": proposal.novel_id,
            "chapter_id": proposal.chapter_id,
            "chapter_number": proposal.chapter_number,
            "proposal_key": proposal.proposal_key,
            "source_snapshot_id": proposal.source_snapshot_id,
            "source_snapshot_hash": proposal.source_snapshot_hash,
            "range": {
                "source_start": proposal.source_start,
                "source_end": proposal.source_end,
                "paragraph_start": proposal.paragraph_start,
                "paragraph_end": proposal.paragraph_end,
            },
            "excerpt": proposal.excerpt,
            "anchor_hash": proposal.anchor_hash,
            "chapter_content_hash": proposal.chapter_content_hash,
            "proposal_asset": build_proposal_ref(asset),
            "presentation": {
                "caption": proposal.caption,
                "alt_text": proposal.alt_text,
                "citation": proposal.citation,
            },
            "idempotency_key": proposal.idempotency_key,
        }
    )
    validation = await AnchorValidationService(db).validate_exact(
        owner_id=owner_id,
        novel_id=novel_id,
        proposal=contract,
        chapter_content=chapter.content,
    )
    if not validation.ok:
        raise AnchorPublishError(
            f"publish gate blocked ({validation.reason_code}): "
            f"{validation.detail or 'exact source span is stale'}"
        )

    now = _utcnow()
    manifest = AnchorPublishManifest(
        owner_id=owner_id,
        novel_id=novel_id,
        chapter_id=proposal.chapter_id,
        chapter_number=proposal.chapter_number,
        anchor_key=proposal.proposal_key,
        text_version_hash=proposal.chapter_content_hash,
        source_snapshot_id=proposal.source_snapshot_id,
        source_snapshot_hash=proposal.source_snapshot_hash,
        range=AnchorRange(
            source_start=proposal.source_start,
            source_end=proposal.source_end,
            paragraph_start=proposal.paragraph_start,
            paragraph_end=proposal.paragraph_end,
        ),
        excerpt=proposal.excerpt,
        anchor_hash=proposal.anchor_hash,
        presentation=AnchorCopy(
            caption=proposal.caption,
            alt_text=proposal.alt_text,
            citation=proposal.citation,
        ),
        asset=PublishedAssetRef(
            asset_revision_id=asset.id,
            asset_id=asset.asset_id,
            bytes_hash=asset.bytes_hash,
            mime_type=asset.mime_type,
        ),
        published_at=now,
    )
    manifest_hash = anchor_publish_manifest_hash(manifest)

    approved_by = str(approval.decision_actor_id or owner_id)
    anchor = IllustrationAnchor(
        owner_id=owner_id,
        novel_id=novel_id,
        chapter_id=proposal.chapter_id,
        chapter_number=proposal.chapter_number,
        anchor_key=proposal.proposal_key,
        proposal_id=proposal.id,
        source_snapshot_id=proposal.source_snapshot_id,
        source_snapshot_hash=proposal.source_snapshot_hash,
        paragraph_start=proposal.paragraph_start,
        paragraph_end=proposal.paragraph_end,
        source_start=proposal.source_start,
        source_end=proposal.source_end,
        excerpt=proposal.excerpt,
        anchor_hash=proposal.anchor_hash,
        chapter_content_hash=proposal.chapter_content_hash,
        published_asset_revision_id=asset.id,
        publish_manifest_hash=manifest_hash,
        approval_request_id=approval.id,
        status=AnchorStatus.VALID.value,
        caption=proposal.caption,
        alt_text=proposal.alt_text,
        citation=proposal.citation,
        approved_by=approved_by,
        approved_at=now,
        canonical_payload=dict(payload),
        canonical_payload_hash=proposal.canonical_payload_hash,
        idempotency_key=_anchor_idempotency_key(
            owner_id=owner_id,
            novel_id=novel_id,
            anchor_key=proposal.proposal_key,
            proposal_id=proposal.id,
            published_asset_revision_id=asset.id,
            publish_manifest_hash=manifest_hash,
            approval_request_id=approval.id,
        ),
        projection_hash=manifest_hash,
        schema_version=ILLUSTRATION_ANCHOR_SCHEMA_VERSION,
    )
    db.add(anchor)

    # Move the proposal projection: pending_approval -> valid with the published
    # asset + frozen manifest (the only mutable projection, D-34-01).
    proposal.published_asset_revision_id = asset.id
    proposal.publish_manifest_hash = manifest_hash
    proposal.status = AnchorStatus.VALID.value
    proposal.approved_by = approved_by
    proposal.approved_at = now
    await db.flush()
    return anchor


async def build_anchor_manifest(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    anchor_id: int,
) -> AnchorPublishManifest:
    """Reconstruct the frozen publish manifest for a valid anchor (D-34-04).

    The manifest is deterministic from the published anchor row + the published
    AssetRevision; the stored ``publish_manifest_hash`` must replay, otherwise
    the anchor is inconsistent and the read fails closed (nothing invents a URL
    or drops provenance).
    """
    anchor = await db.scalar(
        select(IllustrationAnchor).where(
            IllustrationAnchor.id == anchor_id,
            IllustrationAnchor.owner_id == owner_id,
            IllustrationAnchor.novel_id == novel_id,
        )
    )
    if anchor is None:
        raise AnchorPublishError("published anchor not found in the owner/novel scope")
    if anchor.status != AnchorStatus.VALID.value:
        raise AnchorPublishError(
            f"published anchor is {anchor.status!r}; manifest is frozen only for "
            "valid anchors"
        )
    asset = await _load_asset(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        asset_id=anchor.published_asset_revision_id,
    )
    manifest = AnchorPublishManifest(
        owner_id=anchor.owner_id,
        novel_id=anchor.novel_id,
        chapter_id=anchor.chapter_id,
        chapter_number=anchor.chapter_number,
        anchor_key=anchor.anchor_key,
        text_version_hash=anchor.chapter_content_hash,
        source_snapshot_id=anchor.source_snapshot_id,
        source_snapshot_hash=anchor.source_snapshot_hash,
        range=AnchorRange(
            source_start=anchor.source_start,
            source_end=anchor.source_end,
            paragraph_start=anchor.paragraph_start,
            paragraph_end=anchor.paragraph_end,
        ),
        excerpt=anchor.excerpt,
        anchor_hash=anchor.anchor_hash,
        presentation=AnchorCopy(
            caption=anchor.caption,
            alt_text=anchor.alt_text,
            citation=anchor.citation,
        ),
        asset=PublishedAssetRef(
            asset_revision_id=asset.id,
            asset_id=asset.asset_id,
            bytes_hash=asset.bytes_hash,
            mime_type=asset.mime_type,
        ),
        published_at=anchor.approved_at or _utcnow(),
    )
    if anchor_publish_manifest_hash(manifest) != anchor.publish_manifest_hash:
        raise AnchorPublishError(
            "publish manifest does not replay its frozen hash (inconsistent anchor)"
        )
    return manifest


__all__ = [
    "ANCHOR_APPROVAL_ACTIONS",
    "AnchorProposalCreateResult",
    "AnchorProposalError",
    "AnchorPublishError",
    "approval_payload_for_proposal",
    "build_anchor_manifest",
    "create_anchor_proposal",
    "publish_anchor",
    "validate_branch_scope",
]
