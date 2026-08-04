"""Illustration anchor strict contracts (Phase 34-01, REQ-VIS-05).

D-34-01 / D-34-03: an approved illustration stays consistent between the
reader and every export through a hash-verified anchor bound to
owner/novel/chapter, an immutable source snapshot, exact source coordinates
and the proposal-ready AssetRevision. This module owns:

- the strict typed wire contracts with ``extra="forbid"`` and frozen immutable
  payloads (``AnchorRange`` / ``AnchorCopy`` / ``AnchorPublishManifest`` /
  ``IllustrationAnchorProposalContract`` / ``IllustrationAnchorContract``);
- the closed anchor status vocabulary (``proposed`` / ``pending_approval`` /
  ``valid`` / ``needs_repair`` / ``invalid``) and the fail-closed lifecycle:
  a proposal is created ``proposed`` from a proposal-ready AssetRevision and an
  exact source hash/range; only the 34-05 deterministic publish transaction may
  fill the published asset + publish manifest and enter ``valid``;
- canonical hash helpers so the proposal idempotency key and the frozen publish
  manifest are byte-replayable;
- server-side gates that fail closed on a non-replayable idempotency key, an
  exact source hash/range/version mismatch, an unapproved or unresolved asset
  and a published anchor that does not bind an approved action, the published
  asset and the publish manifest.

Nothing here writes to the database and nothing publishes an anchor; a hash or
offset mismatch is stale (``needs_repair`` / ``invalid``) and never silently
relocates to a nearby paragraph (D-34-01). Reader/export consume only the
published anchor via one frozen manifest (D-34-04).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.illustration import FrozenAssetRevisionView

ILLUSTRATION_ANCHOR_SCHEMA_VERSION = "illustration-anchor.v1"
ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION = "illustration-anchor-proposal.v1"
ILLUSTRATION_ANCHOR_MANIFEST_SCHEMA_VERSION = "illustration-anchor-manifest.v1"
ILLUSTRATION_ANCHOR_ARTIFACT_KIND = "illustration_anchor"
ILLUSTRATION_ANCHOR_PROPOSAL_ARTIFACT_KIND = "illustration_anchor_proposal"
ILLUSTRATION_ANCHOR_MANIFEST_ARTIFACT_KIND = "illustration_anchor_manifest"

# Mirrors the ORM vocabulary so schema/model/migration stay byte-identical.
ILLUSTRATION_ANCHOR_STATUSES = (
    "proposed",
    "pending_approval",
    "valid",
    "needs_repair",
    "invalid",
)
ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES = ("valid", "needs_repair", "invalid")


class StrictAnchorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnchorStatus(StrEnum):
    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    VALID = "valid"
    NEEDS_REPAIR = "needs_repair"
    INVALID = "invalid"


class AnchorGateError(ValueError):
    """Fail-closed gate violation while validating an anchor contract."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable lineage)
# ---------------------------------------------------------------------------


