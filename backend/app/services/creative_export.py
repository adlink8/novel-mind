"""Deterministic, read-only Markdown and EPUB export for creative revisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from app.models.fanfiction import FanFiction
from app.models.fanfiction_revision import FanFictionRevision


@dataclass(frozen=True)
class ExportArtifact:
    content: bytes
    media_type: str
    extension: str


def _normalized_content(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else f"{normalized}\n"


def build_markdown(*, project: FanFiction, revision: FanFictionRevision) -> bytes:
    """Export one immutable revision with explicit provenance metadata."""

    body = _normalized_content(revision.content)
    document = (
        f"# {revision.title}\n\n"
        "<!-- NovelMind export; source content is Fanfiction Canon only.\n"
        f"project_id: {project.id}\n"
        f"novel_id: {project.novel_id}\n"
        f"revision_id: {revision.id}\n"
        f"revision_number: {revision.revision_number}\n"
        f"content_hash: {revision.content_hash}\n"
        "-->\n\n"
        f"{body}"
    )
    return document.encode("utf-8")


def _xhtml_body(content: str) -> str:
    return f'<pre class="markdown-source">{escape(_normalized_content(content))}</pre>'


def _zip_entry(
    name: str, content: bytes, *, stored: bool = False
) -> tuple[ZipInfo, bytes]:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_STORED if stored else ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info, content


def build_epub(*, project: FanFiction, revision: FanFictionRevision) -> bytes:
    """Build a minimal deterministic EPUB containing exactly one revision."""

    identifier = f"urn:novelmind:creative:{project.id}:revision:{revision.id}"
    title = escape(revision.title)
    content_xhtml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
        "<head><title>"
        f"{title}"
        '</title><meta charset="utf-8"/><style>'
        ".markdown-source{white-space:pre-wrap;font-family:serif;line-height:1.6;}"
        "</style></head><body><h1>"
        f"{title}"
        "</h1>"
        f"{_xhtml_body(revision.content)}"
        "</body></html>\n"
    ).encode("utf-8")
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>\n'
    ).encode("utf-8")
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="book-id">{escape(identifier)}</dc:identifier>'
        f"<dc:title>{title}</dc:title><dc:language>zh-CN</dc:language>"
        '</metadata><manifest><item id="chapter" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="chapter"/></spine></package>\n'
    ).encode("utf-8")

    entries = [
        _zip_entry("mimetype", b"application/epub+zip", stored=True),
        _zip_entry(
            "META-INF/container.xml",
            container,
        ),
        _zip_entry("OEBPS/chapter.xhtml", content_xhtml),
        _zip_entry("OEBPS/content.opf", opf),
    ]
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for info, content in entries:
            archive.writestr(info, content)
    return output.getvalue()


def build_export(
    *, project: FanFiction, revision: FanFictionRevision, format: str
) -> ExportArtifact:
    if format == "markdown":
        return ExportArtifact(
            build_markdown(project=project, revision=revision), "text/markdown", "md"
        )
    if format == "epub":
        return ExportArtifact(
            build_epub(project=project, revision=revision),
            "application/epub+zip",
            "epub",
        )
    raise ValueError("unsupported export format")


def export_filename(
    *, project: FanFiction, revision: FanFictionRevision, extension: str
) -> str:
    stem = (
        re.sub(r"[^\w\-]+", "-", revision.title, flags=re.UNICODE).strip("-")
        or "creative-project"
    )
    return f"{stem}-v{revision.revision_number}.{extension}"
