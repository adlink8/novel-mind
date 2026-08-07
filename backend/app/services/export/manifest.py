"""Frozen novel export manifest and freeze service (Phase 34-04, REQ-VIS-05).

D-34-04: Markdown/HTML/EPUB export consumes **one frozen manifest** of the text
version, approved published assets, hash-verified anchors, captions, citations
and content/version hashes. This module is the export/parity plane (the
``reader_chat`` selection/evidence and ``narrative_memory/manifests.py`` analog):

- ``NovelExportManifest`` — strict, frozen, `extra="forbid"` contract that
  freezes owner/novel scope, the novel text version hash, every chapter with
  its content hash, every **published** anchor re-verified against the current
  chapter (D-34-01 read-side gate) and the approved asset refs it points to.
  A manifest hash makes the whole freeze replayable and byte-deterministic.
- ``ExportManifestService.freeze`` — one-time server-side freeze. It reads only
  the owner/novel-scoped DB rows (novel, chapters, published anchors, asset
  revisions), re-verifies every anchor hash/range/version against the *current*
  chapter content and reports a **missing asset as an explicit record** — never
  an invented URL and never a silent provenance drop (D-34-04).
- ``FrozenExport`` — the frozen manifest plus an internal storage seam that lets
  the deterministic adapters read the approved asset bytes without any
  independent DB read. A bytes-hash drift on read fails closed to "missing".

Approved-only: proposals (candidate-only rows) never appear; only published
anchor rows (``illustration_anchors``) are consumed. A stale/missing anchor is
presented explicitly (``stale`` / ``asset_missing`` / ``invalid``), never
silently relocated or dropped (D-34-01/03/04).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models import Chapter, Novel
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import IllustrationAnchor
from app.schemas.illustration_anchor import AnchorStatus
from app.services.illustrations.storage import AssetNotFound, AssetStorage

EXPORT_MANIFEST_SCHEMA_VERSION = "novel-export-manifest.v1"
EXPORT_MANIFEST_ARTIFACT_KIND = "novel_export_manifest"
HEX64_RE_SET = frozenset("0123456789abcdef")


class ExportManifestError(ValueError):
    """Fail-closed export manifest gate violation."""


class ExportAnchorStatus(StrEnum):
    """Read-side presentation status frozen into the export manifest.

    ``render`` is the only status that may embed the approved asset bytes; the
    other three are explicit, never silent (D-34-04).
    """

    RENDER = "render"
    STALE = "stale"
    ASSET_MISSING = "asset_missing"
    INVALID = "invalid"


def canonical_export_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in HEX64_RE_SET for ch in value)
    )


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ExportManifestError(
            "export scope identifiers must be explicit positive integers"
        )


# ---------------------------------------------------------------------------
# Frozen manifest contract (D-34-04: one manifest for Markdown/HTML/EPUB)
# ---------------------------------------------------------------------------


class _StrictExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportAssetRef(_StrictExportModel):
    """Approved published AssetRevision ref frozen into the manifest.

    ``cutoff_chapter`` is the scene-spec spoiler cutoff carried as provenance so
    an export consumer always knows how much source plot the approved asset may
    reflect (must-have: owner/version/source snapshot/evidence/cutoff scope).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_revision_id: int = Field(gt=0)
    asset_id: str = Field(min_length=1, max_length=200)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    cutoff_chapter: int = Field(ge=1)


class ExportAnchorEntry(_StrictExportModel):
    """One published anchor re-verified for the current chapter (D-34-01/04)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_id: int = Field(gt=0)
    anchor_key: str = Field(min_length=1, max_length=160)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    paragraph_start: int | None = Field(default=None, ge=1)
    paragraph_end: int | None = Field(default=None, ge=1)
    excerpt: str = Field(min_length=1, max_length=20000)
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    caption: str = Field(min_length=1, max_length=500)
    alt_text: str = Field(min_length=1, max_length=500)
    citation: str = Field(min_length=1, max_length=1000)
    status: ExportAnchorStatus
    reason_code: str | None = None
    detail: str | None = None
    # Frozen published asset ref (may be present even when the bytes are
    # missing/stale so provenance is never silently dropped).
    asset: ExportAssetRef | None = None


class ExportChapter(_StrictExportModel):
    """One chapter's frozen text version plus its verified anchors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    title: str = Field(max_length=200)
    content: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchors: tuple[ExportAnchorEntry, ...] = Field(default_factory=tuple)


