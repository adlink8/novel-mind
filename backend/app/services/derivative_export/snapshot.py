"""Derivative export snapshot freeze service (Phase 39-01, D-39-01/D-39-02).

D-39-01 / REQ-FORK-05 / REQ-CRE-07: the export is only ever materialized from
**one immutable ``ExportSnapshot``** that freezes content, chapters, published
revisions, assets, citations, manifest, project and revision/version in one
aligned version; the same DB state always freezes to the same snapshot hash and
the same bytes. D-39-02: the snapshot consumes **only** Fanfiction Canon
derivative rows — published derivative revisions (37-04) and published
derivative assets (38-03/04); it never reads Original future content and never
writes any space.

This module owns:

- ``ExportSnapshot`` — the strict, frozen aggregate the two deterministic
  serializers (Markdown/EPUB3) consume; ``snapshot_hash`` is the single
  byte-replayable hash shared with the frozen manifest.
- pure parity validators — owner/project/fork/version/snapshot/approval/review
  parity, asset_hashes membership, citation/source refs lineage; any mismatch
  fails closed with an explicit blocked ``ExportSnapshotError``.
- ``ExportSnapshotService.build`` — the owner-scoped DB freeze that loads the
  project/chapters/revisions/assets once and seals the snapshot (no
  independent live reads in the serializers).

Approved-only: unapproved candidates and rejected assets never appear; a
missing binary is an explicit ``MissingDerivativeAssetRecord`` and a missing /
stale / out-of-scope row is a blocked error — never a silent provenance drop.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_override import DerivativeOverride
from app.models.derivative_project import DerivativeProject
from app.models.derivative_revision import DerivativeRevision
from app.schemas.derivative_visual_asset import (
    DERIVATIVE_ASSET_NAMESPACE,
    PublishedDerivativeVisualAsset,
)
from app.services.derivative_editor.chapters import canonicalize_markdown
from app.services.derivative_generation.published_revision import (
    DERIVATIVE_REVISION_PUBLICATION_STATUS,
    PublishedDerivativeRevision,
    build_published_derivative_revision,
    canonical_citation_hash,
)
from app.services.derivative_visual.assets import (
    ALLOWED_DERIVATIVE_MIME_TYPES,
    DerivativeAssetStorage,
    DerivativeAssetStorageError,
)
from app.services.derivative_visual.published_assets import (
    PublishedAssetScopeError,
    list_published_assets,
)
from app.services.derivative_export.manifest import (
    DERIVATIVE_EXPORT_ARTIFACT_KIND,
    DERIVATIVE_EXPORT_SCHEMA_VERSION,
    DERIVATIVE_EXPORT_SPACE,
    DERIVATIVE_EXPORT_VERSION,
    DerivativeExportAsset,
    DerivativeExportChapter,
    DerivativeExportCitation,
    DerivativeExportRevision,
    DerivativeSourceSnapshotExport,
    DerivativeVisualVersionExport,
    MissingDerivativeAssetRecord,
    canonical_export_hash,
)

HEX64_RE_SET = frozenset("0123456789abcdef")


class ExportSnapshotError(ValueError):
    """Fail-closed derivative export snapshot gate violation."""

    def __init__(self, code: str, detail: str, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


def _require_scope(*, owner_id: int, novel_id: int, project_id: int) -> None:
    values = (owner_id, novel_id, project_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ExportSnapshotError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


# ---------------------------------------------------------------------------
# Frozen export snapshot (the single source the serializers consume)
# ---------------------------------------------------------------------------


class _StrictSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportSnapshot(_StrictSnapshotModel):
    """Immutable derivative export snapshot (D-39-01: one version for all)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DERIVATIVE_EXPORT_SCHEMA_VERSION
    artifact_kind: str = DERIVATIVE_EXPORT_ARTIFACT_KIND
    export_version: str = DERIVATIVE_EXPORT_VERSION
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    project_key: str = Field(min_length=1, max_length=128)
    project_name: str = Field(min_length=1, max_length=120)
    fork_id: int = Field(gt=0)
    fork_key: str = Field(min_length=1, max_length=128)
    space: str = DERIVATIVE_EXPORT_SPACE
    source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapters: tuple[DerivativeExportChapter, ...]
    revisions: tuple[DerivativeExportRevision, ...] = Field(default_factory=tuple)
    assets: tuple[DerivativeExportAsset, ...] = Field(default_factory=tuple)
    citations: tuple[DerivativeExportCitation, ...] = Field(default_factory=tuple)
    missing_assets: tuple[MissingDerivativeAssetRecord, ...] = Field(
        default_factory=tuple
    )
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def export_snapshot_payload(snapshot: ExportSnapshot | dict[str, Any]) -> dict[str, Any]:
    """Canonical payload of a snapshot (the hash input, excluding the hash)."""
    if isinstance(snapshot, ExportSnapshot):
        payload = snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
    else:
        payload = dict(snapshot)
        payload.pop("snapshot_hash", None)
    return payload


