"""Phase 39-01 derivative export PostgreSQL integration tests (D-39-01/D-39-02).

Covers the real CI database + API surface:

- ``build_export_snapshot`` freezes the owner/project/fork-scoped published
  revisions/assets/citations/version lineage and is byte/hash reproducible;
- ``POST .../prepare`` returns the frozen manifest whose hash replays;
- ``GET .../download?format=markdown|epub`` returns deterministic bytes that
  are identical on repeat and carry the manifest hash header;
- EPUB3 has the fixed stdlib-only layout (mimetype first, OPF manifest/spine,
  nav, NCX, citations, embedded export-manifest.json, content-hash assets);
- a removed binary is an explicit missing record in Markdown/EPUB (never a
  silent drop);
- a drifted chapter version (stale revision) fails closed as blocked;
- cross-owner access and Original/nonexistent scopes are identical 404s.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.derivative_export import set_derivative_export_asset_storage
from app.core.security import create_access_token, hash_password
from app.models.canon_fork import CanonFork
from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import (
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.models.derivative_override import DerivativeOverride
from app.models.derivative_project import DerivativeProject
from app.models.derivative_revision import DerivativeRevision
from app.models.derivative_visual import (
    DERIVATIVE_ASSET_NAMESPACE,
    DERIVATIVE_VISUAL_NAMESPACE,
    DerivativeVisualCandidateAsset,
    DerivativeVisualCandidateReviewEvent,
    DerivativeVisualVersion,
)
from app.models.novel import Novel
from app.models.user import User
from app.models.visual_bible import VisualBibleVersion
from app.services.derivative_editor.chapters import canonicalize_markdown, markdown_checksum
from app.services.derivative_export.manifest import (
    derivative_export_manifest_hash,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    ExportSnapshotService,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

PREPARE_BASE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/prepare"
)
DOWNLOAD_BASE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/download"
)

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_E = "e" * 64
HEX64_F = "f" * 64

CONTENT = "阿宁在竹林入口站定，深吸一口气。\n\n她推开了那扇竹门。"
CANONICAL = canonicalize_markdown(CONTENT)
CONTENT_HASH = markdown_checksum(CONTENT)


def _idem64() -> str:
    return uuid.uuid4().hex * 2


def async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture(scope="module")
def asset_storage() -> DerivativeAssetStorage:
    with tempfile.TemporaryDirectory(prefix="novelmind-deriv-export-") as tmp:
        yield DerivativeAssetStorage(Path(tmp))


@pytest_asyncio.fixture
async def api_client(migrated_postgres: str, asset_storage: DerivativeAssetStorage):
    aengine = create_async_engine(
        async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    set_derivative_export_asset_storage(asset_storage)
    try:
        yield factory, migrated_postgres, asset_storage
    finally:
        set_derivative_export_asset_storage(None)
        await aengine.dispose()


# ---------------------------------------------------------------------------
# ORM seeding of the full derivative chain (Fanfiction Canon only)
# ---------------------------------------------------------------------------


def _seed_chain(
    sync_url: str,
    storage: DerivativeAssetStorage,
    *,
    suffix: str,
    chapter_count: int = 2,
    with_assets: bool = True,
    asset_review_state: str = "approved",
    with_override: bool = True,
    second_revision: bool = True,
) -> dict:
    """Seed owner + novel + fork + project + chapters + revision chain + asset."""
    engine = create_engine(sync_url, poolclass=NullPool)
    data: dict = {}
    with Session(engine) as session:
        user = User(
            username=f"dex_{suffix}",
            email=f"dex_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"Deriv Export Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=chapter_count,
            word_count=len(CONTENT) * chapter_count,
        )
        session.add(novel)
        session.flush()

        fork = CanonFork(
            owner_id=user.id,
            novel_id=novel.id,
            fork_key=f"ff-dex-{suffix}",
            space="fanfiction_canon",
            status="approved",
            source_version_key="original:1",
            source_snapshot_id="snap-1",
            source_snapshot_hash=HEX64,
            through_chapter=chapter_count,
            full_book_authorized=False,
            cutoff_snapshot_hash=HEX64_C,
            scope_hash=HEX64,
            manifest_hash=HEX64_B,
            citation_lineage=[],
            authorization={},
            active=False,
        )
        session.add(fork)
        session.flush()

        project = DerivativeProject(
            owner_id=user.id,
            novel_id=novel.id,
            fork_id=fork.id,
            project_key=f"proj-{suffix}",
            name=f"Deriv Project {suffix}",
            status="active",
            space="fanfiction_canon",
            fork_key=fork.fork_key,
            source_version_key="original:1",
            source_snapshot_hash=HEX64,
            through_chapter=chapter_count,
            full_book_authorized=False,
            cutoff_snapshot_hash=HEX64_C,
            scope_hash=HEX64,
            manifest_hash=HEX64_B,
        )
        session.add(project)
        session.flush()

        original_vb = VisualBibleVersion(
            owner_id=user.id,
            novel_id=novel.id,
            version_key=f"vb-original-{suffix}",
            revision_number=1,
            source_snapshot_id="snap-1",
            source_snapshot_hash=HEX64,
            cutoff_chapter=8,
            review_state="candidate",
            schema_version="visual-bible.v1",
            schema_hash=HEX64,
            policy_hash=HEX64_B,
            manifest_hash=HEX64_C,
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=_idem64(),
            projection_hash=HEX64,
        )
        session.add(original_vb)
        session.flush()

        # Context package -> generation job -> candidate -> approved override.
        package = ContextPackageRecord(
            owner_id=user.id,
            novel_id=novel.id,
            fork_id=fork.id,
            package_key=f"pkg-{suffix}",
            space="fanfiction_canon",
            intent="continuation",
            fork_key=fork.fork_key,
            source_version_key="original:1",
            source_snapshot_hash=HEX64,
            through_chapter=chapter_count,
            full_book_authorized=False,
            cutoff_snapshot_hash=HEX64_C,
            scope_hash=HEX64,
            manifest_hash=HEX64_B,
            canonical_payload={},
            budget_estimate={},
            package_hash=HEX64_E,
        )
        session.add(package)
        session.flush()

        chapters: list[DerivativeChapter] = []
        for idx in range(chapter_count):
            chapter = DerivativeChapter(
                owner_id=user.id,
                novel_id=novel.id,
                project_id=project.id,
                position=idx,
                title=f"Chapter {idx + 1}",
                markdown=CANONICAL,
                markdown_checksum=CONTENT_HASH,
                status="draft",
                revision=1,
            )
            session.add(chapter)
            session.flush()
            chapters.append(chapter)

            job = DerivativeGenerationJob(
                owner_id=user.id,
                novel_id=novel.id,
                fork_id=fork.id,
                context_package_id=package.id,
                package_hash=HEX64_E,
                intent="continuation",
                job_key=f"job-{suffix}-{idx}",
                idempotency_key=_idem64(),
                status="needs_override",
                status_reason="declared divergence",
                prompt_hash=HEX64_E,
                schema_hash=HEX64,
                config_hash=HEX64_C,
                model_lineage={},
                price_snapshot={},
                budget_policy={},
            )
            session.add(job)
            session.flush()

            citation_keys = [f"fork:{fork.fork_key}:chapter:{idx + 1}"]
            candidate = DerivativeGenerationCandidate(
                owner_id=user.id,
                novel_id=novel.id,
                job_id=job.id,
                intent="continuation",
                draft_text=CANONICAL,
                citation_keys=citation_keys,
                divergence={"divergence_type": "character"},
                branch_suggestions=[],
                canon_delta_hash=HEX64_C,
                gate_verdict="needs_override",
                gate_reason="declared_canon_delta",
                package_hash=HEX64_E,
                prompt_hash=HEX64_E,
                schema_hash=HEX64,
                request_hash=HEX64,
                response_hash=HEX64,
                usage={},
                cost_usd=None,
                model_lineage={},
                approval_state="needs_override",
            )
            session.add(candidate)
            session.flush()

            revision = DerivativeRevision(
                chapter_id=chapter.id,
                owner_id=user.id,
                novel_id=novel.id,
                project_id=project.id,
                revision_number=chapter.revision,
                parent_revision_id=None,
                kind="agent_proposal",
                content=CANONICAL,
                content_checksum=CONTENT_HASH,
                actor_id=user.id,
                reason=f"divergence override:{suffix}:{idx}",
                approval_state="approved",
            )
            session.add(revision)
            session.flush()

            if with_override:
                override = DerivativeOverride(
                    owner_id=user.id,
                    novel_id=novel.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    fork_id=fork.id,
                    candidate_id=candidate.id,
                    job_id=job.id,
                    kind="character",
                    reason="the twist requires the hero to know the secret early",
                    affected_evidence=[citation_keys[0]],
                    canon_delta_hash=HEX64_C,
                    evidence_snapshot={
                        "gate_verdict": "needs_override",
                        "gate_reason": "declared_canon_delta",
                        "canon_delta_hash": HEX64_C,
                        "divergence": {"divergence_type": "character"},
                        "kind": "character",
                        "reason": "fixture divergence reason",
                        "affected_evidence": [citation_keys[0]],
                        "citation_keys": citation_keys,
                        "package_hash": HEX64_E,
                        "prompt_hash": HEX64_E,
                    },
                    actor_id=user.id,
                    approval_state="approved",
                    approver_id=user.id,
                    approved_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc),
                    approval_reason="owner approved the divergence",
                )
                session.add(override)
                session.flush()

            if not second_revision:
                break

        # Approved derivative visual fork version + approved candidate asset.
        visual_version = None
        candidate_asset = None
        if with_assets:
            visual_version = DerivativeVisualVersion(
                owner_id=user.id,
                novel_id=novel.id,
                project_id=project.id,
                fork_id=fork.id,
                visual_namespace=DERIVATIVE_VISUAL_NAMESPACE,
                version_key=f"dv-version-{suffix}",
                revision_number=1,
                parent_version_id=None,
                source_version_id=original_vb.id,
                source_snapshot_id="snap-1",
                source_snapshot_hash=HEX64,
                source_manifest_hash=HEX64_B,
                cutoff_chapter=8,
                divergence={"divergence_type": "character"},
                provenance={},
                review_state="approved",
                schema_version="derivative-visual.v1",
                schema_hash=HEX64,
                policy_hash=HEX64_B,
                prompt_hash=HEX64_E,
                model_hash=HEX64_C,
                config_hash=HEX64,
                manifest_hash=HEX64_C,
                style_profile=None,
                constraints=[],
                canonical_payload={},
                canonical_payload_hash=HEX64_E,
                idempotency_key=_idem64(),
                projection_hash=HEX64,
            )
            session.add(visual_version)
            session.flush()

            asset_id = f"dv-{suffix}-{uuid.uuid4().hex[:8]}"
            storage_key = storage.store(
                owner_id=user.id,
                novel_id=novel.id,
                visual_version_id=visual_version.id,
                asset_id=asset_id,
                mime_type="image/png",
                payload=TINY_PNG,
            )
            candidate_asset = DerivativeVisualCandidateAsset(
                owner_id=user.id,
                novel_id=novel.id,
                project_id=project.id,
                fork_id=fork.id,
                visual_version_id=visual_version.id,
                visual_version_hash=visual_version.canonical_payload_hash,
                version_key=visual_version.version_key,
                asset_key=f"asset-key-{suffix}",
                asset_id=asset_id,
                storage_key=storage_key,
                mime_type="image/png",
                content_hash=TINY_PNG_HASH,
                size_bytes=len(TINY_PNG),
                visual_namespace=DERIVATIVE_ASSET_NAMESPACE,
                scene_spec_hash=HEX64_E,
                chapter_number=1,
                source_snapshot_id="snap-1",
                source_snapshot_hash=HEX64,
                source_manifest_hash=HEX64_B,
                cutoff_chapter=8,
                identity_key="fixture-hero",
                identity_lineage=[
                    {
                        "stable_id": "fixture-hero",
                        "entity_key": "hero",
                        "entity_type": "character",
                        "source_entity_hash": HEX64_C,
                    }
                ],
                source_refs=[
                    {
                        "asset_key": "source-1",
                        "asset_id": "source-asset-1",
                        "source_asset_id": "original-asset-1",
                        "source_bytes_hash": HEX64,
                    }
                ],
                generator_lineage={
                    "provider": "mock",
                    "provider_model": "mock-img-v1",
                    "prompt_hash": HEX64_E,
                    "runtime": {},
                },
                divergence_manifest_hash=HEX64_F,
                consistency_evidence={
                    "chapter_number": 1,
                    "identity_key": "fixture-hero",
                    "identity_source_hash": HEX64_C,
                    "style_hash": HEX64,
                    "scene_spec_hash": HEX64_E,
                    "declared_style_divergence": False,
                },
                consistency_report={
                    "schema_version": "derivative-visual-asset.v1",
                    "evaluator_id": "derivative-visual-consistency.cross_chapter.v1",
                    "evaluator_version": "1.0.0",
                    "chapters": [],
                    "reasons": [],
                    "verdict": "pass",
                    "details": {},
                },
                consistency_verdict="pass",
                review_state=asset_review_state,
                canonical_payload={},
                canonical_payload_hash=HEX64_E,
                idempotency_key=_idem64(),
                projection_hash=HEX64,
                schema_version="derivative-visual-asset.v1",
            )
            session.add(candidate_asset)
            session.flush()

            session.add(
                DerivativeVisualCandidateReviewEvent(
                    owner_id=user.id,
                    novel_id=novel.id,
                    candidate_id=candidate_asset.id,
                    action="approve",
                    actor_source="human",
                    actor="owner",
                    reason="fixture approval",
                    event_key=_idem64(),
                    from_review_state="candidate",
                    to_review_state="approved",
                    details={},
                )
            )
            session.flush()

        session.commit()
        data.update(
            {
                "owner_id": user.id,
                "novel_id": novel.id,
                "project_id": project.id,
                "fork_id": fork.id,
                "chapter_ids": [ch.id for ch in chapters],
                "token": create_access_token({"sub": str(user.id)}),
                "asset_id": asset_id if candidate_asset else None,
                "asset_revision": candidate_asset.id if candidate_asset else None,
            }
        )
    engine.dispose()
    return data


# ---------------------------------------------------------------------------
# Snapshot freeze (service level)
# ---------------------------------------------------------------------------


async def _freeze(factory, storage, *, owner_id, novel_id, project_id):
    async with factory() as session:
        return await ExportSnapshotService(session, storage=storage).build(
            owner_id=owner_id, novel_id=novel_id, project_id=project_id
        )


async def test_snapshot_freeze_is_reproducible(
    api_client, asset_storage: DerivativeAssetStorage
):
    factory, sync_url, _ = api_client
    ids = _seed_chain(sync_url, asset_storage, suffix=f"rx_{uuid.uuid4().hex[:6]}")
    first = await _freeze(
        factory, asset_storage, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
        project_id=ids["project_id"],
    )
    second = await _freeze(
        factory, asset_storage, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
        project_id=ids["project_id"],
    )
    snap = first.snapshot
    assert len(snap.snapshot_hash) == 64
    assert snap.owner_id == ids["owner_id"]
    assert snap.project_id == ids["project_id"]
    assert snap.fork_id == ids["fork_id"]
    assert snap.space == "fanfiction_canon"
    assert snap.snapshot_hash == second.snapshot.snapshot_hash
    # Chapter content/version parity + revision/version alignment.
    assert len(snap.chapters) == 2
    assert [c.chapter_number for c in snap.chapters] == [1, 2]
    assert snap.chapters[0].content_hash == CONTENT_HASH
    assert snap.chapters[0].version_id == 1
    assert len(snap.revisions) == 2
    for rev in snap.revisions:
        assert rev.status == "derivative_revision"
        assert rev.owner_id == ids["owner_id"]
        assert rev.project_id == ids["project_id"]
        assert rev.fork_id == ids["fork_id"]
        assert rev.source_snapshot == HEX64
        assert rev.manifest_hash == HEX64_B
    # Approved asset present; nothing missing.
    assert len(snap.assets) == 1
    assert snap.assets[0].content_hash == TINY_PNG_HASH
    assert snap.missing_assets == ()
    assert len(snap.citations) >= 1
    # The manifest hash replays the snapshot payload.
    from app.services.derivative_export.manifest import seal_derivative_export_manifest

    manifest = seal_derivative_export_manifest(snap)
    assert derivative_export_manifest_hash(manifest) == manifest.manifest_hash
    assert manifest.manifest_hash == snap.snapshot_hash


async def test_prepare_and_download_round_trip(
    api_client, asset_storage: DerivativeAssetStorage
):
    from httpx import ASGITransport, AsyncClient

    from app.api.dependencies import require_owned_novel
    from app.core.database import get_db
    from app.main import app

    factory, sync_url, _ = api_client
    ids = _seed_chain(sync_url, asset_storage, suffix=f"api_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    project_id = ids["project_id"]

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                PREPARE_BASE.format(novel_id=novel_id, project_id=project_id),
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["manifest_hash"] == body["snapshot_hash"]
            assert len(body["manifest_hash"]) == 64
            assert body["chapter_count"] == 2
            assert body["revision_count"] == 2
            assert body["asset_count"] == 1
            assert body["missing_asset_count"] == 0
            assert body["manifest"]["text_version_hash"]
            assert derivative_export_manifest_hash(
                body["manifest"]
            ) == body["manifest_hash"]

            md_1 = await client.get(
                DOWNLOAD_BASE.format(novel_id=novel_id, project_id=project_id),
                params={"format": "markdown"},
                headers=headers,
            )
            md_2 = await client.get(
                DOWNLOAD_BASE.format(novel_id=novel_id, project_id=project_id),
                params={"format": "markdown"},
                headers=headers,
            )
            assert md_1.status_code == 200, md_1.text
            assert md_1.content == md_2.content
            assert md_1.headers["X-Export-Manifest-Hash"] == body["manifest_hash"]
            assert "text/markdown" in md_1.headers["content-type"]
            assert "Chapter 1" in md_1.text
            assert TINY_PNG_HASH in md_1.text

            epub_1 = await client.get(
                DOWNLOAD_BASE.format(novel_id=novel_id, project_id=project_id),
                params={"format": "epub"},
                headers=headers,
            )
            epub_2 = await client.get(
                DOWNLOAD_BASE.format(novel_id=novel_id, project_id=project_id),
                params={"format": "epub"},
                headers=headers,
            )
            assert epub_1.status_code == 200, epub_1.text
            assert epub_1.content == epub_2.content
            assert "application/epub+zip" in epub_1.headers["content-type"]
            with ZipFile(BytesIO(epub_1.content)) as archive:
                assert archive.namelist()[0] == "mimetype"
                assert archive.read("mimetype") == b"application/epub+zip"
                assert archive.getinfo("mimetype").compress_type == ZIP_STORED
                container = archive.read("META-INF/container.xml").decode("utf-8")
                assert 'full-path="OEBPS/content.opf"' in container
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                assert 'version="3.0"' in opf
                assert 'id="chapter-1"' in opf
                assert 'id="chapter-2"' in opf
                assert 'id="nav"' in opf
                assert 'id="ncx"' in opf
                assert 'id="citations"' in opf
                assert 'id="export-manifest"' in opf
                assert '<itemref idref="chapter-1"/>' in opf
                embedded = archive.read("OEBPS/export-manifest.json").decode("utf-8")
                assert body["manifest_hash"] in embedded
                image = archive.read(
                    f"OEBPS/assets/{TINY_PNG_HASH}.png"
                )
                assert hashlib.sha256(image).hexdigest() == TINY_PNG_HASH
    finally:
        app.dependency_overrides.clear()


async def test_missing_binary_is_explicit(
    api_client, asset_storage: DerivativeAssetStorage
):
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db
    from app.main import app

    factory, sync_url, _ = api_client
    ids = _seed_chain(sync_url, asset_storage, suffix=f"miss_{uuid.uuid4().hex[:6]}")
    # Remove the bytes but keep the approved row: the export must present an
    # explicit missing record, never a silent drop.
    visual_version_id = await _find_visual_version_id(factory, ids)
    asset_storage.remove(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        visual_version_id=visual_version_id,
        asset_id=ids["asset_id"],
        mime_type="image/png",
    )
    frozen = await _freeze(
        factory, asset_storage, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
        project_id=ids["project_id"],
    )
    assert len(frozen.snapshot.missing_assets) == 1
    assert frozen.snapshot.missing_assets[0].reason_code == "asset_bytes_missing"
    assert frozen.snapshot.assets == ()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            md = await client.get(
                DOWNLOAD_BASE.format(
                    novel_id=ids["novel_id"], project_id=ids["project_id"]
                ),
                params={"format": "markdown"},
                headers={"Authorization": f"Bearer {ids['token']}"},
            )
            assert md.status_code == 200, md.text
            assert "插图缺失" in md.text
            assert "asset_bytes_missing" in md.text

            epub = await client.get(
                DOWNLOAD_BASE.format(
                    novel_id=ids["novel_id"], project_id=ids["project_id"]
                ),
                params={"format": "epub"},
                headers={"Authorization": f"Bearer {ids['token']}"},
            )
            assert epub.status_code == 200, epub.text
            with ZipFile(BytesIO(epub.content)) as archive:
                assert not any(
                    name.startswith("OEBPS/assets/") for name in archive.namelist()
                )
                chapter = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
            assert "插图缺失" in chapter
    finally:
        app.dependency_overrides.clear()


async def _find_visual_version_id(factory, ids) -> int:
    from app.models.derivative_visual import DerivativeVisualCandidateAsset

    async with factory() as session:
        row = await session.scalar(
            select(DerivativeVisualCandidateAsset).where(
                DerivativeVisualCandidateAsset.owner_id == ids["owner_id"],
                DerivativeVisualCandidateAsset.novel_id == ids["novel_id"],
                DerivativeVisualCandidateAsset.asset_id == ids["asset_id"],
            )
        )
        return row.visual_version_id if row else 0


async def test_stale_revision_blocks_export(
    api_client, asset_storage: DerivativeAssetStorage
):
    factory, sync_url, _ = api_client
    ids = _seed_chain(sync_url, asset_storage, suffix=f"stale_{uuid.uuid4().hex[:6]}")
    # Drift the chapter version token past the published revision's version.
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE derivative_chapters SET revision = revision + 1 "
                "WHERE id = :cid"
            ),
            {"cid": ids["chapter_ids"][0]},
        )
        await session.commit()
    with pytest.raises(ExportSnapshotError) as exc:
        await _freeze(
            factory, asset_storage, owner_id=ids["owner_id"],
            novel_id=ids["novel_id"], project_id=ids["project_id"],
        )
    assert exc.value.code == "revision_version_stale"


async def test_cross_owner_export_is_identical_404(
    api_client, asset_storage: DerivativeAssetStorage
):
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db
    from app.main import app

    factory, sync_url, _ = api_client
    a = _seed_chain(sync_url, asset_storage, suffix=f"owa_{uuid.uuid4().hex[:6]}")
    # Owner B has its own novel/project.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user_b = User(
            username=f"dex_b_{uuid.uuid4().hex[:6]}",
            email=f"dex_b_{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user_b)
        session.commit()
        token_b = create_access_token({"sub": str(user_b.id)})
    engine.dispose()

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Owner B cannot reach owner A's novel (identical 404).
            resp = await client.post(
                PREPARE_BASE.format(novel_id=a["novel_id"], project_id=a["project_id"]),
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert resp.status_code == 404
            # Nonexistent project id is also an identical 404 for the owner.
            resp2 = await client.post(
                PREPARE_BASE.format(novel_id=a["novel_id"], project_id=999999),
                headers={"Authorization": f"Bearer {a['token']}"},
            )
            assert resp2.status_code == 404
            assert "project_not_found" in resp2.json()["detail"]
    finally:
        app.dependency_overrides.clear()


async def test_rejected_asset_never_exports(
    api_client, asset_storage: DerivativeAssetStorage
):
    factory, sync_url, _ = api_client
    ids = _seed_chain(
        sync_url, asset_storage, suffix=f"rej_{uuid.uuid4().hex[:6]}",
        asset_review_state="rejected",
    )
    frozen = await _freeze(
        factory, asset_storage, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
        project_id=ids["project_id"],
    )
    # The rejected candidate is simply absent — never a silent provenance drop
    # and never a placeholder pretending it was published.
    assert frozen.snapshot.assets == ()
    assert frozen.snapshot.missing_assets == ()


async def test_export_never_mutates_original_space(
    api_client, asset_storage: DerivativeAssetStorage
):
    factory, sync_url, _ = api_client
    ids = _seed_chain(sync_url, asset_storage, suffix=f"mut_{uuid.uuid4().hex[:6]}")
    async with factory() as session:
        original_before = list(
            (
                await session.execute(
                    text(
                        "SELECT chapter_number, content FROM chapters "
                        "WHERE novel_id = :n ORDER BY chapter_number"
                    ),
                    {"n": ids["novel_id"]},
                )
            ).all()
        )
        artifact_before = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM canon_space_artifacts "
                    "WHERE owner_id = :o AND novel_id = :n"
                ),
                {"o": ids["owner_id"], "n": ids["novel_id"]},
            )
            or 0
        )
    await _freeze(
        factory, asset_storage, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
        project_id=ids["project_id"],
    )
    async with factory() as session:
        original_after = list(
            (
                await session.execute(
                    text(
                        "SELECT chapter_number, content FROM chapters "
                        "WHERE novel_id = :n ORDER BY chapter_number"
                    ),
                    {"n": ids["novel_id"]},
                )
            ).all()
        )
        artifact_after = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM canon_space_artifacts "
                    "WHERE owner_id = :o AND novel_id = :n"
                ),
                {"o": ids["owner_id"], "n": ids["novel_id"]},
            )
            or 0
        )
    # D-39-02: the export reads only; it never touches the Original space.
    assert original_before == original_after
    assert artifact_before == artifact_after == 0