class MissingAssetRecord(_StrictExportModel):
    """Explicit missing-asset record; a missing binary is never a silent drop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_revision_id: int = Field(gt=0)
    asset_id: str = Field(min_length=1, max_length=200)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    reason_code: str
    detail: str


class NovelExportManifest(_StrictExportModel):
    """Frozen novel export manifest consumed by Markdown/HTML/EPUB adapters.

    Ordering is deterministic (chapters by number, anchors by source offset,
    assets by revision id) so the same DB state always freezes to the same
    manifest hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = EXPORT_MANIFEST_SCHEMA_VERSION
    artifact_kind: str = EXPORT_MANIFEST_ARTIFACT_KIND
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    novel_title: str = Field(min_length=1, max_length=200)
    novel_author: str | None = None
    text_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapters: tuple[ExportChapter, ...]
    assets: tuple[ExportAssetRef, ...] = Field(default_factory=tuple)
    missing_assets: tuple[MissingAssetRecord, ...] = Field(default_factory=tuple)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def novel_export_manifest_hash(
    manifest: NovelExportManifest | Mapping[str, Any],
) -> str:
    """Replay the frozen manifest hash from a manifest contract or its JSON."""
    if isinstance(manifest, NovelExportManifest):
        payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    else:
        payload = dict(manifest)
        payload.pop("manifest_hash", None)
    return canonical_export_hash(payload)


# ---------------------------------------------------------------------------
# Frozen export (manifest + internal storage seam, no independent DB reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenExport:
    """The frozen manifest plus the internal asset-bytes reader seam.

    ``storage_keys`` is an internal owner/novel-scoped mapping from asset
    revision id to storage key, captured once during freeze. The deterministic
    adapters consume only this object — they never read the database.
    """

    manifest: NovelExportManifest
    storage_keys: Mapping[int, str] = field(default_factory=dict)
    storage: AssetStorage | None = None

    def asset_reader(self) -> Callable[[ExportAssetRef], bytes | None]:
        """Read approved asset bytes by revision id; hash-drift fails closed.

        Returns ``None`` (treated as a missing asset by the adapters) when the
        bytes do not exist or their content hash does not replay — the export
        never invents a URL or embeds tampered bytes.
        """

        def _read(asset: ExportAssetRef) -> bytes | None:
            key = self.storage_keys.get(asset.asset_revision_id)
            if not key or self.storage is None:
                return None
            try:
                payload = self.storage.read(
                    owner_id=self.manifest.owner_id,
                    novel_id=self.manifest.novel_id,
                    storage_key=key,
                )
            except AssetNotFound:
                return None
            if hashlib.sha256(payload).hexdigest() != asset.bytes_hash:
                return None
            return payload

        return _read


# ---------------------------------------------------------------------------
# Freeze service
# ---------------------------------------------------------------------------


