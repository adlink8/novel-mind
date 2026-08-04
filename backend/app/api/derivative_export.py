"""Owner-scoped derivative export API (Phase 39-01, REQ-FORK-05/REQ-CRE-07).

Routes hang under ``/api/novels/{novel_id}/derivative-projects/{project_id}/export``:

- ``POST .../prepare`` — freeze the owner-scoped derivative ``ExportSnapshot``
  (published revisions + published assets + citations + version lineage) and
  return the frozen manifest with the replayable export hash.
- ``GET .../download?format=markdown|epub`` — one deterministic file download.
  Every format is built by a serializer that consumes **only** the frozen
  snapshot; ``X-Export-Manifest-Hash`` carries the export hash and the body
  never invents a URL or silently drops a missing asset (D-39-01).

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404; the project/fork are resolved inside the current owner scope.
Original/future scope, archived projects and any parity/provenance mismatch fail
closed (D-39-02).
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
from app.services.derivative_export.epub import render_epub
from app.services.derivative_export.manifest import (
    DERIVATIVE_EXPORT_VERSION,
    DerivativeExportManifest,
    seal_derivative_export_manifest,
)
from app.services.derivative_export.markdown import render_markdown
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    ExportSnapshotService,
    FrozenDerivativeExport,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage

router = APIRouter(dependencies=[Depends(require_user)])

DERIVATIVE_EXPORT_PATH = "/{novel_id}/derivative-projects/{project_id}/export"

DerivativeExportFormat = Literal["markdown", "epub"]

_FORMAT_MEDIA_TYPES: dict[str, str] = {
    "markdown": "text/markdown",
    "epub": "application/epub+zip",
}
_FORMAT_EXTENSIONS: dict[str, str] = {
    "markdown": "md",
    "epub": "epub",
}


class DerivativeExportPrepareResponse(BaseModel):
    """Frozen derivative export manifest read envelope (D-39-01)."""

    manifest: DerivativeExportManifest
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    export_version: str = DERIVATIVE_EXPORT_VERSION
    chapter_count: int = Field(ge=0)
    asset_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    missing_asset_count: int = Field(ge=0)


# Storage seam (integration tests override the bytes backend, 34-04 pattern).
_derivative_export_asset_storage: DerivativeAssetStorage | None = None


def set_derivative_export_asset_storage(storage: DerivativeAssetStorage | None) -> None:
    """Override the derivative asset bytes backend (used by integration tests)."""
    global _derivative_export_asset_storage
    _derivative_export_asset_storage = storage


def _storage() -> DerivativeAssetStorage:
    if _derivative_export_asset_storage is not None:
        return _derivative_export_asset_storage
    return DerivativeAssetStorage(DerivativeAssetStorage.default_storage_root())


def _map_error(exc: ExportSnapshotError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


async def _freeze(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> FrozenDerivativeExport:
    try:
        return await ExportSnapshotService(db, storage=_storage()).build(
            owner_id=owner_id, novel_id=novel_id, project_id=project_id
        )
    except ExportSnapshotError as exc:
        raise _map_error(exc) from exc


def _export_filename(*, project_name: str, snapshot_hash: str, format: str) -> str:
    import re

    stem = (
        re.sub(r"[^\w\-]+", "-", project_name, flags=re.UNICODE).strip("-")
        or "derivative"
    )
    return f"{stem}-v{snapshot_hash[:8]}.{_FORMAT_EXTENSIONS[format]}"


@router.post(
    DERIVATIVE_EXPORT_PATH + "/prepare",
    response_model=DerivativeExportPrepareResponse,
)
async def prepare_derivative_export(
    project_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeExportPrepareResponse:
    """Freeze the derivative export snapshot + manifest (owner-scoped)."""
    frozen = await _freeze(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        project_id=project_id,
    )
    snapshot = frozen.snapshot
    manifest = seal_derivative_export_manifest(snapshot)
    return DerivativeExportPrepareResponse(
        manifest=manifest,
        manifest_hash=manifest.manifest_hash,
        snapshot_hash=snapshot.snapshot_hash,
        export_version=DERIVATIVE_EXPORT_VERSION,
        chapter_count=len(snapshot.chapters),
        asset_count=len(snapshot.assets),
        revision_count=len(snapshot.revisions),
        citation_count=len(snapshot.citations),
        missing_asset_count=len(snapshot.missing_assets),
    )


@router.get(DERIVATIVE_EXPORT_PATH + "/download")
async def download_derivative_export(
    project_id: int,
    format: DerivativeExportFormat = "markdown",
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Deterministic Markdown/EPUB download from the frozen snapshot."""
    frozen = await _freeze(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        project_id=project_id,
    )
    snapshot = frozen.snapshot
    reader = frozen.asset_reader()
    try:
        if format == "markdown":
            content = render_markdown(snapshot, reader)
        elif format == "epub":
            content = render_epub(snapshot, reader)
        else:  # pragma: no cover - Literal guards this branch
            raise ExportSnapshotError(
                "unsupported_format", f"unsupported export format: {format!r}"
            )
    except ExportSnapshotError as exc:
        raise _map_error(exc) from exc

    filename = _export_filename(
        project_name=snapshot.project_name,
        snapshot_hash=snapshot.snapshot_hash,
        format=format,
    )
    disposition = f'attachment; filename="{quote(filename)}"'
    return Response(
        content=content,
        media_type=_FORMAT_MEDIA_TYPES[format],
        headers={
            "Content-Disposition": disposition,
            "X-Export-Manifest-Hash": snapshot.snapshot_hash,
            "X-Export-Snapshot-Hash": snapshot.snapshot_hash,
            "X-Export-Format": format,
            "X-Export-Project-Id": str(snapshot.project_id),
        },
    )


__all__ = [
    "DERIVATIVE_EXPORT_PATH",
    "DerivativeExportPrepareResponse",
    "router",
    "set_derivative_export_asset_storage",
]