def canonical_anchor_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def source_span_hash(excerpt: str) -> str:
    """D-34-01: the anchor hash is the SHA-256 over the exact source excerpt."""
    return sha256(excerpt.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Frozen value objects (exact source span + accessible copy)
# ---------------------------------------------------------------------------


class AnchorRange(StrictAnchorModel):
    """Exact source span (code-point offsets) plus optional paragraph range.

    ``source_start`` / ``source_end`` are the immutable span the anchor hash is
    verified against; ``paragraph_start`` / ``paragraph_end`` are optional
    paragraph-range coordinates for the reader/export layout. Offset/hash
    mismatch makes an anchor stale — it must never move to a nearby paragraph.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    paragraph_start: int | None = Field(default=None, ge=1)
    paragraph_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exact_span(self) -> "AnchorRange":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        if (self.paragraph_start is None) != (self.paragraph_end is None):
            raise ValueError(
                "paragraph_start and paragraph_end must be set together or not at all"
            )
        if (
            self.paragraph_start is not None
            and self.paragraph_end is not None
            and self.paragraph_end < self.paragraph_start
        ):
            raise ValueError("paragraph_end must be >= paragraph_start")
        return self


class AnchorCopy(StrictAnchorModel):
    """Accessible caption/alt/citation contract (D-34-02, never empty)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    caption: str = Field(min_length=1, max_length=500)
    alt_text: str = Field(min_length=1, max_length=500)
    citation: str = Field(min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# Proposal contract (D-34-01): proposal-ready asset + exact source span
# ---------------------------------------------------------------------------


class IllustrationAnchorProposalContract(StrictAnchorModel):
    """Frozen proposal; only a proposal-ready asset and an exact source
    hash/range are accepted (never auto-relocated)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["illustration-anchor-proposal.v1"] = (
        "illustration-anchor-proposal.v1"
    )
    artifact_kind: Literal["illustration_anchor_proposal"] = (
        "illustration_anchor_proposal"
    )
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    proposal_key: str = Field(min_length=1, max_length=160)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    range: AnchorRange
    excerpt: str = Field(min_length=1, max_length=20000)
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Phase 33 handoff: the frozen proposal-ready asset (the nested validator
    # fails closed unless the asset is proposal_ready with cleared rights).
    proposal_asset: FrozenAssetRevisionView
    presentation: AnchorCopy
    status: AnchorStatus = AnchorStatus.PROPOSED
    approval_request_id: int | None = Field(default=None, gt=0)
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def proposal_only_and_scope(self) -> "IllustrationAnchorProposalContract":
        if self.status is not AnchorStatus.PROPOSED:
            raise AnchorGateError(
                "an anchor proposal must be created as proposed; publish/repair "
                "status is owned by the deterministic workflow"
            )
        if (
            self.proposal_asset.owner_id != self.owner_id
            or self.proposal_asset.novel_id != self.novel_id
        ):
            raise AnchorGateError(
                "proposal asset owner/novel scope does not match the proposal"
            )
        if self.proposal_asset.id <= 0:
            raise AnchorGateError("proposal asset revision id must be a positive id")
        return self


def build_anchor_proposal_idempotency_key(
    proposal: IllustrationAnchorProposalContract,
) -> str:
    """D-34-01 idempotency key: one proposal per owner/novel/key/span/asset."""
    return canonical_anchor_hash(
        {
            "artifact_kind": proposal.artifact_kind,
            "schema_version": proposal.schema_version,
            "owner_id": proposal.owner_id,
            "novel_id": proposal.novel_id,
            "chapter_id": proposal.chapter_id,
            "chapter_number": proposal.chapter_number,
            "proposal_key": proposal.proposal_key,
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
        }
    )


# ---------------------------------------------------------------------------
# Publish manifest (D-34-04): one frozen manifest for reader and export
# ---------------------------------------------------------------------------


class PublishedAssetRef(StrictAnchorModel):
    """Published AssetRevision ref frozen into the manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_revision_id: int = Field(gt=0)
    asset_id: str = Field(min_length=1, max_length=200)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)


class AnchorPublishManifest(StrictAnchorModel):
    """Frozen manifest consumed by Markdown/HTML/EPUB adapters (D-34-04).

    Freezes the text version hash, source snapshot, anchor span/hash, the
    accessible copy and the published asset ref so every export reads exactly
    the same approved content and never invents a URL or drops provenance.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["illustration-anchor-manifest.v1"] = (
        "illustration-anchor-manifest.v1"
    )
    artifact_kind: Literal["illustration_anchor_manifest"] = (
        "illustration_anchor_manifest"
    )
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    anchor_key: str = Field(min_length=1, max_length=160)
    text_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    range: AnchorRange
    excerpt: str = Field(min_length=1, max_length=20000)
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    presentation: AnchorCopy
    asset: PublishedAssetRef
    published_at: datetime


def anchor_publish_manifest_hash(manifest: AnchorPublishManifest) -> str:
    """D-34-04: the publish manifest hash freezes text/assets/citations once."""
    return canonical_anchor_hash(
        {
            "artifact_kind": manifest.artifact_kind,
            "schema_version": manifest.schema_version,
            "owner_id": manifest.owner_id,
            "novel_id": manifest.novel_id,
            "chapter_id": manifest.chapter_id,
            "chapter_number": manifest.chapter_number,
            "anchor_key": manifest.anchor_key,
            "text_version_hash": manifest.text_version_hash,
            "source_snapshot_id": manifest.source_snapshot_id,
            "source_snapshot_hash": manifest.source_snapshot_hash,
            "paragraph_start": manifest.range.paragraph_start,
            "paragraph_end": manifest.range.paragraph_end,
            "source_start": manifest.range.source_start,
            "source_end": manifest.range.source_end,
            "excerpt": manifest.excerpt,
            "anchor_hash": manifest.anchor_hash,
            "caption": manifest.presentation.caption,
            "alt_text": manifest.presentation.alt_text,
            "citation": manifest.presentation.citation,
            "asset": {
                "asset_revision_id": manifest.asset.asset_revision_id,
                "asset_id": manifest.asset.asset_id,
                "bytes_hash": manifest.asset.bytes_hash,
                "mime_type": manifest.asset.mime_type,
            },
            "published_at": manifest.published_at.isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# Published anchor contract (D-34-01): approved action + published asset +
# publish manifest
# ---------------------------------------------------------------------------


class IllustrationAnchorContract(StrictAnchorModel):
    """Frozen published anchor created only by the deterministic publish
    transaction; must bind an approved action, the published asset and the
    publish manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["illustration-anchor.v1"] = "illustration-anchor.v1"
    artifact_kind: Literal["illustration_anchor"] = "illustration_anchor"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    anchor_key: str = Field(min_length=1, max_length=160)
    proposal_id: int = Field(gt=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    range: AnchorRange
    excerpt: str = Field(min_length=1, max_length=20000)
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_asset_revision_id: int = Field(gt=0)
    approval_request_id: int = Field(gt=0)
    publish_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    presentation: AnchorCopy
    status: AnchorStatus = AnchorStatus.VALID
    approved_by: str = Field(min_length=1, max_length=200)
    approved_at: datetime
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def published_only(self) -> "IllustrationAnchorContract":
        if self.status is not AnchorStatus.VALID:
            raise AnchorGateError(
                "a published anchor is created valid only; needs_repair/invalid "
                "are later status projections (D-34-03)"
            )
        if self.anchor_hash != source_span_hash(self.excerpt):
            raise AnchorGateError(
                "anchor_hash does not replay from the excerpt (D-34-01)"
            )
        return self


# ---------------------------------------------------------------------------
# Server-side gates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorValidationResult:
    """Fail-closed exact-anchor validation with a stable machine reason code."""

    ok: bool
    status: AnchorStatus
    reason_code: str | None = None
    detail: str | None = None


def validate_exact_source(
    *,
    source_range: AnchorRange,
    excerpt: str,
    anchor_hash: str,
    chapter_content_hash: str,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    chapter_content: str | None = None,
) -> AnchorValidationResult:
    """Verify the exact source hash/range/version of an anchor span.

    Fail closed on a malformed hash, an anchor hash that does not replay from
    the excerpt, a frozen chapter content hash that does not replay from the
    current content, an out-of-bounds or mismatched source span. A mismatch is
    ``invalid`` — the validator never searches for the excerpt and never
    auto-relocates to a nearby paragraph (D-34-01).
    """
    if not _is_hex64(anchor_hash) or not _is_hex64(chapter_content_hash):
        return AnchorValidationResult(
            ok=False,
            status=AnchorStatus.INVALID,
            reason_code="malformed_anchor_hash",
            detail="anchor_hash and chapter_content_hash must be 64-hex hashes",
        )
    if not _is_hex64(source_snapshot_hash) or not source_snapshot_id.strip():
        return AnchorValidationResult(
            ok=False,
            status=AnchorStatus.INVALID,
            reason_code="source_snapshot_incomplete",
            detail="source_snapshot_hash must be a 64-hex hash with a non-empty id",
        )
    if anchor_hash != source_span_hash(excerpt):
        return AnchorValidationResult(
            ok=False,
            status=AnchorStatus.INVALID,
            reason_code="anchor_hash_mismatch",
            detail="anchor_hash does not replay from the frozen source excerpt",
        )
    if chapter_content is not None:
        if sha256(chapter_content.encode("utf-8")).hexdigest() != chapter_content_hash:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="chapter_content_hash_mismatch",
                detail="chapter content hash does not replay from the current text",
            )
        if (
            source_range.source_start < 0
            or source_range.source_end > len(chapter_content)
        ):
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="source_range_out_of_bounds",
                detail="source span is outside the current chapter content",
            )
        actual = chapter_content[
            source_range.source_start : source_range.source_end
        ]
        if actual != excerpt:
            return AnchorValidationResult(
                ok=False,
                status=AnchorStatus.INVALID,
                reason_code="source_range_mismatch",
                detail=(
                    "source span does not replay the excerpt; the anchor is stale "
                    "and must not relocate to a nearby paragraph"
                ),
            )
    return AnchorValidationResult(ok=True, status=AnchorStatus.PROPOSED)