def export_snapshot_hash(snapshot: ExportSnapshot) -> str:
    """Replay the snapshot hash from the frozen payload (byte-reproducible)."""
    return canonical_export_hash(export_snapshot_payload(snapshot))


def seal_export_snapshot(snapshot: ExportSnapshot) -> ExportSnapshot:
    """Return a copy of the snapshot with the replayable hash sealed."""
    return snapshot.model_copy(update={"snapshot_hash": export_snapshot_hash(snapshot)})


# ---------------------------------------------------------------------------
# Frozen export package (snapshot + asset-bytes seam, no live DB reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenDerivativeExport:
    """The frozen snapshot plus the asset-bytes reader seam.

    The deterministic serializers consume only this object; they never read the
    database. Missing/hash-drifted bytes resolve to ``None`` (explicit
    placeholder) — never an invented URL or a silent drop (D-39-01/34-04).
    """

    snapshot: ExportSnapshot
    storage: DerivativeAssetStorage | None = None

    def asset_reader(self) -> Callable[[DerivativeExportAsset], bytes | None]:
        def _read(asset: DerivativeExportAsset) -> bytes | None:
            if self.storage is None:
                return None
            try:
                payload = self.storage.read(
                    owner_id=self.snapshot.owner_id,
                    novel_id=self.snapshot.novel_id,
                    visual_version_id=asset.visual_version.version_id,
                    asset_id=asset.asset_id,
                    mime_type=asset.mime_type,
                )
            except DerivativeAssetStorageError:
                return None
            if hashlib.sha256(payload).hexdigest() != asset.content_hash:
                return None
            return payload

        return _read


# ---------------------------------------------------------------------------
# Pure parity validators (fail-closed, DB-free, unit/adversarial testable)
# ---------------------------------------------------------------------------


def validate_revision_citation_hash(revision: PublishedDerivativeRevision) -> list[str]:
    """The revision citation hash must replay its frozen citation keys."""
    evidence = dict(revision.review.get("evidence_snapshot") or {})
    keys = [str(key) for key in (evidence.get("citation_keys") or [])]
    if canonical_citation_hash(keys) != revision.citation_hash:
        return ["revision_citation_hash_mismatch"]
    return []


def validate_published_revision(
    revision: PublishedDerivativeRevision,
    *,
    owner_id: int,
    project_id: int,
    fork_id: int,
    source_snapshot: str,
    project_manifest_hash: str,
    chapter_version_id: int,
) -> list[str]:
    """Owner/project/fork/status/snapshot/manifest/version/citation parity."""
    errors: list[str] = []
    if revision.owner_id != owner_id:
        errors.append("revision_owner_mismatch")
    if revision.project_id != project_id:
        errors.append("revision_project_mismatch")
    if revision.fork_id != fork_id:
        errors.append("revision_fork_mismatch")
    if revision.status != DERIVATIVE_REVISION_PUBLICATION_STATUS:
        errors.append("revision_status_denied")
    if revision.source_snapshot != source_snapshot:
        errors.append("revision_source_snapshot_mismatch")
    if revision.manifest_hash != project_manifest_hash:
        errors.append("revision_manifest_hash_mismatch")
    if revision.version_id != chapter_version_id:
        errors.append("revision_version_stale")
    errors.extend(validate_revision_citation_hash(revision))
    return errors


