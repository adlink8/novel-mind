"""Deterministic HTML adapter for the frozen novel export (Phase 34-04, REQ-VIS-05).

D-34-04: the HTML adapter consumes **only** the frozen ``NovelExportManifest``
(via ``FrozenExport``) — no independent database reads, no invented URLs. It
shares the exact chapter body / figure markup generator with the EPUB adapter so
HTML and EPUB3 stay in parity:

- ``render_anchor_figure`` — one explicit, accessible ``<figure>`` per published
  anchor. ``render`` embeds the approved asset bytes (data-URI in the
  self-contained HTML document, relative href in EPUB); ``asset_missing`` /
  ``stale`` / ``invalid`` are explicit placeholders with caption and reason, and
  never a broken URL or silent drop.
- ``render_chapter_body`` / ``render_chapter_xhtml`` — the shared body generator
  used by both ``build_html_export`` and ``epub.build_epub``, giving HTML/EPUB3
  byte-level parity for the same frozen manifest.
- ``build_html_export`` — a self-contained HTML document embedding approved
  asset bytes as data URIs plus an explicit export report for missing assets.
"""

from __future__ import annotations

import base64
from html import escape
from typing import Callable

from app.services.export.manifest import (
    ExportAnchorEntry,
    ExportAssetRef,
    ExportAnchorStatus,
    ExportChapter,
    FrozenExport,
)

# ---------------------------------------------------------------------------
# Asset reference helpers (shared by the deterministic adapters)
# ---------------------------------------------------------------------------


def asset_filename(asset: ExportAssetRef) -> str:
    """Content-addressed, deterministic export filename for an asset.

    The name is derived from the frozen bytes hash + MIME extension so the
    provenance is addressable and collisions are impossible; a non-allowlisted
    MIME still produces an explicit, deterministic name (never a raw path).
    """
    extension = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(asset.mime_type, ".img")
    return f"{asset.bytes_hash}{extension}"


# ---------------------------------------------------------------------------
# Shared figure / chapter body markup (HTML/EPUB3 parity)
# ---------------------------------------------------------------------------


def escape_text(value: str) -> str:
    return escape(value, quote=True)


def _figure_attrs(entry: ExportAnchorEntry) -> str:
    return (
        ' class="export-illustration"'
        f' data-anchor-id="{entry.anchor_id}"'
        f' data-anchor-key="{escape_text(entry.anchor_key)}"'
        f' data-anchor-status="{entry.status.value}"'
    )


def _caption_html(entry: ExportAnchorEntry) -> str:
    caption = escape_text(entry.caption)
    if entry.citation:
        citation = escape_text(entry.citation)
        return (
            '<figcaption><span class="export-caption">'
            f"{caption}"
            '</span><span class="export-citation">引用：'
            f"{citation}"
            "</span></figcaption>"
        )
    return f'<figcaption><span class="export-caption">{caption}</span></figcaption>'


def render_anchor_figure(
    entry: ExportAnchorEntry,
    asset_src_resolver: Callable[[ExportAssetRef], str | None],
) -> str:
    """Render one frozen anchor as an explicit accessible figure.

    ``asset_src_resolver`` maps an approved asset ref to a source string for the
    current format (data URI / relative href); ``None`` means the bytes are
    unavailable and the figure degrades to an explicit missing placeholder.
    """
    if entry.status is ExportAnchorStatus.RENDER:
        src = asset_src_resolver(entry.asset) if entry.asset else None
        if src is None:
            return (
                f"<figure{_figure_attrs(entry)} data-reason=\"asset_bytes_missing\">"
                '<div class="export-missing"><strong>插图缺失</strong>'
                f"<p>{escape_text(entry.detail or '')}</p></div>"
                f"{_caption_html(entry)}"
                "</figure>"
            )
        alt = escape_text(entry.alt_text or entry.caption)
        src_escaped = escape(src, quote=True)
        return (
            f"<figure{_figure_attrs(entry)}>"
            f'<img src="{src_escaped}" alt="{alt}" loading="lazy"/>'
            f"{_caption_html(entry)}"
            "</figure>"
        )
    if entry.status is ExportAnchorStatus.ASSET_MISSING:
        reason = escape_text(entry.reason_code or "asset_bytes_missing")
        return (
            f"<figure{_figure_attrs(entry)} data-reason=\"{reason}\">"
            '<div class="export-missing"><strong>插图缺失</strong>'
            f"<p>{escape_text(entry.detail or '')}</p></div>"
            f"{_caption_html(entry)}"
            "</figure>"
        )
    if entry.status is ExportAnchorStatus.STALE:
        reason = escape_text(entry.reason_code or "stale")
        return (
            f"<figure{_figure_attrs(entry)} data-reason=\"{reason}\">"
            '<div class="export-stale"><strong>插图待修复</strong>'
            f"<p>{escape_text(entry.detail or '')}</p></div>"
            f"{_caption_html(entry)}"
            "</figure>"
        )
    reason = escape_text(entry.reason_code or "invalid")
    return (
        f"<figure{_figure_attrs(entry)} data-reason=\"{reason}\">"
        '<div class="export-invalid"><strong>插图已失效</strong>'
        f"<p>{escape_text(entry.detail or '')}</p></div>"
        f"{_caption_html(entry)}"
        "</figure>"
    )


