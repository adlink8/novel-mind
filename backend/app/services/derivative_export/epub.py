"""Deterministic EPUB3 packaging for the frozen derivative export (Phase 39-01).

D-39-01 / T-39-01-02 / T-39-01-SC: ``render_epub`` consumes **only** the frozen
``ExportSnapshot`` (via ``FrozenDerivativeExport``) and is built from the
**standard library only** (``zipfile`` + hand-written XHTML/OPF/NCX) — no third
party EPUB dependency. The package is byte-deterministic: fixed timestamps,
allowlisted entry names (derived from chapter indexes and content hashes, never
a client path), bounded entry/total sizes and the exact layout below:

- ``mimetype`` (first, uncompressed)
- ``META-INF/container.xml``
- ``OEBPS/content.opf`` — OPF manifest/spine (nav, chapters, citations)
- ``OEBPS/nav.xhtml`` + ``OEBPS/toc.ncx`` — EPUB3 navigation
- ``OEBPS/chapter-{i}.xhtml`` — frozen chapter bodies (same order as Markdown)
- ``OEBPS/citations.xhtml`` — the citation package
- ``OEBPS/export-manifest.json`` — the frozen manifest (one version for all)
- ``OEBPS/assets/{content_hash}{ext}`` — approved asset bytes (hash-addressed)

A missing/hash-drifted binary is an explicit placeholder and is omitted from the
OPF/archive — never an invented URL or a silent drop.
"""

from __future__ import annotations

import hashlib
import json
from html import escape
from io import BytesIO
from typing import Callable
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from app.services.derivative_export.manifest import (
    DerivativeExportAsset,
    DerivativeExportManifest,
)
from app.services.derivative_export.snapshot import ExportSnapshot, ExportSnapshotError
from app.services.derivative_export.markdown import asset_filename

# T-39-01-02 bounded sizes (fail closed on a degenerate/oversized package).
MAX_EPUB_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MiB
MAX_CHAPTER_BYTES = 5 * 1024 * 1024  # 5 MiB per frozen chapter body

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_FIXED_MODIFIED = "2026-01-01T00:00:00Z"


def _zip_entry(name: str, content: bytes, *, stored: bool = False) -> tuple[ZipInfo, bytes]:
    info = ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = ZIP_STORED if stored else ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info, content


def _escape_text(value: str) -> str:
    return escape(value, quote=True)


# ---------------------------------------------------------------------------
# Chapter XHTML (shared body shape with Markdown chapter order/content)
# ---------------------------------------------------------------------------


