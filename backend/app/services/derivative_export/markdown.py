"""Deterministic Markdown serializer for the frozen derivative export (Phase 39-01).

D-39-01 / REQ-CRE-07: ``render_markdown`` consumes **only** the frozen
``ExportSnapshot`` (via ``FrozenDerivativeExport``) — no independent DB read.
Chapter order, content, asset figures, the citation package and the version
manifest all come from the same snapshot, so the same snapshot always renders
the same bytes and Markdown/EPUB3 stay aligned.

A missing/hash-drifted binary is an explicit placeholder in its chapter (never
an invented URL or a silent drop); the machine-readable missing-asset report is
always present.
"""

from __future__ import annotations

import hashlib
from typing import Callable

from app.services.derivative_export.manifest import (
    DerivativeExportAsset,
    DerivativeExportCitation,
)
from app.services.derivative_export.snapshot import ExportSnapshot
from app.services.derivative_visual.assets import ALLOWED_DERIVATIVE_MIME_TYPES


def asset_filename(asset: DerivativeExportAsset) -> str:
    """Content-addressed, deterministic export filename (never a raw path)."""
    extension = ALLOWED_DERIVATIVE_MIME_TYPES.get(asset.mime_type, ".img")
    return f"{asset.content_hash}{extension}"


def render_markdown(
    snapshot: ExportSnapshot,
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> bytes:
    """Build a deterministic Markdown document from the frozen snapshot."""
    lines: list[str] = []
    lines.append(f"# {snapshot.project_name}")
    lines.append("")
    lines.append(
        "<!-- NovelMind derivative export manifest "
        f"{snapshot.snapshot_hash}; owner_id={snapshot.owner_id}; "
        f"novel_id={snapshot.novel_id}; project_id={snapshot.project_id}; "
        f"fork_id={snapshot.fork_id}; text_version_hash={snapshot.text_version_hash} -->"
    )
    lines.append("")

    assets_by_chapter: dict[int, list[DerivativeExportAsset]] = {}
    for asset in snapshot.assets:
        assets_by_chapter.setdefault(asset.chapter_number, []).append(asset)
    missing_by_chapter: dict[int, list] = {}
    for record in snapshot.missing_assets:
        missing_by_chapter.setdefault(record.chapter_number, []).append(record)

    for chapter in snapshot.chapters:
        title = chapter.title or f"第 {chapter.chapter_number} 章"
        lines.append(f"## {title}")
        lines.append("")
        lines.append(
            f"<!-- 章节 {chapter.chapter_number} version_id={chapter.version_id} "
            f"content_hash={chapter.content_hash} "
            f"markdown_checksum={chapter.markdown_checksum} -->"
        )
        lines.append("")
        for paragraph in chapter.content.split("\n"):
            lines.append(paragraph if paragraph else "")
            lines.append("")
        for asset in assets_by_chapter.get(chapter.chapter_number, []):
            lines.append(_asset_figure(asset, asset_reader))
            lines.append("")
        for record in missing_by_chapter.get(chapter.chapter_number, []):
            lines.append(_missing_figure(record))
            lines.append("")

    lines.append("## 引用")
    lines.append("")
    if snapshot.citations:
        for citation in snapshot.citations:
            lines.append(_citation_line(citation))
    else:
        lines.append("无引用")
    lines.append("")

    lines.append("## 导出清单")
    lines.append("")
    lines.append(f"- schema_version: {snapshot.schema_version}")
    lines.append(f"- export_version: {snapshot.export_version}")
    lines.append(f"- manifest_hash: {snapshot.snapshot_hash}")
    lines.append(f"- text_version_hash: {snapshot.text_version_hash}")
    lines.append(f"- source_snapshot: {snapshot.source_snapshot}")
    lines.append(f"- project_manifest_hash: {snapshot.project_manifest_hash}")
    lines.append(f"- revisions: {len(snapshot.revisions)}")
    lines.append(f"- assets: {len(snapshot.assets)}")
    lines.append(f"- citations: {len(snapshot.citations)}")
    lines.append("")
    if snapshot.missing_assets:
        for record in snapshot.missing_assets:
            lines.append(
                f"- 缺失资产 asset_id={record.asset_id} "
                f"content_hash={record.content_hash} "
                f"chapter={record.chapter_number} "
                f"（{record.reason_code}）：{record.detail}"
            )
    else:
        lines.append("- 无缺失资产")
    lines.append("")

    return "\n".join(lines).encode("utf-8")


def _asset_figure(
    asset: DerivativeExportAsset,
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> str:
    payload = asset_reader(asset)
    # Defense in depth (T-39-01-02): never embed bytes that do not replay the
    # frozen content hash — a drift degrades to the explicit placeholder.
    if payload is not None and hashlib.sha256(payload).hexdigest() == asset.content_hash:
        src = f"assets/{asset_filename(asset)}"
        return (
            "<figure class=\"derivative-export-asset\">"
            f'<img src="{src}" alt="{asset.asset_id}"/>'
            "<figcaption>"
            f"asset_id={asset.asset_id} chapter={asset.chapter_number} "
            f"content_hash={asset.content_hash}"
            "</figcaption></figure>"
        )
    return _missing_figure(asset)


def _missing_figure(asset) -> str:
    reason = getattr(asset, "reason_code", None) or "asset_bytes_missing"
    detail = getattr(asset, "detail", None) or (
        "asset bytes missing; the export presents an explicit placeholder "
        "and never invents a URL (D-39-01)"
    )
    content_hash = getattr(asset, "content_hash", None)
    hash_part = f" content_hash={content_hash}" if content_hash else ""
    return (
        "> **插图缺失**：asset_id="
        f"{asset.asset_id}（{reason}{hash_part}）"
        + f" {detail}"
    )


def _citation_line(citation: DerivativeExportCitation) -> str:
    return (
        f"- `{citation.citation_key}` "
        f"citation_hash={citation.citation_hash} "
        f"source_snapshot={citation.source_snapshot} "
        f"revision_id={citation.revision_id} "
        f"chapter={citation.chapter_number}"
    )


__all__ = ["asset_filename", "render_markdown"]