def validate_anchor_proposal_contract(
    proposal: IllustrationAnchorProposalContract,
    *,
    chapter_content: str | None = None,
) -> AnchorValidationResult:
    """Proposal gate: proposal-ready asset + exact source hash/range/version.

    Only produces ``proposed`` (the service-side creation state) or ``invalid``;
    publish/repair status is owned by the deterministic workflow.
    """
    expected = build_anchor_proposal_idempotency_key(proposal)
    if proposal.idempotency_key != expected:
        return AnchorValidationResult(
            ok=False,
            status=AnchorStatus.INVALID,
            reason_code="proposal_idempotency_mismatch",
            detail="proposal idempotency_key does not replay from its span/asset",
        )
    if proposal.anchor_hash != source_span_hash(proposal.excerpt):
        return AnchorValidationResult(
            ok=False,
            status=AnchorStatus.INVALID,
            reason_code="anchor_hash_mismatch",
            detail="proposal anchor_hash does not replay from the excerpt",
        )
    return validate_exact_source(
        source_range=proposal.range,
        excerpt=proposal.excerpt,
        anchor_hash=proposal.anchor_hash,
        chapter_content_hash=proposal.chapter_content_hash,
        source_snapshot_id=proposal.source_snapshot_id,
        source_snapshot_hash=proposal.source_snapshot_hash,
        chapter_content=chapter_content,
    )