def _chapter_body_xhtml(
    chapter,
    assets_by_chapter: dict[int, list[DerivativeExportAsset]],
    missing_by_chapter: dict[int, list],
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> str:
    parts: list[str] = []
    for paragraph in chapter.content.split("\n"):
        if paragraph:
            parts.append(f"<p>{_escape_text(paragraph)}</p>")
        else:
            parts.append('<p class="export-empty">&nbsp;</p>')
    for asset in assets_by_chapter.get(chapter.chapter_number, []):
        parts.append(_asset_figure_xhtml(asset, asset_reader))
    for record in missing_by_chapter.get(chapter.chapter_number, []):
        parts.append(_missing_figure_xhtml(record))
    return "\n".join(parts)


def _asset_figure_xhtml(
    asset: DerivativeExportAsset,
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> str:
    payload = asset_reader(asset)
    # Defense in depth (T-39-01-02): never embed bytes that do not replay the
    # frozen content hash — a drift degrades to the explicit placeholder.
    if payload is not None and hashlib.sha256(payload).hexdigest() == asset.content_hash:
        src = f"assets/{asset_filename(asset)}"
        return (
            '<figure class="derivative-export-asset">'
            f'<img src="{src}" alt="{_escape_text(asset.asset_id)}"/>'
            "<figcaption>"
            f"asset_id={_escape_text(asset.asset_id)} "
            f"chapter={asset.chapter_number} "
            f"content_hash={asset.content_hash}"
            "</figcaption></figure>"
        )
    return (
        '<figure class="derivative-export-asset" data-reason="asset_bytes_missing">'
        '<div class="export-missing"><strong>插图缺失</strong>'
        f"<p>asset_id={_escape_text(asset.asset_id)} "
        f"content_hash={asset.content_hash}</p></div>"
        "</figure>"
    )


def _missing_figure_xhtml(record) -> str:
    return (
        '<figure class="derivative-export-asset" data-reason="asset_bytes_missing">'
        '<div class="export-missing"><strong>插图缺失</strong>'
        f"<p>asset_id={_escape_text(record.asset_id)} "
        f"content_hash={record.content_hash}</p></div>"
        "</figure>"
    )


def _chapter_xhtml(
    snapshot: ExportSnapshot,
    chapter,
    assets_by_chapter: dict[int, list[DerivativeExportAsset]],
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> bytes:
    title = _escape_text(chapter.title or f"第 {chapter.chapter_number} 章")
    missing_by_chapter: dict[int, list] = {}
    for record in snapshot.missing_assets:
        missing_by_chapter.setdefault(record.chapter_number, []).append(record)
    body = _chapter_body_xhtml(
        chapter, assets_by_chapter, missing_by_chapter, asset_reader
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
        "<head><title>"
        f"{title}"
        '</title><meta charset="utf-8"/><style>'
        ".derivative-export-asset{margin:2rem auto;text-align:center;}"
        ".derivative-export-asset img{max-width:100%;height:auto;border-radius:8px;}"
        ".export-missing{border:1px dashed #c7a252;background:#fdf6e3;"
        "border-radius:8px;padding:1rem;text-align:center;}"
        "</style></head><body>"
        f'<h2 data-chapter-number="{chapter.chapter_number}">{title}</h2>'
        f"{body}"
        "</body></html>\n"
    ).encode("utf-8")
    if len(document) > MAX_CHAPTER_BYTES:
        raise ExportSnapshotError(
            "chapter_too_large",
            f"chapter {chapter.chapter_number} exceeds the {MAX_CHAPTER_BYTES} "
            "byte EPUB body limit",
        )
    return document


# ---------------------------------------------------------------------------
# OPF / NCX / nav / citations / manifest (fixed, deterministic)
# ---------------------------------------------------------------------------


def _opf_metadata(manifest: DerivativeExportManifest) -> str:
    identifier = (
        f"urn:novelmind:derivative:{manifest.project_id}:"
        f"manifest:{manifest.manifest_hash}"
    )
    title = _escape_text(manifest.project_name)
    return (
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="book-id">{_escape_text(identifier)}</dc:identifier>'
        f"<dc:title>{title}</dc:title>"
        "<dc:language>zh-CN</dc:language>"
        f'<meta property="dcterms:modified">{_FIXED_MODIFIED}</meta>'
        "</metadata>"
    )


def _build_opf(
    manifest: DerivativeExportManifest,
    *,
    chapter_items: list[tuple[str, str, str]],
    asset_items: list[tuple[str, str, str]],
    has_nav: bool,
    has_citations: bool,
) -> bytes:
    items: list[str] = []
    if has_nav:
        items.append(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>'
        )
        items.append(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        )
    for item_id, href, media_type in chapter_items:
        items.append(
            f'<item id="{item_id}" href="{_escape_text(href)}" '
            f'media-type="{_escape_text(media_type)}"/>'
        )
    if has_citations:
        items.append(
            '<item id="citations" href="citations.xhtml" '
            'media-type="application/xhtml+xml"/>'
        )
        items.append(
            '<item id="export-manifest" href="export-manifest.json" '
            'media-type="application/json"/>'
        )
    for item_id, href, media_type in asset_items:
        items.append(
            f'<item id="{item_id}" href="{_escape_text(href)}" '
            f'media-type="{_escape_text(media_type)}"/>'
        )
    spine_items = ""
    if has_nav:
        spine_items += '<itemref idref="nav"/>'
    spine_items += "".join(
        f'<itemref idref="{item_id}"/>' for item_id, _, _ in chapter_items
    )
    if has_citations:
        spine_items += '<itemref idref="citations"/>'
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="book-id">'
        f"{_opf_metadata(manifest)}"
        f"<manifest>{''.join(items)}</manifest>"
        f"<spine>{spine_items}</spine></package>\n"
    )
    return opf.encode("utf-8")


def _build_nav(
    manifest: DerivativeExportManifest, chapter_items: list[tuple[int, str]]
) -> bytes:
    title = _escape_text(manifest.project_name)
    list_items = "".join(
        f'<li><a href="chapter-{index}.xhtml">'
        f"{_escape_text(label)}</a></li>"
        for index, label in chapter_items
    )
    if manifest.citations:
        list_items += '<li><a href="citations.xhtml">引用</a></li>'
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
        "<head><title>"
        f"{title}"
        "</title></head><body><nav epub:type=\"toc\">"
        f"<h1>{title}</h1><ol>{list_items}</ol></nav></body></html>\n"
    ).encode("utf-8")
    return document


def _build_ncx(
    manifest: DerivativeExportManifest, chapter_items: list[tuple[int, str]]
) -> bytes:
    points = "".join(
        (
            f'<navPoint id="chapter-{index}" playOrder="{index}">'
            f'<navLabel><text>{_escape_text(label)}</text></navLabel>'
            f'<content src="chapter-{index}.xhtml"/>'
            "</navPoint>"
        )
        for index, label in chapter_items
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f"<head><meta name=\"dtb:uid\" content=\"{manifest.manifest_hash}\"/>"
        "<meta name=\"dtb:depth\" content=\"1\"/>"
        f"<meta name=\"dtb:totalPageCount\" content=\"0\"/>"
        f"<meta name=\"dtb:maxPageNumber\" content=\"0\"/></head>"
        f"<docTitle><text>{_escape_text(manifest.project_name)}</text></docTitle>"
        f"<navMap>{points}</navMap></ncx>\n"
    ).encode("utf-8")
    return document


def _build_citations(manifest: DerivativeExportManifest) -> bytes:
    if manifest.citations:
        items = "".join(
            f"<li><code>{_escape_text(c.citation_key)}</code> "
            f"citation_hash={c.citation_hash} "
            f"source_snapshot={c.source_snapshot} "
            f"revision_id={c.revision_id} chapter={c.chapter_number}</li>"
            for c in manifest.citations
        )
    else:
        items = "<li>无引用</li>"
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
        "<head><title>引用</title></head><body>"
        f"<h1>引用</h1><ul>{items}</ul></body></html>\n"
    ).encode("utf-8")
    return document