class ExportManifestService:
    """Owner/novel-scoped, approved-only export manifest freeze (D-34-04)."""

    def __init__(
        self, session: AsyncSession, *, storage: AssetStorage | None = None
    ) -> None:
        self._session = session
        self._storage = storage or AssetStorage(AssetStorage.default_storage_root())

    async def freeze(self, *, owner_id: int, novel_id: int) -> FrozenExport:
        """Freeze text/assets/anchors/citations/hashes once for the export.

        Reads only owner/novel-scoped rows (novel, chapters, published anchors,
        referenced asset revisions), re-verifies every anchor against the
        current chapter content and reports missing bytes explicitly. No
        candidate proposal can ever reach the manifest (approved-only).
        """
        _require_scope(owner_id=owner_id, novel_id=novel_id)
        novel = await self._session.scalar(
            select(Novel).where(
                Novel.id == novel_id,
                Novel.owner_id == owner_id,
            )
        )
        if novel is None:
            raise ExportManifestError(
                "novel not found in the owner scope (indistinguishable from 404)"
            )

        chapters = list(
            (
                await self._session.scalars(
                    select(Chapter)
                    .options(undefer(Chapter.content))
                    .where(Chapter.novel_id == novel_id)
                    .order_by(Chapter.chapter_number, Chapter.id)
                )
            ).all()
        )
        anchors = list(
            (
                await self._session.scalars(
                    select(IllustrationAnchor)
                    .where(
                        IllustrationAnchor.owner_id == owner_id,
                        IllustrationAnchor.novel_id == novel_id,
                    )
                    .order_by(
                        IllustrationAnchor.chapter_id,
                        IllustrationAnchor.source_start,
                        IllustrationAnchor.id,
                    )
                )
            ).all()
        )
        asset_revision_ids = {anchor.published_asset_revision_id for anchor in anchors}
        asset_rows: dict[int, AssetRevision] = {}
        if asset_revision_ids:
            asset_rows = {
                row.id: row
                for row in (
                    await self._session.scalars(
                        select(AssetRevision).where(
                            AssetRevision.owner_id == owner_id,
                            AssetRevision.novel_id == novel_id,
                            AssetRevision.id.in_(asset_revision_ids),
                        )
                    )
                ).all()
            }

        # Renderable assets (bytes present) and explicit missing-asset records.
        renderable: dict[int, tuple[AssetRevision, str]] = {}
        missing_assets: dict[int, MissingAssetRecord] = {}
        for anchor in anchors:
            row = asset_rows.get(anchor.published_asset_revision_id)
            if row is None:
                continue
            if self._storage.exists(
                owner_id=owner_id, novel_id=novel_id, storage_key=row.storage_key
            ):
                renderable[row.id] = (row, row.storage_key)
            else:
                missing_assets[row.id] = MissingAssetRecord(
                    asset_revision_id=row.id,
                    asset_id=row.asset_id,
                    bytes_hash=row.bytes_hash,
                    mime_type=row.mime_type,
                    reason_code="asset_bytes_missing",
                    detail=(
                        f"asset bytes for revision {row.id} are missing in the "
                        "owner/novel scope; the export presents an explicit "
                        "placeholder and never invents a URL (D-34-04)"
                    ),
                )

        export_chapters: list[ExportChapter] = []
        for chapter in chapters:
            content = chapter.content or ""
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chapter_anchors = [
                anchor for anchor in anchors if anchor.chapter_id == chapter.id
            ]
            entries: list[ExportAnchorEntry] = []
            for anchor in sorted(chapter_anchors, key=lambda a: (a.source_start, a.id)):
                status, reason_code, detail = self._classify_anchor(anchor, content)
                row = asset_rows.get(anchor.published_asset_revision_id)
                asset_ref: ExportAssetRef | None = None
                if row is not None:
                    asset_ref = ExportAssetRef(
                        asset_revision_id=row.id,
                        asset_id=row.asset_id,
                        bytes_hash=row.bytes_hash,
                        mime_type=row.mime_type,
                        cutoff_chapter=row.cutoff_chapter,
                    )
                    if status is ExportAnchorStatus.RENDER:
                        if row.id not in renderable:
                            status = ExportAnchorStatus.ASSET_MISSING
                            reason_code = "asset_bytes_missing"
                            detail = (
                                f"asset bytes for revision {row.id} are missing "
                                "in the owner/novel scope (D-34-04)"
                            )
                entries.append(
                    ExportAnchorEntry(
                        anchor_id=anchor.id,
                        anchor_key=anchor.anchor_key,
                        chapter_id=anchor.chapter_id,
                        chapter_number=anchor.chapter_number,
                        source_start=anchor.source_start,
                        source_end=anchor.source_end,
                        paragraph_start=anchor.paragraph_start,
                        paragraph_end=anchor.paragraph_end,
                        excerpt=anchor.excerpt,
                        anchor_hash=anchor.anchor_hash,
                        chapter_content_hash=anchor.chapter_content_hash,
                        source_snapshot_id=anchor.source_snapshot_id,
                        source_snapshot_hash=anchor.source_snapshot_hash,
                        caption=anchor.caption,
                        alt_text=anchor.alt_text,
                        citation=anchor.citation,
                        status=status,
                        reason_code=reason_code,
                        detail=detail,
                        asset=asset_ref,
                    )
                )
            export_chapters.append(
                ExportChapter(
                    chapter_id=chapter.id,
                    chapter_number=chapter.chapter_number,
                    title=chapter.title or "",
                    content=content,
                    content_hash=content_hash,
                    anchors=tuple(entries),
                )
            )

        assets = tuple(
            sorted(
                (
                    ExportAssetRef(
                        asset_revision_id=row.id,
                        asset_id=row.asset_id,
                        bytes_hash=row.bytes_hash,
                        mime_type=row.mime_type,
                        cutoff_chapter=row.cutoff_chapter,
                    )
                    for row, _ in renderable.values()
                ),
                key=lambda ref: ref.asset_revision_id,
            )
        )

        text_version_hash = canonical_export_hash(
            {
                "chapters": [
                    {
                        "chapter_number": chapter.chapter_number,
                        "content_hash": chapter.content_hash,
                    }
                    for chapter in export_chapters
                ]
            }
        )

        manifest = NovelExportManifest(
            owner_id=owner_id,
            novel_id=novel_id,
            novel_title=novel.title,
            novel_author=novel.author,
            text_version_hash=text_version_hash,
            chapters=tuple(export_chapters),
            assets=assets,
            missing_assets=tuple(
                sorted(missing_assets.values(), key=lambda r: r.asset_revision_id)
            ),
            manifest_hash="0" * 64,
        )
        manifest = manifest.model_copy(
            update={"manifest_hash": novel_export_manifest_hash(manifest)}
        )

        storage_keys = {
            row_id: storage_key for row_id, (row, storage_key) in renderable.items()
        }
        return FrozenExport(
            manifest=manifest,
            storage_keys=storage_keys,
            storage=self._storage,
        )

    # ------------------------------------------------------ read-side gate

    @staticmethod
    def _classify_anchor(
        anchor: IllustrationAnchor, chapter_content: str
    ) -> tuple[ExportAnchorStatus, str | None, str | None]:
        """D-34-01 read-side re-verification for the frozen export manifest.

        Mirrors ``validate_exact_source`` / the reader's
        ``verifyAnchorAgainstChapter``: a published ``valid`` anchor renders
        only when its exact hash/range/version replay against the current
        chapter content; anything else is ``stale`` / ``invalid`` and never
        relocates to a nearby paragraph.
        """
        if anchor.status == AnchorStatus.NEEDS_REPAIR.value:
            return (
                ExportAnchorStatus.STALE,
                "needs_repair",
                "anchor is explicitly awaiting repair (D-34-03)",
            )
        if anchor.status == AnchorStatus.INVALID.value:
            return (
                ExportAnchorStatus.INVALID,
                "invalid_status",
                "anchor is explicitly invalid (D-34-03)",
            )
        if anchor.status != AnchorStatus.VALID.value:
            return (
                ExportAnchorStatus.INVALID,
                "not_valid_status",
                "only a server-published valid anchor may render an asset",
            )
        if not _is_hex64(anchor.anchor_hash) or not _is_hex64(
            anchor.chapter_content_hash
        ):
            return (
                ExportAnchorStatus.INVALID,
                "malformed_hash",
                "anchor_hash and chapter_content_hash must be 64-hex hashes",
            )
        if (
            anchor.anchor_hash
            != hashlib.sha256(anchor.excerpt.encode("utf-8")).hexdigest()
        ):
            return (
                ExportAnchorStatus.INVALID,
                "anchor_hash_mismatch",
                "anchor_hash does not replay from the frozen source excerpt",
            )
        if hashlib.sha256(chapter_content.encode("utf-8")).hexdigest() != (
            anchor.chapter_content_hash
        ):
            return (
                ExportAnchorStatus.STALE,
                "text_version_drift",
                "chapter content hash does not replay the current text version",
            )
        if (
            anchor.source_start < 0
            or anchor.source_end > len(chapter_content)
            or anchor.source_end <= anchor.source_start
        ):
            return (
                ExportAnchorStatus.STALE,
                "source_range_out_of_bounds",
                "source span is outside the current chapter content",
            )
        if chapter_content[anchor.source_start : anchor.source_end] != anchor.excerpt:
            return (
                ExportAnchorStatus.STALE,
                "source_range_mismatch",
                "source span does not replay the excerpt; the anchor is stale "
                "and never relocated (D-34-01)",
            )
        return ExportAnchorStatus.RENDER, None, None


__all__ = [
    "EXPORT_MANIFEST_ARTIFACT_KIND",
    "EXPORT_MANIFEST_SCHEMA_VERSION",
    "ExportAnchorEntry",
    "ExportAnchorStatus",
    "ExportAssetRef",
    "ExportChapter",
    "ExportManifestError",
    "ExportManifestService",
    "FrozenExport",
    "MissingAssetRecord",
    "NovelExportManifest",
    "canonical_export_hash",
    "novel_export_manifest_hash",
]
