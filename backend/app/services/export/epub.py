"""Deterministic EPUB3 packaging for the frozen novel export (Phase 34-04, REQ-VIS-05).

D-34-04: ``build_epub`` consumes **only** the frozen ``NovelExportManifest`` (via
``FrozenExport``) with fixed EPUB3 packaging built from the standard library
(zipfile/XML strings) — no new production dependency and no independent DB read.
The chapter XHTML is rendered by the shared ``html.render_chapter_xhtml`` body
generator, so HTML and EPUB3 stay in byte-level parity for the same frozen
manifest. Approved asset bytes are embedded as OEBPS/assets content-hash files;
a missing binary appears as an explicit placeholder in the chapter body and in
the OPF/export report — never an invented URL or a silent drop.
"""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from app.services.export.manifest import ExportAssetRef, FrozenExport
from app.services.export.html import asset_filename, escape_text, render_chapter_xhtml


def _zip_entry(
    name: str, content: bytes, *, stored: bool = False
) -> tuple[ZipInfo, bytes]:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_STORED if stored else ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info, content


def _opf_metadata(manifest) -> str:
    identifier = (
        f"urn:novelmind:novel:{manifest.novel_id}:manifest:{manifest.manifest_hash}"
    )
    title = escape_text(manifest.novel_title)
    return (
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="book-id">{escape_text(identifier)}</dc:identifier>'
        f"<dc:title>{title}</dc:title>"
        "<dc:language>zh-CN</dc:language>"
        f'<meta property="dcterms:modified">'
        "2026-01-01T00:00:00Z</meta>"
        "</metadata>"
    )


def _build_opf(manifest, *, chapter_items: list[str], asset_items: list[str]) -> bytes:
    """Fixed EPUB3 OPF: manifest (chapters + assets) + spine (chapters)."""
    manifest_items: list[str] = []
    for item_id, href, media_type in chapter_items:
        manifest_items.append(
            f'<item id="{item_id}" href="{escape_text(href)}" '
            f'media-type="{escape_text(media_type)}"/>'
        )
    for item_id, href, media_type in asset_items:
        manifest_items.append(
            f'<item id="{item_id}" href="{escape_text(href)}" '
            f'media-type="{escape_text(media_type)}"/>'
        )
    spine_items = "".join(
        f'<itemref idref="{item_id}"/>' for item_id, _, _ in chapter_items
    )
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="book-id">'
        f"{_opf_metadata(manifest)}"
        f"<manifest>{''.join(manifest_items)}</manifest>"
        f"<spine>{spine_items}</spine></package>\n"
    )
    return opf.encode("utf-8")


def build_epub(frozen: FrozenExport) -> bytes:
    """Build a minimal deterministic EPUB3 containing the whole frozen novel.

    Package layout:
    - ``mimetype`` (first, uncompressed)
    - ``META-INF/container.xml`` → ``OEBPS/content.opf``
    - ``OEBPS/content.opf`` — OPF manifest/spine (chapters + image assets)
    - ``OEBPS/chapter-{n}.xhtml`` — shared HTML adapter chapter body (parity)
    - ``OEBPS/assets/{bytes_hash}{ext}`` — approved asset bytes
    """
    manifest = frozen.manifest
    reader = frozen.asset_reader()

    # Read approved bytes once; a hash-drift/missing binary is already reported
    # in the manifest, the OPF omits the unreadable resource and the figure
    # degrades to an explicit placeholder (never an invented URL or silent drop).
    readable: dict[int, bytes] = {}
    for asset in manifest.assets:
        payload = reader(asset)
        if payload is not None:
            readable[asset.asset_revision_id] = payload

    def relative_resolver(asset: ExportAssetRef) -> str | None:
        if asset.asset_revision_id not in readable:
            return None
        return f"assets/{asset_filename(asset)}"

    chapter_items: list[tuple[str, str, str]] = []
    chapter_bytes: list[bytes] = []
    for index, chapter in enumerate(manifest.chapters, start=1):
        item_id = f"chapter-{index}"
        href = f"chapter-{index}.xhtml"
        chapter_items.append((item_id, href, "application/xhtml+xml"))
        chapter_bytes.append(render_chapter_xhtml(chapter, relative_resolver))

    asset_items: list[tuple[str, str, str]] = []
    asset_bytes: list[tuple[str, bytes]] = []
    for asset in manifest.assets:
        payload = readable.get(asset.asset_revision_id)
        if payload is None:
            continue
        filename = asset_filename(asset)
        item_id = f"img-{asset.bytes_hash}"
        asset_items.append((item_id, f"assets/{filename}", asset.mime_type))
        asset_bytes.append((f"OEBPS/assets/{filename}", payload))

    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>\n'
    ).encode("utf-8")
    opf = _build_opf(manifest, chapter_items=chapter_items, asset_items=asset_items)

    entries: list[tuple[ZipInfo, bytes]] = [
        _zip_entry("mimetype", b"application/epub+zip", stored=True),
        _zip_entry("META-INF/container.xml", container),
        _zip_entry("OEBPS/content.opf", opf),
    ]
    for (item_id, href, _media), content in zip(chapter_items, chapter_bytes):
        entries.append(_zip_entry(f"OEBPS/{href}", content))
    for name, content in asset_bytes:
        entries.append(_zip_entry(name, content))

    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for info, content in entries:
            archive.writestr(info, content)
    return output.getvalue()


__all__ = ["build_epub"]