def validate_published_asset(
    asset: PublishedDerivativeVisualAsset,
    *,
    owner_id: int,
    project_id: int,
    fork_id: int,
    source_snapshot_hash: str,
) -> list[str]:
    """Owner/project/fork/namespace/snapshot/hash/mime/path parity for an asset."""
    errors: list[str] = []
    if asset.owner_id != owner_id:
        errors.append("asset_owner_mismatch")
    if asset.project_id != project_id:
        errors.append("asset_project_mismatch")
    if asset.fork_id != fork_id:
        errors.append("asset_fork_mismatch")
    if asset.namespace != DERIVATIVE_ASSET_NAMESPACE:
        errors.append("asset_namespace_denied")
    if asset.source_snapshot.source_snapshot_hash != source_snapshot_hash:
        errors.append("asset_source_snapshot_mismatch")
    if asset.approval != "approved":
        errors.append("asset_not_approved")
    if not _is_hex64(asset.content_hash):
        errors.append("asset_hash_malformed")
    if asset.mime_type not in ALLOWED_DERIVATIVE_MIME_TYPES:
        errors.append("asset_mime_denied")
    # T-39-01-02 / zip-slip: an asset id must never carry path separators or
    # traversal tokens — export entry names are derived from the content hash.
    if not isinstance(asset.asset_id, str) or not asset.asset_id:
        errors.append("asset_id_missing")
    elif any(token in asset.asset_id for token in ("/", "\\", "..", "\x00")):
        errors.append("asset_path_denied")
    return errors


def validate_asset_membership(
    revision: PublishedDerivativeRevision, available_content_hashes: set[str]
) -> list[str]:
    """Every revision asset hash must be a member of the published asset set."""
    missing = sorted(
        hash_ for hash_ in (revision.asset_hashes or []) if hash_ not in available_content_hashes
    )
    if missing:
        return ["asset_hash_not_member"]
    return []


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in HEX64_RE_SET for ch in value)
    )


# ---------------------------------------------------------------------------
# Snapshot freeze service (owner-scoped, approved-only)
# ---------------------------------------------------------------------------