def validate_published_anchor(
    anchor: IllustrationAnchorContract,
    manifest: AnchorPublishManifest,
) -> None:
    """Published-anchor gate: bind the approved action, the published asset and
    the frozen publish manifest (D-34-01/04).

    Fails closed on a non-valid status, a manifest hash that does not replay
    from the frozen manifest, a published asset that is not the manifest asset,
    and any drift between the anchor span/hash/version and the manifest.
    """
    if anchor.status is not AnchorStatus.VALID:
        raise AnchorGateError(
            "a published anchor must be valid; needs_repair/invalid are later "
            "status projections"
        )
    if anchor.publish_manifest_hash != anchor_publish_manifest_hash(manifest):
        raise AnchorGateError(
            "publish_manifest_hash does not replay from the frozen manifest"
        )
    if anchor.published_asset_revision_id != manifest.asset.asset_revision_id:
        raise AnchorGateError(
            "published asset revision does not match the manifest asset"
        )
    if anchor.chapter_content_hash != manifest.text_version_hash:
        raise AnchorGateError(
            "anchor text version hash does not match the manifest text version"
        )
    if anchor.anchor_hash != manifest.anchor_hash:
        raise AnchorGateError("anchor hash does not match the manifest hash")
    if anchor.source_snapshot_id != manifest.source_snapshot_id or (
        anchor.source_snapshot_hash != manifest.source_snapshot_hash
    ):
        raise AnchorGateError(
            "anchor source snapshot does not match the manifest source snapshot"
        )
    if (
        anchor.range.source_start != manifest.range.source_start
        or anchor.range.source_end != manifest.range.source_end
        or anchor.range.paragraph_start != manifest.range.paragraph_start
        or anchor.range.paragraph_end != manifest.range.paragraph_end
    ):
        raise AnchorGateError("anchor source range does not match the manifest range")
    if (
        anchor.presentation.caption != manifest.presentation.caption
        or anchor.presentation.alt_text != manifest.presentation.alt_text
        or anchor.presentation.citation != manifest.presentation.citation
    ):
        raise AnchorGateError(
            "anchor caption/alt/citation does not match the manifest copy"
        )
    if anchor.approval_request_id is None or anchor.approval_request_id <= 0:
        raise AnchorGateError(
            "a valid published anchor must bind an approved action"
        )