def _manifest_json(manifest: DerivativeExportManifest) -> bytes:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload.encode("utf-8")


# ---------------------------------------------------------------------------
# Public serializer
# ---------------------------------------------------------------------------


def render_epub(
    snapshot: ExportSnapshot,
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> bytes:
    """Build a deterministic minimal EPUB3 from the frozen snapshot."""
    from app.services.derivative_export.manifest import seal_derivative_export_manifest

    manifest = seal_derivative_export_manifest(snapshot)

    # Read approved bytes once; missing/hash-drifted bytes degrade to placeholders.
    readable: dict[str, bytes] = {}
    for asset in snapshot.assets:
        payload = asset_reader(asset)
        if payload is not None:
            readable[asset.asset_id] = payload

    assets_by_chapter: dict[int, list[DerivativeExportAsset]] = {}
    for asset in snapshot.assets:
        assets_by_chapter.setdefault(asset.chapter_number, []).append(asset)

    chapter_items: list[tuple[str, str, str]] = []
    chapter_bytes: list[bytes] = []
    nav_items: list[tuple[int, str]] = []
    for index, chapter in enumerate(snapshot.chapters, start=1):
        item_id = f"chapter-{index}"
        href = f"chapter-{index}.xhtml"
        chapter_items.append((item_id, href, "application/xhtml+xml"))
        chapter_bytes.append(
            _chapter_xhtml(snapshot, chapter, assets_by_chapter, asset_reader)
        )
        nav_items.append(
            (index, chapter.title or f"第 {chapter.chapter_number} 章")
        )

    asset_items: list[tuple[str, str, str]] = []
    asset_bytes: list[tuple[str, bytes]] = []
    for asset in snapshot.assets:
        payload = readable.get(asset.asset_id)
        if payload is None:
            continue
        filename = asset_filename(asset)
        item_id = f"img-{asset.content_hash}"
        asset_items.append((item_id, f"assets/{filename}", asset.mime_type))
        asset_bytes.append((f"OEBPS/assets/{filename}", payload))

    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>\n'
    ).encode("utf-8")
    has_nav = True
    opf = _build_opf(
        manifest,
        chapter_items=chapter_items,
        asset_items=asset_items,
        has_nav=True,
        has_citations=True,
    )

    entries: list[tuple[ZipInfo, bytes]] = [
        _zip_entry("mimetype", b"application/epub+zip", stored=True),
        _zip_entry("META-INF/container.xml", container),
        _zip_entry("OEBPS/content.opf", opf),
        _zip_entry("OEBPS/nav.xhtml", _build_nav(manifest, nav_items)),
        _zip_entry("OEBPS/toc.ncx", _build_ncx(manifest, nav_items)),
    ]
    for (item_id, href, _media), content in zip(chapter_items, chapter_bytes):
        entries.append(_zip_entry(f"OEBPS/{href}", content))
    entries.append(_zip_entry("OEBPS/citations.xhtml", _build_citations(manifest)))
    entries.append(_zip_entry("OEBPS/export-manifest.json", _manifest_json(manifest)))
    for name, content in asset_bytes:
        entries.append(_zip_entry(name, content))

    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for info, content in entries:
            archive.writestr(info, content)
    payload = output.getvalue()
    if len(payload) > MAX_EPUB_TOTAL_BYTES:
        raise ExportSnapshotError(
            "epub_too_large",
            f"EPUB package exceeds the {MAX_EPUB_TOTAL_BYTES} byte limit",
        )
    return payload


__all__ = [
    "MAX_EPUB_TOTAL_BYTES",
    "render_epub",
]
