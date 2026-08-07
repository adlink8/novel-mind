"""Phase 39-01 derivative export serializer unit tests (D-39-01/D-39-02).

Pure, database-free coverage of the deterministic export adapters:

- the frozen snapshot hash replays from its canonical payload and ordering is
  deterministic (same snapshot -> same hash, same bytes);
- Markdown/EPUB3 both consume the same frozen snapshot: chapter order, content,
  asset figures, the citation package and the version manifest are aligned;
- repeated export is byte-identical and output never carries CRLF whitespace;
- EPUB3 has the fixed stdlib-only layout (mimetype first/uncompressed,
  container, OPF manifest/spine, nav, NCX, citations, embedded manifest JSON,
  content-hash asset entries) and never embeds an unapproved/missing binary;
- a missing binary is an explicit placeholder in both formats — never an
  invented URL or a silent drop;
- bounded package size fails closed (T-39-01-02).
"""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

import pytest

from app.services.derivative_export import epub as epub_module
from app.services.derivative_export.epub import render_epub
from app.services.derivative_export.manifest import (
    derivative_export_manifest_hash,
)
from app.services.derivative_export.markdown import (
    asset_filename,
    render_markdown,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    export_snapshot_hash,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    CITATION_KEYS,
    build_fixture_snapshot,
    fixture_asset,
    fixture_chapter,
    fixture_export_asset,
    fixture_export_revision,
    seal_fixture_manifest,
)

pytestmark = pytest.mark.unit

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()


