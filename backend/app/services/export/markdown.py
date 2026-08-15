"""Deterministic Markdown adapter for the frozen novel export (Phase 34-04, REQ-VIS-05).

D-34-04: consumes **only** the frozen ``NovelExportManifest`` (via
``FrozenExport``) — no independent DB reads. Approved asset references use the
content-addressed export filename (never an invented URL); missing/stale/invalid
anchors are explicit visible notes plus a final machine-readable missing-asset
report, so a missing binary is never silently dropped (D-34-04).
"""

from __future__ import annotations

from app.services.export.manifest import (
    ExportAnchorEntry,
    ExportAnchorStatus,
    FrozenExport,
)
from app.services.export.html import asset_filename, escape_text


def _anchor_note(entry: ExportAnchorEntry, label: str) -> str:
    caption = entry.caption
    anchor_ref = entry.anchor_key
    reason = entry.reason_code or entry.detail or "explicit placeholder"
    return (
        f"> **{label}**：{escape_text(caption)}"
        f"（anchor={escape_text(anchor_ref)}，{escape_text(reason)}）"
    )


def build_markdown(frozen: FrozenExport) -> bytes:
    """Build a deterministic Markdown document from the frozen export."""
    manifest = frozen.manifest

    lines: list[str] = []
    lines.append(f"# {manifest.novel_title}")
    lines.append("")
    lines.append(
        "<!-- NovelMind export manifest "
        f"{manifest.manifest_hash}; owner_id={manifest.owner_id}; "
        f"novel_id={manifest.novel_id}; text_version_hash={manifest.text_version_hash} -->"
    )
    lines.append("")

    for chapter in manifest.chapters:
        title = chapter.title or f"第 {chapter.chapter_number} 章"
        lines.append(f"## {title}")
        lines.append("")
        paragraph_figures: dict[int, list[ExportAnchorEntry]] = {}
        for entry in sorted(
            chapter.anchors, key=lambda a: (a.source_start, a.anchor_id)
        ):
            # Mirror html.render_chapter_body: the figure is a sibling block
            # after the paragraph that contains the exact source offset.
            index = _paragraph_index_for_offset(chapter.content, entry.source_start)
            paragraph_figures.setdefault(index, []).append(entry)

        for index, paragraph in enumerate(chapter.content.split("\n")):
            if paragraph:
                lines.append(paragraph)
            else:
                lines.append("")
            for entry in paragraph_figures.get(index, []):
                lines.append(_markdown_figure(entry))
            lines.append("")

    lines.append("## 导出报告")
    lines.append("")
    if manifest.missing_assets:
        for record in manifest.missing_assets:
            lines.append(
                f"- 缺失资产 asset_revision_id={record.asset_revision_id} "
                f"bytes_hash={record.bytes_hash} "
                f"（{escape_text(record.reason_code)}）：{escape_text(record.detail)}"
            )
    else:
        lines.append("无缺失资产")
    lines.append("")

    return "\n".join(lines).encode("utf-8")


def _paragraph_index_for_offset(content: str, offset: int) -> int:
    paragraphs = content.split("\n")
    cursor = 0
    for index, paragraph in enumerate(paragraphs):
        if offset <= cursor + len(paragraph):
            return index
        cursor += len(paragraph) + 1
    return max(0, len(paragraphs) - 1)


def _markdown_figure(entry: ExportAnchorEntry) -> str:
    if entry.status is ExportAnchorStatus.RENDER and entry.asset is not None:
        src = f"assets/{asset_filename(entry.asset)}"
        alt = escape_text(entry.alt_text or entry.caption)
        caption = escape_text(entry.caption)
        citation = f"引用：{escape_text(entry.citation)}" if entry.citation else ""
        return (
            '<figure class="export-illustration">'
            f'<img src="{src}" alt="{alt}"/>'
            "<figcaption>"
            f'<span class="export-caption">{caption}</span>'
            f'<span class="export-citation">{citation}</span>'
            "</figcaption></figure>"
        )
    if entry.status is ExportAnchorStatus.ASSET_MISSING:
        return _anchor_note(entry, "插图缺失")
    if entry.status is ExportAnchorStatus.STALE:
        return _anchor_note(entry, "插图待修复")
    return _anchor_note(entry, "插图已失效")


__all__ = ["build_markdown"]
