"""Phase 34-04 export parity integration tests (REQ-VIS-05, D-34-04).

Prove the frozen export manifest + Markdown/HTML/EPUB adapters on CI PostgreSQL:
- ``ExportManifestService.freeze`` is owner/novel-scoped, approved-only
  (candidate proposals never appear), deterministically ordered and replayable
  (identical DB state → identical manifest hash that replays its payload);
- a published ``valid`` anchor whose hash replays the current chapter renders
  the approved asset; a missing binary is an explicit ``asset_missing`` record
  and a removed binary is reported — never an invented URL or silent drop;
- a changed chapter/version marks the anchor ``stale`` (needs_repair), never
  relocated;
- Markdown/HTML/EPUB are all driven by the same frozen manifest: EPUB3 package
  has the fixed layout (uncompressed mimetype, META-INF/container.xml, OPF
  manifest/spine, content-hash image resources) and the EPUB chapter XHTML is
  byte-identical to the shared HTML adapter chapter body (parity);
- the export API routes are registered under ``/api/novels``.

Direct service-level tests (no HTTP client): the API surface is thin and already
owner-scoped by ``require_owned_novel``; the authority boundaries live here.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_STORED, ZipFile

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import Chapter, Novel, User
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import IllustrationAnchorProposal
from app.models.illustration_job import IllustrationJob
from app.services.agent_runtime.approvals import confirm
from app.services.export.epub import build_epub
from app.services.export.html import (
    asset_filename,
    build_html_export,
    render_chapter_xhtml,
)
from app.services.export.manifest import (
    ExportAnchorStatus,
    ExportManifestError,
    ExportManifestService,
    novel_export_manifest_hash,
)
from app.services.export.markdown import build_markdown
from app.services.illustration_anchors.publish import (
    create_anchor_proposal,
    publish_anchor,
)
from app.services.illustrations.storage import AssetStorage
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# One tiny valid PNG (1x1) served as the approved asset bytes.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()

CHAPTER_TEXT = (
    "Arin crossed the rain-soaked courtyard. The lanterns flickered in the "
    "wind, casting long shadows over the cobblestones."
)
_EXCERPT_START = CHAPTER_TEXT.index("The lanterns")
_EXCERPT_END = len(CHAPTER_TEXT)
EXCERPT = CHAPTER_TEXT[_EXCERPT_START:_EXCERPT_END]
CHAPTER_CONTENT_HASH = hashlib.sha256(CHAPTER_TEXT.encode("utf-8")).hexdigest()
ANCHOR_HASH = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()

EDITED_TEXT = "A guard shouted. " + CHAPTER_TEXT
EDITED_CONTENT_HASH = hashlib.sha256(EDITED_TEXT.encode("utf-8")).hexdigest()

HEX64 = "a" * 64
SNAPSHOT_HASH = "4" * 64
SCENE_SPEC_HASH = "1" * 64
PROMPT_HASH = "2" * 64
VB_HASH = "3" * 64
CONFIG_HASH = "5" * 64


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _seed(sync_url: str, storage: AssetStorage, *, suffix: str) -> dict[str, Any]:
    """Seed owner + novel + chapter + succeeded job + proposal-ready cleared asset
    whose real bytes are stored in the temp AssetStorage."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p34x_{suffix}",
            email=f"p34x_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(title=f"P34 Export Novel {suffix}", owner_id=user.id)
        session.add(novel)
        session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=4,
            title="The Lantern Courtyard",
            content=CHAPTER_TEXT,
            word_count=len(CHAPTER_TEXT),
        )
        session.add(chapter)
        session.flush()
        job = IllustrationJob(
            owner_id=user.id,
            novel_id=novel.id,
            job_key=f"job-export-{suffix}",
            idempotency_key=hashlib.sha256(f"job-{suffix}".encode("utf-8")).hexdigest(),
            status="succeeded",
            status_reason="generated",
            scene_spec_hash=SCENE_SPEC_HASH,
            prompt_revision_id=101,
            prompt_revision_hash=PROMPT_HASH,
            visual_bible_revision_id=None,
            visual_bible_revision_hash=VB_HASH,
            source_snapshot_id="ss-1",
            source_snapshot_hash=SNAPSHOT_HASH,
            cutoff_chapter=8,
            model_lineage={},
            config_hash=CONFIG_HASH,
            price_snapshot={},
            response_hash=None,
            schema_version="illustration.v1",
        )
        session.add(job)
        session.flush()
        storage_key = storage.store(
            owner_id=user.id,
            novel_id=novel.id,
            payload=TINY_PNG,
            mime_type="image/png",
            bytes_hash=TINY_PNG_HASH,
        )
        asset = AssetRevision(
            owner_id=user.id,
            novel_id=novel.id,
            job_id=job.id,
            revision_key="rev-1",
            revision_number=1,
            asset_id="asset-1",
            storage_key=storage_key,
            mime_type="image/png",
            width=1,
            height=1,
            size_bytes=len(TINY_PNG),
            bytes_hash=TINY_PNG_HASH,
            scene_spec_hash=SCENE_SPEC_HASH,
            prompt_revision_id=101,
            prompt_revision_hash=PROMPT_HASH,
            visual_bible_revision_hash=VB_HASH,
            source_snapshot_id="ss-1",
            source_snapshot_hash=SNAPSHOT_HASH,
            cutoff_chapter=8,
            model_lineage={},
            config_hash=CONFIG_HASH,
            provider="mock",
            provider_model="mock-img-v1",
            provider_request_id="req-1",
            provider_response={},
            provenance={},
            rights_status="cleared",
            approval_state="proposal_ready",
            approved_by="editor",
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=hashlib.sha256(
                f"asset-{suffix}".encode("utf-8")
            ).hexdigest(),
            projection_hash=HEX64,
            schema_version="illustration-asset.v1",
        )
        session.add(asset)
        session.flush()
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "chapter_id": chapter.id,
            "job_id": job.id,
            "asset_id": asset.id,
            "storage_key": storage_key,
        }
    engine.dispose()
    return data