class _FakeReader:
    """Serves the tiny PNG only for assets whose content hash matches it."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def __call__(self, asset):
        if not self.available:
            return None
        if asset.content_hash != TINY_PNG_HASH:
            return None
        return TINY_PNG


def _snapshot_with_asset(asset=None, *, missing: bool = False) -> object:
    from app.services.derivative_export.manifest import MissingDerivativeAssetRecord

    from tests.fixtures.derivative_export_roundtrip_fixtures import (
        fixture_export_asset,
    )

    asset = asset or fixture_export_asset(
        fixture_asset(content_hash=TINY_PNG_HASH, size_bytes=len(TINY_PNG))
    )
    snapshot = build_fixture_snapshot(assets=(asset,))
    if missing:
        record = MissingDerivativeAssetRecord(
            asset_id=asset.asset_id,
            content_hash=asset.content_hash,
            mime_type=asset.mime_type,
            chapter_number=asset.chapter_number,
            reason_code="asset_bytes_missing",
            detail="bytes missing in scope",
        )
        snapshot = build_fixture_snapshot(assets=(), missing_assets=(record,))
    return snapshot


# ---------------------------------------------------------------------------
# Snapshot / manifest hash (reproducibility)
# ---------------------------------------------------------------------------


def test_snapshot_hash_replays_and_is_deterministic():
    a = build_fixture_snapshot()
    b = build_fixture_snapshot()
    assert len(a.snapshot_hash) == 64
    assert export_snapshot_hash(a) == a.snapshot_hash
    assert a.snapshot_hash == b.snapshot_hash

    manifest = seal_fixture_manifest(a)
    assert derivative_export_manifest_hash(manifest) == manifest.manifest_hash
    # The manifest hash equals the snapshot hash (one version for all).
    assert manifest.manifest_hash == a.snapshot_hash


def test_snapshot_hash_changes_when_content_changes():
    chapter_a = fixture_chapter(content="同一句正文。")
    chapter_b = fixture_chapter(content="不同的正文。")
    assert build_fixture_snapshot(chapters=(chapter_a,)).snapshot_hash != (
        build_fixture_snapshot(chapters=(chapter_b,)).snapshot_hash
    )


def test_seal_snapshot_requires_explicit_scope():
    with pytest.raises(ExportSnapshotError) as exc:
        from app.services.derivative_export.snapshot import _require_scope

        _require_scope(owner_id=0, novel_id=1, project_id=2)
    assert exc.value.code == "invalid_scope"


# ---------------------------------------------------------------------------
# Markdown / EPUB parity on the same frozen snapshot
# ---------------------------------------------------------------------------


def test_markdown_and_epub_share_one_snapshot():
    snapshot = _snapshot_with_asset()
    md = render_markdown(snapshot, _FakeReader()).decode("utf-8")
    epub = render_epub(snapshot, _FakeReader())

    # Version manifest / chapter order / content / asset / citation parity.
    assert snapshot.project_name in md
    assert snapshot.snapshot_hash in md
    assert snapshot.text_version_hash in md
    assert "阿宁在竹林入口站定" in md
    assert f"assets/{asset_filename(snapshot.assets[0])}" in md
    assert CITATION_KEYS[0] in md
    assert "无缺失资产" in md

    with ZipFile(BytesIO(epub)) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.getinfo("mimetype").compress_type == ZIP_STORED
        container = archive.read("META-INF/container.xml").decode("utf-8")
        assert 'full-path="OEBPS/content.opf"' in container
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'version="3.0"' in opf
        assert 'id="chapter-1"' in opf
        assert 'id="nav"' in opf
        assert 'id="ncx"' in opf
        assert 'id="citations"' in opf
        assert 'id="export-manifest"' in opf
        assert '<itemref idref="chapter-1"/>' in opf
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
        assert "阿宁在竹林入口站定" in chapter
        embedded_manifest = archive.read("OEBPS/export-manifest.json").decode("utf-8")
        assert snapshot.snapshot_hash in embedded_manifest
        assert snapshot.text_version_hash in embedded_manifest
        # Chapter order/content/version manifest embedded identically.
        citations_doc = archive.read("OEBPS/citations.xhtml").decode("utf-8")
        assert CITATION_KEYS[0] in citations_doc
        image = archive.read(f"OEBPS/assets/{asset_filename(snapshot.assets[0])}")
        assert hashlib.sha256(image).hexdigest() == TINY_PNG_HASH


def test_repeated_export_is_byte_identical_and_lf_only():
    snapshot = _snapshot_with_asset()
    md_a = render_markdown(snapshot, _FakeReader())
    md_b = render_markdown(snapshot, _FakeReader())
    assert md_a == md_b
    assert b"\r" not in md_a

    epub_a = render_epub(snapshot, _FakeReader())
    epub_b = render_epub(snapshot, _FakeReader())
    assert epub_a == epub_b


def test_chapter_order_is_frozen_position_order():
    ch1 = fixture_chapter(chapter_id=1, position=0, title="第一章", content="正文一。")
    ch2 = fixture_chapter(chapter_id=2, position=1, title="第二章", content="正文二。")
    snapshot = build_fixture_snapshot(
        chapters=(ch1, ch2),
        revisions=(
            fixture_export_revision(chapter_id=1, chapter_number=1),
            fixture_export_revision(
                revision_id=502, version_id=1, chapter_id=2, chapter_number=2
            ),
        ),
        assets=(),
        citations=(),
    )
    md = render_markdown(snapshot, _FakeReader()).decode("utf-8")
    assert md.index("## 第一章") < md.index("## 第二章")
    assert md.index("正文一。") < md.index("正文二。")

    epub = render_epub(snapshot, _FakeReader())
    with ZipFile(BytesIO(epub)) as archive:
        spine = archive.read("OEBPS/content.opf").decode("utf-8")
        assert spine.index('<itemref idref="chapter-1"/>') < spine.index(
            '<itemref idref="chapter-2"/>'
        )
        assert archive.read("OEBPS/chapter-1.xhtml") is not None
        assert archive.read("OEBPS/chapter-2.xhtml") is not None


def test_ordered_assets_follow_snapshot_order():
    asset_1 = fixture_export_asset(
        fixture_asset(
            asset_id="dv-a",
            asset_key="a",
            content_hash=TINY_PNG_HASH,
            size_bytes=len(TINY_PNG),
        )
    )
    asset_2 = fixture_export_asset(
        fixture_asset(
            asset_id="dv-b",
            asset_key="b",
            content_hash=hashlib.sha256(b"other-bytes").hexdigest(),
            size_bytes=11,
        )
    )
    snapshot = build_fixture_snapshot(
        assets=(asset_1, asset_2),
        revisions=(fixture_export_revision(asset_hashes=(asset_1.content_hash,)),),
        citations=(),
    )
    md = render_markdown(snapshot, _FakeReader()).decode("utf-8")
    assert md.index("asset_id=dv-a") < md.index("asset_id=dv-b")


# ---------------------------------------------------------------------------
# Missing asset (explicit, never silent)
# ---------------------------------------------------------------------------


def test_missing_binary_is_explicit_in_markdown_and_epub():
    snapshot = _snapshot_with_asset(missing=True)
    md = render_markdown(snapshot, _FakeReader(available=False)).decode("utf-8")
    assert "插图缺失" in md
    assert "asset_bytes_missing" in md

    epub = render_epub(snapshot, _FakeReader(available=False))
    with ZipFile(BytesIO(epub)) as archive:
        assert not any(name.startswith("OEBPS/assets/") for name in archive.namelist())
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
    assert "插图缺失" in chapter


def test_hash_drifted_bytes_are_treated_as_missing():
    # The asset declares a content hash; the reader returns different bytes.
    snapshot = _snapshot_with_asset()

    def reader(_asset):
        return b"tampered-bytes"

    md = render_markdown(snapshot, reader).decode("utf-8")
    assert "插图缺失" in md
    assert "assets/" not in md


# ---------------------------------------------------------------------------
# EPUB hardening (stdlib-only, bounded sizes, fixed layout)
# ---------------------------------------------------------------------------


def test_epub_module_imports_only_stdlib_and_app():
    """T-39-01-SC: the EPUB serializer must not import any third-party package."""
    import ast

    source = open(epub_module.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    third_party = [
        name
        for name in imports
        if name
        and not name.startswith("app.")
        and name
        not in {"zipfile", "json", "html", "io", "typing", "__future__", "hashlib"}
    ]
    assert third_party == [], f"EPUB module must be stdlib-only; found {third_party}"


def test_epub_bounded_total_size_fails_closed(monkeypatch):
    snapshot = _snapshot_with_asset()
    monkeypatch.setattr(epub_module, "MAX_EPUB_TOTAL_BYTES", 10)
    with pytest.raises(ExportSnapshotError) as exc:
        render_epub(snapshot, _FakeReader())
    assert exc.value.code == "epub_too_large"


def test_epub_fixed_timestamps():
    snapshot = _snapshot_with_asset()
    epub = render_epub(snapshot, _FakeReader())
    with ZipFile(BytesIO(epub)) as archive:
        info = archive.getinfo("OEBPS/content.opf")
        assert info.date_time == (1980, 1, 1, 0, 0, 0)


def test_epub_no_raw_asset_path_in_entries():
    # T-39-01-02: entry names derive from content hashes only — a malicious
    # asset_id must never reach the zip.
    snapshot = _snapshot_with_asset()
    epub = render_epub(snapshot, _FakeReader())
    with ZipFile(BytesIO(epub)) as archive:
        names = set(archive.namelist())
    # No entry outside the allowlisted package layout.
    allowed_prefixes = ("mimetype", "META-INF/", "OEBPS/")
    assert all(name.startswith(allowed_prefixes) for name in names)
    # Every asset entry is exactly {content_hash}{ext} (64 hex + extension).
    for name in names:
        if not name.startswith("OEBPS/assets/"):
            continue
        stem = name[len("OEBPS/assets/") :]
        assert (
            len(stem) == 68
            and stem[:64].isalnum()
            and all(ch in "0123456789abcdef" for ch in stem[:64])
        )


def test_markdown_and_epub_escape_content():
    snapshot = build_fixture_snapshot(
        chapters=(fixture_chapter(content="<script>alert(1)</script> & 正文"),),
        revisions=(),
        assets=(),
        citations=(),
    )
    md = render_markdown(snapshot, _FakeReader()).decode("utf-8")
    epub = render_epub(snapshot, _FakeReader())
    with ZipFile(BytesIO(epub)) as archive:
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
    # Markdown keeps raw content (text), EPUB escapes it for XML safety.
    assert "<script>" in md
    assert "<script>" not in chapter
    assert "&lt;script&gt;" in chapter