class ExportSnapshotService:
    """Owner/novel/project-scoped derivative export snapshot freeze (D-39-01)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: DerivativeAssetStorage | None = None,
    ) -> None:
        self._session = session
        self._storage = storage

    async def build(
        self, *, owner_id: int, novel_id: int, project_id: int
    ) -> FrozenDerivativeExport:
        """Freeze chapters/revisions/assets/citations once for the export.

        Reads only owner/novel/project-scoped Fanfiction Canon derivative rows
        (project, chapters, head revisions, approved overrides, published
        assets) and fails closed on any parity/missing/provenance mismatch.
        """
        _require_scope(owner_id=owner_id, novel_id=novel_id, project_id=project_id)
        project = await self._session.scalar(
            select(DerivativeProject).where(
                DerivativeProject.id == project_id,
                DerivativeProject.owner_id == owner_id,
                DerivativeProject.novel_id == novel_id,
            )
        )
        if project is None:
            raise ExportSnapshotError(
                "project_not_found",
                "derivative project not found in the owner/novel scope",
                status_code=404,
            )
        if project.space != DERIVATIVE_EXPORT_SPACE:
            raise ExportSnapshotError(
                "namespace_denied",
                f"only the {DERIVATIVE_EXPORT_SPACE!r} space is a derivative "
                "export target",
            )
        if project.status == "archived":
            raise ExportSnapshotError(
                "project_archived",
                "an archived derivative project cannot be exported",
            )

        chapters = list(
            (
                await self._session.scalars(
                    select(DerivativeChapter)
                    .where(
                        DerivativeChapter.owner_id == owner_id,
                        DerivativeChapter.novel_id == novel_id,
                        DerivativeChapter.project_id == project.id,
                    )
                    .order_by(DerivativeChapter.position, DerivativeChapter.id)
                )
            ).all()
        )

        export_chapters: list[DerivativeExportChapter] = []
        export_revisions: list[DerivativeExportRevision] = []
        citations: list[DerivativeExportCitation] = []
        revision_by_chapter: dict[int, PublishedDerivativeRevision] = {}

        for chapter in chapters:
            content = chapter.markdown or ""
            canonical = canonicalize_markdown(content)
            content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            # D-39-01 content parity: the frozen checksum must replay.
            if chapter.markdown_checksum != content_hash:
                raise ExportSnapshotError(
                    "chapter_checksum_mismatch",
                    f"chapter {chapter.id} content checksum does not replay its "
                    "stored markdown_checksum",
                )
            head = await _load_head_revision(self._session, chapter)
            override = await _load_approved_override(self._session, project, chapter)
            revision_id = head.id if head is not None else None

            if override is not None:
                if head is None:
                    raise ExportSnapshotError(
                        "published_revision_missing",
                        f"chapter {chapter.id} has an approved override but no "
                        "head revision; the export cannot be aligned",
                    )
                revision = _reconstruct_revision(project, chapter, head, override)
                errors = validate_published_revision(
                    revision,
                    owner_id=owner_id,
                    project_id=project.id,
                    fork_id=project.fork_id,
                    source_snapshot=project.source_snapshot_hash,
                    project_manifest_hash=project.manifest_hash,
                    chapter_version_id=chapter.revision,
                )
                if errors:
                    raise ExportSnapshotError(
                        errors[0],
                        f"published revision for chapter {chapter.id} failed "
                        f"parity: {errors}",
                    )
                revision_by_chapter[chapter.id] = revision
                export_revisions.append(
                    _to_export_revision(revision, chapter, chapter_number=chapter.position + 1)
                )
                evidence = dict(override.evidence_snapshot or {})
                for key in [str(k) for k in (evidence.get("citation_keys") or [])]:
                    citations.append(
                        DerivativeExportCitation(
                            citation_key=key,
                            citation_hash=canonical_citation_hash([key]),
                            source_snapshot=project.source_snapshot_hash,
                            revision_id=revision.revision_id,
                            chapter_number=chapter.position + 1,
                        )
                    )

            export_chapters.append(
                DerivativeExportChapter(
                    chapter_id=chapter.id,
                    position=chapter.position,
                    chapter_number=chapter.position + 1,
                    title=chapter.title or "",
                    content=canonical,
                    content_hash=content_hash,
                    markdown_checksum=chapter.markdown_checksum,
                    version_id=chapter.revision,
                    revision_id=revision_id,
                )
            )

        # Published assets (approved-only) + explicit missing-binary records.
        try:
            published_assets = await list_published_assets(
                self._session,
                owner_id=owner_id,
                novel_id=novel_id,
                project_id=project.id,
                fork_id=project.fork_id,
            )
        except PublishedAssetScopeError as exc:
            raise ExportSnapshotError(
                "invalid_scope", f"asset scope check failed: {exc}"
            ) from exc

        export_assets: list[DerivativeExportAsset] = []
        missing_assets: list[MissingDerivativeAssetRecord] = []
        available_hashes: set[str] = set()
        for asset in published_assets:
            errors = validate_published_asset(
                asset,
                owner_id=owner_id,
                project_id=project.id,
                fork_id=project.fork_id,
                source_snapshot_hash=project.source_snapshot_hash,
            )
            if errors:
                raise ExportSnapshotError(
                    errors[0],
                    f"published asset {asset.asset_id!r} failed parity: {errors}",
                )
            available_hashes.add(asset.content_hash)
            if self._asset_bytes_present(asset):
                export_assets.append(_to_export_asset(asset))
            else:
                missing_assets.append(
                    MissingDerivativeAssetRecord(
                        asset_id=asset.asset_id,
                        content_hash=asset.content_hash,
                        mime_type=asset.mime_type,
                        chapter_number=asset.chapter_number,
                        reason_code="asset_bytes_missing",
                        detail=(
                            f"published asset {asset.asset_id} bytes are missing "
                            "in the owner/novel scope; the export presents an "
                            "explicit placeholder and never invents a URL "
                            "(D-39-01)"
                        ),
                    )
                )

        # D-39-01 asset membership: every revision asset hash must be published.
        for revision in revision_by_chapter.values():
            errors = validate_asset_membership(revision, available_hashes)
            if errors:
                raise ExportSnapshotError(
                    errors[0],
                    f"published revision {revision.revision_id} references "
                    "asset hashes that are not members of the published asset set",
                )

        text_version_hash = canonical_export_hash(
            {
                "chapters": [
                    {
                        "chapter_number": chapter.chapter_number,
                        "content_hash": chapter.content_hash,
                        "version_id": chapter.version_id,
                    }
                    for chapter in export_chapters
                ]
            }
        )

        snapshot = ExportSnapshot(
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=project.id,
            project_key=project.project_key,
            project_name=project.name,
            fork_id=project.fork_id,
            fork_key=project.fork_key,
            source_snapshot=project.source_snapshot_hash,
            project_manifest_hash=project.manifest_hash,
            cutoff_snapshot_hash=project.cutoff_snapshot_hash,
            scope_hash=project.scope_hash,
            text_version_hash=text_version_hash,
            chapters=tuple(export_chapters),
            revisions=tuple(export_revisions),
            assets=tuple(export_assets),
            citations=tuple(citations),
            missing_assets=tuple(missing_assets),
            snapshot_hash="0" * 64,
        )
        return FrozenDerivativeExport(
            snapshot=seal_export_snapshot(snapshot), storage=self._storage
        )

    # ------------------------------------------------------- asset presence

    def _asset_bytes_present(
        self, asset: PublishedDerivativeVisualAsset
    ) -> bool:
        if self._storage is None:
            return False
        try:
            return self._storage.exists(
                owner_id=asset.owner_id,
                novel_id=asset.novel_id,
                visual_version_id=asset.visual_version.version_id,
                asset_id=asset.asset_id,
                mime_type=asset.mime_type,
            )
        except DerivativeAssetStorageError:
            return False


async def _load_head_revision(
    db: AsyncSession, chapter: DerivativeChapter
) -> DerivativeRevision | None:
    return await db.scalar(
        select(DerivativeRevision)
        .where(DerivativeRevision.chapter_id == chapter.id)
        .order_by(
            DerivativeRevision.revision_number.desc(),
            DerivativeRevision.id.desc(),
        )
        .limit(1)
    )


async def _load_approved_override(
    db: AsyncSession, project: DerivativeProject, chapter: DerivativeChapter
) -> DerivativeOverride | None:
    return await db.scalar(
        select(DerivativeOverride)
        .where(
            DerivativeOverride.owner_id == project.owner_id,
            DerivativeOverride.novel_id == project.novel_id,
            DerivativeOverride.project_id == project.id,
            DerivativeOverride.chapter_id == chapter.id,
            DerivativeOverride.approval_state == "approved",
        )
        .order_by(DerivativeOverride.id.desc())
        .limit(1)
    )


def _reconstruct_revision(
    project: DerivativeProject,
    chapter: DerivativeChapter,
    head: DerivativeRevision,
    override: DerivativeOverride,
) -> PublishedDerivativeRevision:
    """Rebuild the immutable 37-04 DTO from the approved override materialization."""
    evidence = dict(override.evidence_snapshot or {})
    return build_published_derivative_revision(
        owner_id=chapter.owner_id,
        project_id=project.id,
        fork_id=project.fork_id,
        revision_id=head.id,
        version_id=head.revision_number,
        source_snapshot=project.source_snapshot_hash,
        manifest_hash=project.manifest_hash,
        citation_keys=[str(k) for k in (evidence.get("citation_keys") or [])],
        approval_state=override.approval_state,
        approver_id=override.approver_id,
        approved_at=override.approved_at,
        approval_reason=override.approval_reason,
        override_kind=override.kind,
        override_reason=override.reason,
        gate_verdict=str(evidence.get("gate_verdict") or ""),
        gate_reason=evidence.get("gate_reason"),
        canon_delta_hash=override.canon_delta_hash,
        evidence_snapshot=evidence,
    )


def _to_export_revision(
    revision: PublishedDerivativeRevision,
    chapter: DerivativeChapter,
    *,
    chapter_number: int,
) -> DerivativeExportRevision:
    return DerivativeExportRevision(
        owner_id=revision.owner_id,
        project_id=revision.project_id,
        fork_id=revision.fork_id,
        revision_id=revision.revision_id,
        version_id=revision.version_id,
        chapter_id=chapter.id,
        chapter_number=chapter_number,
        status=revision.status,
        source_snapshot=revision.source_snapshot,
        manifest_hash=revision.manifest_hash,
        citation_hash=revision.citation_hash,
        asset_hashes=tuple(revision.asset_hashes or []),
        approval=dict(revision.approval),
        review=dict(revision.review),
    )


def _to_export_asset(asset: PublishedDerivativeVisualAsset) -> DerivativeExportAsset:
    return DerivativeExportAsset(
        asset_id=asset.asset_id,
        asset_key=asset.asset_key,
        content_hash=asset.content_hash,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        namespace=asset.namespace,
        scene_spec_hash=asset.scene_spec_hash,
        chapter_number=asset.chapter_number,
        visual_version=DerivativeVisualVersionExport(
            version_id=asset.visual_version.version_id,
            version_key=asset.visual_version.version_key,
            version_hash=asset.visual_version.version_hash,
        ),
        source_snapshot=DerivativeSourceSnapshotExport(
            source_snapshot_id=asset.source_snapshot.source_snapshot_id,
            source_snapshot_hash=asset.source_snapshot.source_snapshot_hash,
            source_manifest_hash=asset.source_snapshot.source_manifest_hash,
            cutoff_chapter=asset.source_snapshot.cutoff_chapter,
        ),
        approval=str(asset.approval),
        review_state=str(asset.review.review_state),
        review=asset.review.model_dump(mode="json"),
        source_refs=tuple(ref.model_dump(mode="json") for ref in asset.source_refs),
        identity_lineage=tuple(
            row.model_dump(mode="json") for row in asset.identity_lineage
        ),
        generator_lineage=dict(asset.generator_lineage),
        divergence_manifest_hash=asset.divergence_manifest_hash,
    )


__all__ = [
    "DERIVATIVE_EXPORT_ARTIFACT_KIND",
    "DERIVATIVE_EXPORT_SCHEMA_VERSION",
    "DERIVATIVE_EXPORT_SPACE",
    "DERIVATIVE_EXPORT_VERSION",
    "ExportSnapshot",
    "ExportSnapshotError",
    "ExportSnapshotService",
    "FrozenDerivativeExport",
    "export_snapshot_hash",
    "export_snapshot_payload",
    "seal_export_snapshot",
    "validate_asset_membership",
    "validate_published_asset",
    "validate_published_revision",
    "validate_revision_citation_hash",
]
