"""Phase 34-04 export adapter unit tests (REQ-VIS-05, D-34-04).

Pure, database-free coverage of the deterministic export adapters:
- the frozen manifest hash replays from its canonical payload and ordering is
  deterministic;
- the read-side anchor gate classifies render / stale / asset_missing /
  invalid with stable reason codes (never relocated);
- Markdown/HTML figures present every status explicitly and never invent a URL;
- EPUB3 packaging has the fixed layout (mimetype, container, OPF manifest/spine)
  and a missing binary at embed time is a graceful placeholder, not a drop.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_STORED, ZipFile

import pytest

from app.models.illustration_anchor import IllustrationAnchor
from app.services.export.epub import build_epub
from app.services.export.html import (
    asset_filename,
    build_html_export,
    render_anchor_figure,
    render_chapter_body,
    render_chapter_xhtml,
)
from app.services.export.manifest import (
    ExportAnchorEntry,
    ExportAnchorStatus,
    ExportAssetRef,
    ExportChapter,
    ExportManifestService,
    FrozenExport,
    MissingAssetRecord,
    NovelExportManifest,
    novel_export_manifest_hash,
)
from app.services.export.markdown import build_markdown

pytestmark = pytest.mark.unit

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()

CHAPTER_TEXT = (
    "Arin crossed the rain-soaked courtyard. The lanterns flickered in the "
    "wind, casting long shadows over the cobblestones."
)
START = CHAPTER_TEXT.index("The lanterns")
END = len(CHAPTER_TEXT)
EXCERPT = CHAPTER_TEXT[START:END]
CONTENT_HASH = hashlib.sha256(CHAPTER_TEXT.encode("utf-8")).hexdigest()
ANCHOR_HASH = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()


def _asset() -> ExportAssetRef:
    return ExportAssetRef(
        asset_revision_id=1,
        asset_id="asset-1",
        bytes_hash=TINY_PNG_HASH,
        mime_type="image/png",
        cutoff_chapter=8,
    )


def _entry(
    status: ExportAnchorStatus = ExportAnchorStatus.RENDER,
    *,
    asset: ExportAssetRef | None = None,
    reason_code: str | None = None,
    detail: str | None = None,
    content_hash: str = CONTENT_HASH,
    anchor_hash: str = ANCHOR_HASH,
) -> ExportAnchorEntry:
    return ExportAnchorEntry(
        anchor_id=1,
        anchor_key="anchor-1",
        chapter_id=10,
        chapter_number=4,
        source_start=START,
        source_end=END,
        paragraph_start=2,
        paragraph_end=2,
        excerpt=EXCERPT,
        anchor_hash=anchor_hash,
        chapter_content_hash=content_hash,
        source_snapshot_id="ss-1",
        source_snapshot_hash="4" * 64,
        caption="The lanterns flickered in the wind",
        alt_text="Illustration of flickering lanterns",
        citation="Chapter 4",
        status=status,
        reason_code=reason_code,
        detail=detail,
        asset=asset,
    )


def _chapter(entry: ExportAnchorEntry | None = None) -> ExportChapter:
    return ExportChapter(
        chapter_id=10,
        chapter_number=4,
        title="The Lantern Courtyard",
        content=CHAPTER_TEXT,
        content_hash=CONTENT_HASH,
        anchors=(entry,) if entry is not None else (),
    )


def _manifest(*, entry: ExportAnchorEntry | None = None) -> NovelExportManifest:
    asset = entry.asset if entry is not None else None
    manifest = NovelExportManifest(
        owner_id=1,
        novel_id=2,
        novel_title="The Lantern Novel",
        novel_author="author",
        text_version_hash="1" * 64,
        chapters=(_chapter(entry),),
        assets=(asset,) if asset is not None else (),
        missing_assets=(),
        manifest_hash="0" * 64,
    )
    return manifest.model_copy(
        update={"manifest_hash": novel_export_manifest_hash(manifest)}
    )


def _in_memory_anchor(
    *,
    status: str = "valid",
    excerpt: str = EXCERPT,
    anchor_hash: str = ANCHOR_HASH,
    content_hash: str = CONTENT_HASH,
    start: int = START,
    end: int = END,
) -> IllustrationAnchor:
    return IllustrationAnchor(
        owner_id=1,
        novel_id=2,
        chapter_id=10,
        chapter_number=4,
        anchor_key="anchor-1",
        proposal_id=1,
        source_snapshot_id="ss-1",
        source_snapshot_hash="4" * 64,
        paragraph_start=2,
        paragraph_end=2,
        source_start=start,
        source_end=end,
        excerpt=excerpt,
        anchor_hash=anchor_hash,
        chapter_content_hash=content_hash,
        published_asset_revision_id=1,
        publish_manifest_hash="0" * 64,
        approval_request_id=1,
        status=status,
        caption="caption",
        alt_text="alt",
        citation="Chapter 4",
        approved_by="owner",
        approved_at=datetime.now(timezone.utc),
        canonical_payload={},
        canonical_payload_hash="0" * 64,
        idempotency_key="0" * 64,
        projection_hash="0" * 64,
        schema_version="illustration-anchor.v1",
    )


# ---------------------------------------------------------------------------
# Manifest contract (pure)
# ---------------------------------------------------------------------------


def test_manifest_hash_replays_from_payload():
    manifest = _manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset()))
    assert len(manifest.manifest_hash) == 64
    assert novel_export_manifest_hash(manifest) == manifest.manifest_hash
    # Two identical manifests freeze to the same hash.
    assert novel_export_manifest_hash(_manifest(entry=_entry(asset=_asset()))) == (
        manifest.manifest_hash
    )


def test_manifest_hash_changes_when_text_version_changes():
    a = _manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset()))
    b = _manifest(
        entry=_entry(
            status=ExportAnchorStatus.RENDER,
            asset=_asset(),
            content_hash="2" * 64,
        )
    )
    assert a.manifest_hash != b.manifest_hash


# ---------------------------------------------------------------------------
# Read-side anchor gate (pure, no DB)
# ---------------------------------------------------------------------------


def test_classify_render_when_exact_hashes_replay():
    status, reason, _ = ExportManifestService._classify_anchor(
        _in_memory_anchor(), CHAPTER_TEXT
    )
    assert status is ExportAnchorStatus.RENDER
    assert reason is None


def test_classify_stale_when_text_version_drifted():
    status, reason, _ = ExportManifestService._classify_anchor(
        _in_memory_anchor(content_hash="2" * 64), CHAPTER_TEXT
    )
    assert status is ExportAnchorStatus.STALE
    assert reason == "text_version_drift"


def test_classify_stale_when_source_range_mismatched():
    # The content hash replays the edited chapter, but the frozen span no longer
    # replays the excerpt at the stored offsets → stale, never relocated.
    edited = "A guard shouted. " + CHAPTER_TEXT
    anchor = _in_memory_anchor(
        start=0,
        end=START,
        excerpt=EXCERPT,
        content_hash=hashlib.sha256(edited.encode("utf-8")).hexdigest(),
    )
    status, reason, _ = ExportManifestService._classify_anchor(anchor, edited)
    assert status is ExportAnchorStatus.STALE
    assert reason == "source_range_mismatch"


def test_classify_invalid_when_anchor_hash_mismatches_excerpt():
    status, reason, _ = ExportManifestService._classify_anchor(
        _in_memory_anchor(anchor_hash="2" * 64), CHAPTER_TEXT
    )
    assert status is ExportAnchorStatus.INVALID
    assert reason == "anchor_hash_mismatch"


def test_classify_stale_when_db_status_needs_repair():
    status, reason, _ = ExportManifestService._classify_anchor(
        _in_memory_anchor(status="needs_repair"), CHAPTER_TEXT
    )
    assert status is ExportAnchorStatus.STALE
    assert reason == "needs_repair"


def test_classify_invalid_when_db_status_invalid():
    status, reason, _ = ExportManifestService._classify_anchor(
        _in_memory_anchor(status="invalid"), CHAPTER_TEXT
    )
    assert status is ExportAnchorStatus.INVALID
    assert reason == "invalid_status"


# ---------------------------------------------------------------------------
# Markdown / HTML figure adapters (pure)
# ---------------------------------------------------------------------------


def test_markdown_render_and_explicit_missing():
    frozen = FrozenExport(_manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset())))
    md = build_markdown(frozen).decode("utf-8")
    assert "# The Lantern Novel" in md
    assert "The lanterns flickered in the wind" in md
    assert "引用：Chapter 4" in md
    assert f"assets/{asset_filename(_asset())}" in md
    assert "无缺失资产" in md

    missing = _manifest(
        entry=_entry(
            status=ExportAnchorStatus.ASSET_MISSING,
            reason_code="asset_bytes_missing",
            detail="bytes missing",
        )
    )
    md_missing = build_markdown(FrozenExport(missing)).decode("utf-8")
    assert "插图缺失" in md_missing
    assert "asset_bytes_missing" in md_missing


def test_html_figure_explicit_stale_invalid_and_report():
    stale = _manifest(
        entry=_entry(
            status=ExportAnchorStatus.STALE,
            reason_code="text_version_drift",
            detail="text changed",
        )
    )
    html = build_html_export(FrozenExport(stale)).decode("utf-8")
    assert 'data-anchor-status="stale"' in html
    assert "插图待修复" in html
    assert 'data-reason="text_version_drift"' in html

    invalid = _manifest(
        entry=_entry(
            status=ExportAnchorStatus.INVALID,
            reason_code="anchor_hash_mismatch",
            detail="hash drift",
        )
    )
    html_invalid = build_html_export(FrozenExport(invalid)).decode("utf-8")
    assert 'data-anchor-status="invalid"' in html_invalid
    assert "插图已失效" in html_invalid


def test_html_embeds_asset_bytes_as_data_uri():
    frozen = FrozenExport(
        _manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset())),
        storage_keys={1: "assets/1/2/x.png"},
        storage=_FakeStorage(),
    )
    html = build_html_export(frozen).decode("utf-8")
    assert "data:image/png;base64," in html
    # No invented external URL anywhere in the document.
    assert "http://" not in html and "https://" not in html


def test_html_placeholder_when_bytes_missing_at_embed_time():
    # Status render + manifest asset present, but storage has no bytes → the
    # figure degrades to an explicit missing placeholder (never a broken URL).
    frozen = FrozenExport(
        _manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset())),
        storage_keys={},
        storage=None,
    )
    html = build_html_export(frozen).decode("utf-8")
    assert "插图缺失" in html


# ---------------------------------------------------------------------------
# EPUB3 packaging (pure, fixed layout)
# ---------------------------------------------------------------------------


def test_epub_fixed_package_layout_and_chapter_parity():
    frozen = FrozenExport(
        _manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset())),
        storage_keys={1: "assets/1/2/x.png"},
        storage=_FakeStorage(),
    )
    epub = build_epub(frozen)
    with ZipFile(BytesIO(epub)) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.getinfo("mimetype").compress_type == ZIP_STORED
        container = archive.read("META-INF/container.xml").decode("utf-8")
        assert 'full-path="OEBPS/content.opf"' in container
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'version="3.0"' in opf
        assert 'id="chapter-1"' in opf
        assert 'id="img-' in opf
        assert '<itemref idref="chapter-1"/>' in opf
        # Image bytes are content-hash addressable.
        image = archive.read(f"OEBPS/assets/{asset_filename(_asset())}")
        assert hashlib.sha256(image).hexdigest() == TINY_PNG_HASH
        # EPUB chapter body equals the shared HTML adapter body.
        expected = render_chapter_xhtml(
            frozen.manifest.chapters[0], lambda asset: f"assets/{asset_filename(asset)}"
        )
        assert archive.read("OEBPS/chapter-1.xhtml") == expected


def test_epub_missing_binary_is_explicit_not_dropped():
    # Manifest assets reference bytes that storage cannot serve → the chapter
    # body carries an explicit placeholder and no image item in the OPF.
    frozen = FrozenExport(
        _manifest(entry=_entry(status=ExportAnchorStatus.RENDER, asset=_asset())),
        storage_keys={},
        storage=None,
    )
    epub = build_epub(frozen)
    with ZipFile(BytesIO(epub)) as archive:
        assert not any(name.startswith("OEBPS/assets/") for name in archive.namelist())
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
    assert "插图缺失" in chapter


def test_render_anchor_figure_no_inner_html_injection():
    entry = _entry(
        status=ExportAnchorStatus.RENDER,
        asset=_asset(),
    ).model_copy(
        update={
            "caption": "<script>alert(1)</script>",
            "alt_text": "\"><img onerror=alert(2)>",
        }
    )
    html = render_anchor_figure(entry, lambda asset: "assets/x.png")
    # No raw injection: the payload is escaped text, never a real element.
    assert "<script>" not in html
    assert "<img onerror" not in html
    assert "&lt;img onerror" in html
    # Exactly one real <img> tag whose alt carries only escaped text.
    assert html.count("<img") == 1
    assert 'alt="&quot;&gt;&lt;img onerror=alert(2)&gt;"' in html


class _FakeStorage:
    """Minimal storage stand-in returning the tiny PNG for any read."""

    def read(self, *, owner_id: int, novel_id: int, storage_key: str) -> bytes:
        return TINY_PNG

    def exists(self, *, owner_id: int, novel_id: int, storage_key: str) -> bool:
        return True