def _paragraph_index_for_offset(content: str, offset: int) -> int:
    """Map an exact source offset to the paragraph that contains it.

    Python ``str`` indexing is code-point based, matching the frozen anchor
    offsets; the figure is inserted as a sibling block after that paragraph
    (the reader's flow-layout analog, D-34-02) — never mid-text injection.
    """
    paragraphs = content.split("\n")
    cursor = 0
    for index, paragraph in enumerate(paragraphs):
        if offset <= cursor + len(paragraph):
            return index
        cursor += len(paragraph) + 1
    return max(0, len(paragraphs) - 1)


def render_chapter_body(
    chapter: ExportChapter,
    asset_src_resolver: Callable[[ExportAssetRef], str | None],
) -> str:
    """Render one frozen chapter as HTML paragraphs + anchor figures.

    This exact body generator is shared with the EPUB adapter, so HTML and
    EPUB3 render the same frozen manifest byte-for-byte at the body level.
    """
    paragraphs = chapter.content.split("\n")
    by_paragraph: dict[int, list[ExportAnchorEntry]] = {}
    for entry in sorted(chapter.anchors, key=lambda a: (a.source_start, a.anchor_id)):
        index = _paragraph_index_for_offset(chapter.content, entry.source_start)
        by_paragraph.setdefault(index, []).append(entry)

    parts: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        if paragraph:
            parts.append(f"<p>{escape_text(paragraph)}</p>")
        else:
            parts.append('<p class="export-empty">&nbsp;</p>')
        for entry in by_paragraph.get(index, []):
            parts.append(render_anchor_figure(entry, asset_src_resolver))
    return "\n".join(parts)


_CHAPTER_STYLE = (
    ".export-illustration{margin:2rem auto;text-align:center;}"
    ".export-illustration img{max-width:100%;height:auto;border-radius:8px;}"
    ".export-caption{display:block;margin-top:.5rem;font-weight:600;}"
    ".export-citation{display:block;color:#666;font-size:.85em;}"
    ".export-missing,.export-stale,.export-invalid{"
    "border:1px dashed #c7a252;background:#fdf6e3;border-radius:8px;"
    "padding:1rem;text-align:center;}"
    ".export-invalid{border-color:#c25a5a;background:#fdeaea;}"
)


def render_chapter_xhtml(
    chapter: ExportChapter,
    asset_src_resolver: Callable[[ExportAssetRef], str | None],
) -> bytes:
    """Render one frozen chapter as a complete XHTML 1.0 document.

    EPUB3 requires XHTML chapters; the HTML single document reuses this body
    generator (not the full wrapper) so chapter markup stays in parity.
    """
    body = render_chapter_body(chapter, asset_src_resolver)
    title = escape_text(chapter.title or f"第 {chapter.chapter_number} 章")
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
        "<head><title>"
        f"{title}"
        '</title><meta charset="utf-8"/><style>'
        f"{_CHAPTER_STYLE}"
        "</style></head><body>"
        f'<h2 data-chapter-number="{chapter.chapter_number}">{title}</h2>'
        f"{body}"
        "</body></html>\n"
    ).encode("utf-8")
    return document


# ---------------------------------------------------------------------------
# Self-contained HTML export (data-URI assets + explicit report)
# ---------------------------------------------------------------------------


def build_html_export(frozen: FrozenExport) -> bytes:
    """Build a deterministic, self-contained HTML document from the frozen export.

    Approved asset bytes are embedded as base64 data URIs (never an invented
    URL); missing assets appear as explicit placeholders in the body and as a
    machine-readable export report section at the end (D-34-04).
    """
    manifest = frozen.manifest
    reader = frozen.asset_reader()

    def data_uri(asset: ExportAssetRef) -> str | None:
        payload = reader(asset)
        if payload is None:
            return None
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{asset.mime_type};base64,{encoded}"

    title = escape_text(manifest.novel_title)
    sections: list[str] = []
    for chapter in manifest.chapters:
        body = render_chapter_body(chapter, data_uri)
        chapter_title = escape_text(chapter.title or f"第 {chapter.chapter_number} 章")
        sections.append(
            f'<section data-chapter-number="{chapter.chapter_number}">'
            f"<h2>{chapter_title}</h2>\n{body}\n</section>"
        )

    report_items = ""
    if manifest.missing_assets:
        report_items = "<ul>" + "".join(
            f"<li>asset_revision_id={record.asset_revision_id} "
            f"bytes_hash={record.bytes_hash} "
            f"({escape_text(record.reason_code)}): {escape_text(record.detail)}</li>"
            for record in manifest.missing_assets
        ) + "</ul>"
    else:
        report_items = "<p>无缺失资产</p>"

    document = (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head><meta charset=\"utf-8\"/>"
        "<title>"
        f"{title}"
        "</title><style>"
        f"{_CHAPTER_STYLE}"
        "body{max-width:48rem;margin:0 auto;padding:2rem;line-height:1.7;}"
        "</style></head><body>"
        "<h1>"
        f"{title}"
        "</h1>"
        "<!-- NovelMind export manifest: "
        f"{manifest.manifest_hash} owner_id={manifest.owner_id} "
        f"novel_id={manifest.novel_id} text_version_hash={manifest.text_version_hash} -->"
        + "\n".join(sections)
        + '<section class="export-report"><h2>导出报告</h2>'
        + report_items
        + "</section></body></html>\n"
    ).encode("utf-8")
    return document


__all__ = [
    "asset_filename",
    "build_html_export",
    "escape_text",
    "render_anchor_figure",
    "render_chapter_body",
    "render_chapter_xhtml",
]