# ---------------------------------------------------------------------------
# Read envelopes (owner-scoped; published anchors are the only canon surface)
# ---------------------------------------------------------------------------


class AnchorProposalView(StrictAnchorModel):
    """Read envelope for a candidate anchor proposal (never reader/export)."""

    id: int
    owner_id: int
    novel_id: int
    chapter_id: int
    chapter_number: int
    proposal_key: str
    source_snapshot_id: str
    source_snapshot_hash: str
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    source_start: int
    source_end: int
    excerpt: str
    anchor_hash: str
    chapter_content_hash: str
    proposal_asset_revision_id: int
    approval_request_id: int | None = None
    published_asset_revision_id: int | None = None
    publish_manifest_hash: str | None = None
    status: AnchorStatus
    caption: str
    alt_text: str
    citation: str


class AnchorView(StrictAnchorModel):
    """Read envelope for a published anchor consumed by reader/export."""

    id: int
    owner_id: int
    novel_id: int
    chapter_id: int
    chapter_number: int
    anchor_key: str
    proposal_id: int
    source_snapshot_id: str
    source_snapshot_hash: str
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    source_start: int
    source_end: int
    excerpt: str
    anchor_hash: str
    chapter_content_hash: str
    published_asset_revision_id: int
    publish_manifest_hash: str
    approval_request_id: int
    status: AnchorStatus
    caption: str
    alt_text: str
    citation: str
    approved_by: str | None = None
    approved_at: datetime | None = None


__all__ = [
    "AnchorCopy",
    "AnchorGateError",
    "AnchorPublishManifest",
    "AnchorProposalView",
    "AnchorRange",
    "AnchorStatus",
    "AnchorValidationResult",
    "AnchorView",
    "ILLUSTRATION_ANCHOR_ARTIFACT_KIND",
    "ILLUSTRATION_ANCHOR_MANIFEST_ARTIFACT_KIND",
    "ILLUSTRATION_ANCHOR_MANIFEST_SCHEMA_VERSION",
    "ILLUSTRATION_ANCHOR_PROPOSAL_ARTIFACT_KIND",
    "ILLUSTRATION_ANCHOR_PROPOSAL_SCHEMA_VERSION",
    "ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES",
    "ILLUSTRATION_ANCHOR_SCHEMA_VERSION",
    "ILLUSTRATION_ANCHOR_STATUSES",
    "IllustrationAnchorContract",
    "IllustrationAnchorProposalContract",
    "PublishedAssetRef",
    "anchor_publish_manifest_hash",
    "build_anchor_proposal_idempotency_key",
    "canonical_anchor_hash",
    "source_span_hash",
    "validate_anchor_proposal_contract",
    "validate_exact_source",
    "validate_published_anchor",
]
