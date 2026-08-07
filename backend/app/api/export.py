"""Owner-scoped novel export API (Phase 34-04, REQ-VIS-05 / D-34-04).

Markdown/HTML/EPUB export of the frozen manifest:

- ``GET /api/novels/{novel_id}/export/manifest`` — the frozen novel export
  manifest (text version, approved assets, verified anchors, captions,
  citations, hashes, explicit missing-asset records) + replayable manifest
  hash. Reader/export read exactly this frozen contract.
- ``GET /api/novels/{novel_id}/export?format=markdown|html|epub`` — one
  deterministic file download. Every format is built by an adapter that
  consumes **only** the frozen manifest; ``X-Export-Manifest-Hash`` carries the
  manifest hash and the body never invents a URL or silently drops a missing
  asset (D-34-04).

Every route uses ``require_owned_novel``; a novel outside the caller's
owner/novel scope is indistinguishable from "not found". FastAPI owns state; the
adapters are deterministic and make no independent database reads.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.services.export.epub import build_epub
from app.services.export.html import build_html_export
from app.services.export.manifest import (
    ExportManifestError,
    ExportManifestService,
    FrozenExport,
    NovelExportManifest,
)
from app.services.export.markdown import build_markdown
from app.services.illustrations.storage import AssetStorage

router = APIRouter(dependencies=[Depends(require_user)])

ExportFormat = Literal["markdown", "html", "epub"]

_FORMAT_MEDIA_TYPES: dict[str, str] = {
    "markdown": "text/markdown",
    "html": "text/html; charset=utf-8",
    "epub": "application/epub+zip",
}
_FORMAT_EXTENSIONS: dict[str, str] = {
    "markdown": "md",
    "html": "html",
    "epub": "epub",
}


class ExportManifestEnvelope(BaseModel):
    """Frozen manifest read envelope (D-34-04)."""

    manifest: NovelExportManifest
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


# Storage seam (test override + deployment default) mirroring illustrations.py.
_export_asset_storage: AssetStorage | None = None


def set_export_asset_storage(storage: AssetStorage | None) -> None:
    """Override the asset-bytes backend (used by integration tests)."""
    global _export_asset_storage
    _export_asset_storage = storage


def _storage() -> AssetStorage:
    if _export_asset_storage is not None:
        return _export_asset_storage
    return AssetStorage(AssetStorage.default_storage_root())


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="小说不存在")


def _unsupported(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


async def _freeze(db: AsyncSession, *, owner_id: int, novel_id: int) -> FrozenExport:
    try:
        return await ExportManifestService(db, storage=_storage()).freeze(
            owner_id=owner_id, novel_id=novel_id
        )
    except ExportManifestError as exc:
        raise _not_found() from exc


def _export_filename(*, novel_title: str, text_version_hash: str, format: str) -> str:
    import re

    stem = re.sub(r"[^\w\-]+", "-", novel_title, flags=re.UNICODE).strip("-") or "novel"
    return f"{stem}-v{text_version_hash[:8]}.{_FORMAT_EXTENSIONS[format]}"


@router.get(
    "/{novel_id}/export/manifest",
    response_model=ExportManifestEnvelope,
)
async def get_export_manifest(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Frozen novel export manifest (one source of truth for reader/export)."""
    frozen = await _freeze(db, owner_id=current_user.id, novel_id=novel.id)
    return ExportManifestEnvelope(
        manifest=frozen.manifest,
        manifest_hash=frozen.manifest.manifest_hash,
    )


@router.get("/{novel_id}/export")
async def export_novel(
    format: ExportFormat = "markdown",
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Deterministic Markdown/HTML/EPUB download from the frozen manifest."""
    frozen = await _freeze(db, owner_id=current_user.id, novel_id=novel.id)
    manifest = frozen.manifest
    try:
        if format == "markdown":
            content = build_markdown(frozen)
        elif format == "html":
            content = build_html_export(frozen)
        elif format == "epub":
            content = build_epub(frozen)
        else:  # pragma: no cover - Literal guards this branch
            raise _unsupported(f"unsupported export format: {format!r}")
    except ExportManifestError as exc:  # pragma: no cover - defensive
        raise _unsupported(str(exc)) from exc

    filename = _export_filename(
        novel_title=manifest.novel_title,
        text_version_hash=manifest.text_version_hash,
        format=format,
    )
    disposition = f'attachment; filename="{quote(filename)}"'
    return Response(
        content=content,
        media_type=_FORMAT_MEDIA_TYPES[format],
        headers={
            "Content-Disposition": disposition,
            "X-Export-Manifest-Hash": manifest.manifest_hash,
            "X-Export-Format": format,
        },
    )