def _request(ids: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    request = {
        "branch": None,
        "fork": None,
        "chapter_id": ids["chapter_id"],
        "chapter_number": 4,
        "proposal_key": f"anchor-lantern-{ids['owner_id']}",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "source_start": _EXCERPT_START,
        "source_end": _EXCERPT_END,
        "paragraph_start": 2,
        "paragraph_end": 2,
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": CHAPTER_CONTENT_HASH,
        "asset_revision_id": ids["asset_id"],
        "caption": "The lanterns flickered in the wind",
        "alt_text": "Illustration of flickering lanterns in the courtyard",
        "citation": "Chapter 4",
        "run_id": None,
        "skill_version_id": None,
        "artifact_id": None,
        "artifact_revision_id": None,
    }
    request.update(overrides)
    return request


async def _publish_valid_anchor(
    factory, sync_url: str, storage: AssetStorage, *, suffix: str
) -> dict[str, Any]:
    """Seed + propose + Web-approve + deterministic publish → valid anchor."""
    ids = _seed(sync_url, storage, suffix=suffix)
    async with factory() as session:
        result = await create_anchor_proposal(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            request=_request(ids),
            action="publish_illustration",
        )
        await session.commit()
        ids["proposal_id"] = result.proposal.id
        ids["approval_id"] = result.approval_request.id
        await confirm(
            session,
            request_id=ids["approval_id"],
            owner_id=ids["owner_id"],
            mode="once",
        )
        await session.commit()
        anchor = await publish_anchor(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            proposal_id=ids["proposal_id"],
        )
        await session.commit()
        ids["anchor_id"] = anchor.id
    return ids


async def _freeze(factory, storage: AssetStorage, *, owner_id: int, novel_id: int):
    async with factory() as session:
        return await ExportManifestService(session, storage=storage).freeze(
            owner_id=owner_id, novel_id=novel_id
        )


async def _edit_chapter(factory, *, chapter_id: int, content: str) -> None:
    async with factory() as session:
        await session.execute(
            text("UPDATE chapters SET content = :content WHERE id = :cid"),
            {"content": content, "cid": chapter_id},
        )
        await session.commit()


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture(scope="module")
def asset_storage() -> AssetStorage:
    with tempfile.TemporaryDirectory(prefix="novelmind-export-") as tmp:
        yield AssetStorage(Path(tmp))


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


# ────────────────────────── manifest contract ──────────────────────────


async def test_freeze_is_owner_scoped_and_replayable(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"fx_ok_{uuid.uuid4().hex[:6]}",
    )
    first = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    second = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    manifest = first.manifest

    # Owner/novel/version/source scope is frozen and replayable.
    assert manifest.owner_id == ids["owner_id"]
    assert manifest.novel_id == ids["novel_id"]
    assert len(manifest.manifest_hash) == 64
    assert novel_export_manifest_hash(manifest) == manifest.manifest_hash
    assert manifest.manifest_hash == second.manifest.manifest_hash

    # One chapter, deterministic ordering, exact content hash.
    assert len(manifest.chapters) == 1
    chapter = manifest.chapters[0]
    assert chapter.chapter_number == 4
    assert chapter.content == CHAPTER_TEXT
    assert chapter.content_hash == CHAPTER_CONTENT_HASH

    # Approved-only: exactly the one published valid anchor is present.
    assert len(chapter.anchors) == 1
    entry = chapter.anchors[0]
    assert entry.status is ExportAnchorStatus.RENDER
    assert entry.anchor_hash == ANCHOR_HASH
    assert entry.chapter_content_hash == CHAPTER_CONTENT_HASH
    assert entry.asset is not None
    assert entry.asset.asset_revision_id == ids["asset_id"]
    assert entry.asset.bytes_hash == TINY_PNG_HASH
    # spoiler cutoff provenance is carried
    assert entry.asset.cutoff_chapter == 8

    # Renderable asset present; nothing missing.
    assert [a.asset_revision_id for a in manifest.assets] == [ids["asset_id"]]
    assert manifest.missing_assets == ()

    # Cross-owner scope fails closed (indistinguishable from 404).
    with pytest.raises(ExportManifestError) as exc:
        await _freeze(
            runtime_factory,
            asset_storage,
            owner_id=ids["owner_id"] + 999,
            novel_id=ids["novel_id"],
        )
    assert "not found" in str(exc.value)


async def test_freeze_is_approved_only_candidates_never_appear(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"fx_app_{uuid.uuid4().hex[:6]}",
    )
    # Add a second candidate proposal that is never published (no approval).
    async with runtime_factory() as session:
        await create_anchor_proposal(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            request=_request(ids, proposal_key="anchor-unpublished"),
            action="publish_illustration",
        )
        await session.commit()
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    assert len(frozen.manifest.chapters[0].anchors) == 1
    # The proposal table carries two rows but only the published anchor exports.
    async with runtime_factory() as session:
        proposal_count = int(
            await session.scalar(
                select(func.count())
                .select_from(IllustrationAnchorProposal)
                .where(IllustrationAnchorProposal.owner_id == ids["owner_id"])
            )
            or 0
        )
    assert proposal_count == 2
    assert len(frozen.manifest.chapters[0].anchors) == 1


async def test_freeze_marks_missing_binary_asset_missing(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"fx_miss_{uuid.uuid4().hex[:6]}",
    )
    asset_storage.remove(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        storage_key=ids["storage_key"],
    )
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    manifest = frozen.manifest
    entry = manifest.chapters[0].anchors[0]
    assert entry.status is ExportAnchorStatus.ASSET_MISSING
    assert entry.reason_code == "asset_bytes_missing"
    assert manifest.assets == ()  # no embeddable asset
    assert len(manifest.missing_assets) == 1
    record = manifest.missing_assets[0]
    assert record.asset_revision_id == ids["asset_id"]
    assert record.reason_code == "asset_bytes_missing"


async def test_freeze_marks_edited_chapter_stale(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"fx_stale_{uuid.uuid4().hex[:6]}",
    )
    await _edit_chapter(
        runtime_factory, chapter_id=ids["chapter_id"], content=EDITED_TEXT
    )
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    entry = frozen.manifest.chapters[0].anchors[0]
    assert entry.status is ExportAnchorStatus.STALE
    assert entry.reason_code == "text_version_drift"
    # The frozen span is preserved — never relocated.
    assert entry.source_start == _EXCERPT_START
    assert entry.source_end == _EXCERPT_END


# ────────────────────────── Markdown / HTML / EPUB adapters ──────────────────────────


async def test_markdown_html_epub_share_one_frozen_manifest(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ad_all_{uuid.uuid4().hex[:6]}",
    )
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    manifest = frozen.manifest

    md = build_markdown(frozen).decode("utf-8")
    assert "# P34 Export Novel" in md
    assert manifest.manifest_hash in md
    assert "The lanterns flickered in the wind" in md
    assert "引用：Chapter 4" in md
    assert f"assets/{asset_filename(manifest.assets[0])}" in md
    assert "无缺失资产" in md

    html = build_html_export(frozen).decode("utf-8")
    assert manifest.manifest_hash in html
    assert 'data-anchor-status="render"' in html
    assert "data:image/png;base64," in html
    assert "The lanterns flickered in the wind" in html
    assert "引用：Chapter 4" in html

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
        assert "<spine>" in opf and '<itemref idref="chapter-1"/>' in opf
        image_name = f"OEBPS/assets/{asset_filename(manifest.assets[0])}"
        image_bytes = archive.read(image_name)
        assert hashlib.sha256(image_bytes).hexdigest() == TINY_PNG_HASH


async def test_html_epub_chapter_body_parity(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    """EPUB chapter XHTML is byte-identical to the shared HTML adapter body."""
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"parity_{uuid.uuid4().hex[:6]}",
    )
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    chapter = frozen.manifest.chapters[0]

    def relative_resolver(asset):
        return f"assets/{asset_filename(asset)}"

    expected = render_chapter_xhtml(chapter, relative_resolver)
    epub = build_epub(frozen)
    with ZipFile(BytesIO(epub)) as archive:
        actual = archive.read("OEBPS/chapter-1.xhtml")
    assert actual == expected
    assert b'data-anchor-status="render"' in actual
    assert "引用：Chapter 4".encode("utf-8") in actual


async def test_missing_asset_is_explicit_in_all_formats(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"md_miss_{uuid.uuid4().hex[:6]}",
    )
    asset_storage.remove(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        storage_key=ids["storage_key"],
    )
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )

    md = build_markdown(frozen).decode("utf-8")
    assert "插图缺失" in md
    assert f"asset_revision_id={ids['asset_id']}" in md

    html = build_html_export(frozen).decode("utf-8")
    assert "插图缺失" in html
    assert 'data-reason="asset_bytes_missing"' in html

    epub = build_epub(frozen)
    with ZipFile(BytesIO(epub)) as archive:
        assert not any(name.startswith("OEBPS/assets/") for name in archive.namelist())
        chapter = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
    assert "插图缺失" in chapter


async def test_stale_anchor_is_explicit_in_markdown(
    runtime_factory, migrated_postgres: str, asset_storage: AssetStorage
):
    ids = await _publish_valid_anchor(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"md_stale_{uuid.uuid4().hex[:6]}",
    )
    await _edit_chapter(
        runtime_factory, chapter_id=ids["chapter_id"], content=EDITED_TEXT
    )
    frozen = await _freeze(
        runtime_factory,
        asset_storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
    )
    md = build_markdown(frozen).decode("utf-8")
    assert "插图待修复" in md
    assert "text_version_drift" in md


# ────────────────────────── API registration ──────────────────────────


def test_export_api_routes_registered():
    """The export router is wired into the FastAPI app under /api/novels."""
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/novels/{novel_id}/export" in paths
    assert "/api/novels/{novel_id}/export/manifest" in paths
