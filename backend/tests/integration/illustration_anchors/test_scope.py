"""Phase 34-03 explicit anchor repair integration tests (REQ-VIS-05, D-34-03).

Prove the owner/novel-scoped repair boundaries on CI PostgreSQL:
- revalidate classifies a published anchor as valid / needs_repair / invalid with
  the frozen evidence diff and persists the status projection after text/version
  changes (stale anchors are presented explicitly, never relocated);
- propose-repair creates an append-only repair candidate proposal + pending Web
  ApprovalRequest whose frozen payload carries the repair lineage; only a
  needs_repair anchor is accepted and the exact new span must replay against the
  current chapter;
- approve-repair (after the Web approval) publishes a new valid anchor through
  the deterministic publisher while the old anchor row is preserved as history —
  no silent mutation;
- cross-owner / wrong-chapter-version / non-stale anchor / unapproved approval /
  asset-not-proposal-ready / stale-offset repair all fail closed with no
  authoritative write.

Direct service-level tests (no HTTP client): the API surface is thin and already
owner-scoped by ``require_owned_novel``; the authority boundaries live here.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models import Chapter, Novel, User
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import IllustrationAnchor, IllustrationAnchorProposal
from app.models.illustration_job import IllustrationJob
from app.schemas.illustration_anchor import AnchorStatus
from app.services.agent_runtime.approvals import confirm
from app.services.illustration_anchors.publish import (
    AnchorPublishError,
    build_anchor_manifest,
    create_anchor_proposal,
    publish_anchor,
)
from app.services.illustration_anchors.repair import (
    AnchorRepairError,
    AnchorRepairService,
    approve_anchor_repair,
    propose_anchor_repair,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Deterministic mock chapter text (code-point offsets are stable in Python).
CHAPTER_TEXT = (
    "Arin crossed the rain-soaked courtyard. The lanterns flickered in the "
    "wind, casting long shadows over the cobblestones."
)
_EXCERPT_START = CHAPTER_TEXT.index("The lanterns")
_EXCERPT_END = len(CHAPTER_TEXT)
EXCERPT = CHAPTER_TEXT[_EXCERPT_START:_EXCERPT_END]
CHAPTER_CONTENT_HASH = hashlib.sha256(CHAPTER_TEXT.encode("utf-8")).hexdigest()
ANCHOR_HASH = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()

# Edited text: a sentence is inserted before the anchored span so the frozen
# span no longer replays the excerpt and the content version changes.
EDITED_TEXT = "A guard shouted. " + CHAPTER_TEXT
EDITED_CONTENT_HASH = hashlib.sha256(EDITED_TEXT.encode("utf-8")).hexdigest()
EDITED_EXCERPT_START = EDITED_TEXT.index(EXCERPT)
EDITED_EXCERPT_END = len(EDITED_TEXT)

HEX64 = "a" * 64
HEX64_B = "b" * 64
SNAPSHOT_HASH = "4" * 64
SCENE_SPEC_HASH = "1" * 64
PROMPT_HASH = "2" * 64
VB_HASH = "3" * 64
CONFIG_HASH = "5" * 64
ASSET_BYTES_HASH = "6" * 64


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """Seed owner + novel + chapter + succeeded job + proposal-ready cleared asset."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p34r_{suffix}",
            email=f"p34r_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(title=f"P34 Repair Novel {suffix}", owner_id=user.id)
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
            job_key=f"job-repair-{suffix}",
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
        asset = AssetRevision(
            owner_id=user.id,
            novel_id=novel.id,
            job_id=job.id,
            revision_key="rev-1",
            revision_number=1,
            asset_id="asset-1",
            storage_key=f"assets/{user.id}/{novel.id}/{ASSET_BYTES_HASH}.png",
            mime_type="image/png",
            width=1024,
            height=1024,
            size_bytes=42,
            bytes_hash=ASSET_BYTES_HASH,
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
            idempotency_key=hashlib.sha256(f"asset-{suffix}".encode("utf-8")).hexdigest(),
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
        }
    engine.dispose()
    return data


def _request(
    ids: dict[str, Any],
    *,
    action: str = "publish_illustration",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": None,
        "fork": None,
        "chapter_id": ids["chapter_id"],
        "chapter_number": 4,
        "proposal_key": f"anchor-lantern-{action}",
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
    base.update(overrides)
    return base


def _repair_request(
    ids: dict[str, Any],
    *,
    action: str = "publish_illustration",
    content: str = EDITED_TEXT,
    source_start: int = EDITED_EXCERPT_START,
    source_end: int = EDITED_EXCERPT_END,
    **overrides: Any,
) -> dict[str, Any]:
    """Repair candidate body: exact new span against the edited chapter (no key)."""
    base: dict[str, Any] = {
        "branch": None,
        "fork": None,
        "chapter_id": ids["chapter_id"],
        "chapter_number": 4,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "source_start": source_start,
        "source_end": source_end,
        "paragraph_start": 2,
        "paragraph_end": 2,
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "asset_revision_id": ids["asset_id"],
        "caption": "The lanterns flickered in the wind",
        "alt_text": "Illustration of flickering lanterns in the courtyard",
        "citation": "Chapter 4",
        "run_id": None,
        "skill_version_id": None,
        "artifact_id": None,
        "artifact_revision_id": None,
    }
    base.update(overrides)
    return base


async def _count_for_owner(factory, model, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.owner_id == owner_id)  # type: ignore[attr-defined]
            )
            or 0
        )


async def _anchor_by_id(factory, *, anchor_id: int) -> IllustrationAnchor | None:
    async with factory() as session:
        return await session.get(IllustrationAnchor, anchor_id)


async def _proposal_by_id(
    factory, *, proposal_id: int
) -> IllustrationAnchorProposal | None:
    async with factory() as session:
        return await session.get(IllustrationAnchorProposal, proposal_id)


async def _edit_chapter(
    factory, *, chapter_id: int, content: str
) -> None:
    async with factory() as session:
        await session.execute(
            text("UPDATE chapters SET content = :content WHERE id = :cid"),
            {"content": content, "cid": chapter_id},
        )
        await session.commit()


async def _publish_valid_anchor(
    factory, sync_url: str, *, suffix: str
) -> dict[str, Any]:
    """Seed + propose + Web-approve + deterministic publish → valid anchor."""
    ids = _seed(sync_url, suffix=suffix)
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
            session, request_id=ids["approval_id"], owner_id=ids["owner_id"], mode="once"
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


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


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


# ────────────────────────── revalidate ──────────────────────────


async def test_revalidate_valid_anchor_returns_valid(
    runtime_factory, migrated_postgres: str
):
    ids = await _publish_valid_anchor(
        runtime_factory, migrated_postgres, suffix=f"rv_ok_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        result = await AnchorRepairService(session).revalidate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
        )
        await session.commit()
    assert result.status is AnchorStatus.VALID
    assert result.reason_code is None
    assert result.previous_content_hash == CHAPTER_CONTENT_HASH
    assert result.current_content_hash == CHAPTER_CONTENT_HASH

    anchor = await _anchor_by_id(runtime_factory, anchor_id=ids["anchor_id"])
    assert anchor is not None
    assert anchor.status == AnchorStatus.VALID.value


async def test_revalidate_after_text_edit_marks_needs_repair(
    runtime_factory, migrated_postgres: str
):
    ids = await _publish_valid_anchor(
        runtime_factory, migrated_postgres, suffix=f"rv_stale_{uuid.uuid4().hex[:6]}"
    )
    await _edit_chapter(runtime_factory, chapter_id=ids["chapter_id"], content=EDITED_TEXT)
    async with runtime_factory() as session:
        result = await AnchorRepairService(session).revalidate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
        )
        await session.commit()
    assert result.status is AnchorStatus.NEEDS_REPAIR
    assert result.reason_code == "text_version_drift"
    assert result.previous_content_hash == CHAPTER_CONTENT_HASH
    assert result.current_content_hash == EDITED_CONTENT_HASH
    # The frozen span is preserved — never relocated.
    assert result.source_start == _EXCERPT_START
    assert result.source_end == _EXCERPT_END

    anchor = await _anchor_by_id(runtime_factory, anchor_id=ids["anchor_id"])
    assert anchor is not None
    assert anchor.status == AnchorStatus.NEEDS_REPAIR.value
    # Content is immutable: the stored excerpt/span never changed.
    assert anchor.excerpt == EXCERPT
    assert anchor.source_start == _EXCERPT_START


async def test_revalidate_version_drift_marks_needs_repair(
    runtime_factory, migrated_postgres: str
):
    ids = await _publish_valid_anchor(
        runtime_factory, migrated_postgres, suffix=f"rv_ver_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        result = await AnchorRepairService(session).revalidate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
            current_snapshot_id="ss-2",
            current_snapshot_hash=HEX64_B,
        )
        await session.commit()
    assert result.status is AnchorStatus.NEEDS_REPAIR
    assert result.reason_code == "source_snapshot_drift"
    assert result.previous_snapshot_id == "ss-1"
    assert result.current_snapshot_id == "ss-2"


async def test_revalidate_out_of_scope_fails_closed(
    runtime_factory, migrated_postgres: str
):
    ids = await _publish_valid_anchor(
        runtime_factory, migrated_postgres, suffix=f"rv_own_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await AnchorRepairService(session).revalidate(
                owner_id=ids["owner_id"] + 999,
                novel_id=ids["novel_id"],
                anchor_id=ids["anchor_id"],
            )
    assert "not found" in str(exc.value)


# ────────────────────────── propose repair ──────────────────────────


async def _stale_anchor(runtime_factory, migrated_postgres: str, *, suffix: str):
    ids = await _publish_valid_anchor(runtime_factory, migrated_postgres, suffix=suffix)
    await _edit_chapter(runtime_factory, chapter_id=ids["chapter_id"], content=EDITED_TEXT)
    async with runtime_factory() as session:
        result = await AnchorRepairService(session).revalidate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
        )
        await session.commit()
    assert result.status is AnchorStatus.NEEDS_REPAIR
    return ids


async def test_propose_repair_creates_candidate_proposal(
    runtime_factory, migrated_postgres: str
):
    ids = await _stale_anchor(
        runtime_factory, migrated_postgres, suffix=f"pr_ok_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        result = await propose_anchor_repair(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
            request=_repair_request(ids),
            action="publish_illustration",
        )
        await session.commit()
    assert result.replayed is False
    assert result.proposal.status == AnchorStatus.PENDING_APPROVAL.value
    assert result.approval_request.status == "pending"
    assert result.repaired_anchor.id == ids["anchor_id"]
    # Candidate-only: nothing became reader/export visible.
    assert await _count_for_owner(runtime_factory, IllustrationAnchor, owner_id=ids["owner_id"]) == 1

    payload = dict(result.proposal.canonical_payload or {})
    assert payload["repair_anchor_id"] == ids["anchor_id"]
    assert payload["repair_of_anchor_key"] == result.repaired_anchor.anchor_key
    assert payload["repair_reason_code"] == "text_version_drift"
    assert payload["repair_previous_content_hash"] == CHAPTER_CONTENT_HASH
    assert payload["repair_current_content_hash"] == EDITED_CONTENT_HASH
    assert result.proposal.proposal_key.startswith(f"repair:{ids['anchor_id']}:")
    # The new span is the exact edited offset, never a nearest-match relocation.
    assert result.proposal.source_start == EDITED_EXCERPT_START
    assert result.proposal.source_end == EDITED_EXCERPT_END

    # The old anchor is preserved and still stale.
    anchor = await _anchor_by_id(runtime_factory, anchor_id=ids["anchor_id"])
    assert anchor is not None
    assert anchor.status == AnchorStatus.NEEDS_REPAIR.value
    assert anchor.excerpt == EXCERPT


async def test_propose_repair_replays_idempotently(
    runtime_factory, migrated_postgres: str
):
    ids = await _stale_anchor(
        runtime_factory, migrated_postgres, suffix=f"pr_rep_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        first = await propose_anchor_repair(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
            request=_repair_request(ids),
            action="publish_illustration",
        )
        second = await propose_anchor_repair(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
            request=_repair_request(ids),
            action="publish_illustration",
        )
        await session.commit()
    assert first.replayed is False
    assert second.replayed is True
    assert second.proposal.id == first.proposal.id
    assert second.approval_request.id == first.approval_request.id
    # Original publish proposal + one replayed repair proposal (append-only).
    assert (
        await _count_for_owner(
            runtime_factory, IllustrationAnchorProposal, owner_id=ids["owner_id"]
        )
        == 2
    )


async def test_propose_repair_requires_stale_anchor(
    runtime_factory, migrated_postgres: str
):
    # No text edit: the anchor is still valid — nothing to repair (fail closed).
    ids = await _publish_valid_anchor(
        runtime_factory, migrated_postgres, suffix=f"pr_val_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await propose_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                anchor_id=ids["anchor_id"],
                request=_repair_request(ids),
                action="publish_illustration",
            )
    assert "needs_repair anchor" in str(exc.value)
    # Only the original publish proposal exists; no repair candidate was written.
    assert (
        await _count_for_owner(
            runtime_factory, IllustrationAnchorProposal, owner_id=ids["owner_id"]
        )
        == 1
    )


async def test_propose_repair_rejects_stale_offsets(
    runtime_factory, migrated_postgres: str
):
    # Proposing the frozen offsets against the edited chapter is stale and must
    # fail closed — the exact new span gate never auto-relocates (D-34-01).
    ids = await _stale_anchor(
        runtime_factory, migrated_postgres, suffix=f"pr_off_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await propose_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                anchor_id=ids["anchor_id"],
                request=_repair_request(
                    ids, source_start=_EXCERPT_START, source_end=_EXCERPT_END
                ),
                action="publish_illustration",
            )
    assert "source_range_mismatch" in str(exc.value)
    # Only the original publish proposal exists; no repair candidate was written.
    assert (
        await _count_for_owner(
            runtime_factory, IllustrationAnchorProposal, owner_id=ids["owner_id"]
        )
        == 1
    )


async def test_propose_repair_rejects_cross_owner(
    runtime_factory, migrated_postgres: str
):
    ids = await _stale_anchor(
        runtime_factory, migrated_postgres, suffix=f"pr_own_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await propose_anchor_repair(
                session,
                owner_id=ids["owner_id"] + 999,
                novel_id=ids["novel_id"],
                anchor_id=ids["anchor_id"],
                request=_repair_request(ids),
                action="publish_illustration",
            )
    assert "not found" in str(exc.value)


async def test_propose_repair_rejects_wrong_chapter_version(
    runtime_factory, migrated_postgres: str
):
    ids = await _stale_anchor(
        runtime_factory, migrated_postgres, suffix=f"pr_ch_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await propose_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                anchor_id=ids["anchor_id"],
                request=_repair_request(ids, chapter_id=ids["chapter_id"] + 999),
                action="publish_illustration",
            )
    assert "chapter" in str(exc.value)


# ────────────────────────── approve repair ──────────────────────────


async def _proposed_repair(runtime_factory, migrated_postgres: str, *, suffix: str):
    ids = await _stale_anchor(runtime_factory, migrated_postgres, suffix=suffix)
    async with runtime_factory() as session:
        result = await propose_anchor_repair(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=ids["anchor_id"],
            request=_repair_request(ids),
            action="publish_illustration",
        )
        await session.commit()
        ids["repair_proposal_id"] = result.proposal.id
        ids["repair_approval_id"] = result.approval_request.id
    return ids


async def test_approve_repair_publishes_new_anchor_preserves_old(
    runtime_factory, migrated_postgres: str
):
    ids = await _proposed_repair(
        runtime_factory, migrated_postgres, suffix=f"ap_ok_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=ids["repair_approval_id"],
            owner_id=ids["owner_id"],
            mode="once",
        )
        await session.commit()
        result = await approve_anchor_repair(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            proposal_id=ids["repair_proposal_id"],
        )
        manifest = await build_anchor_manifest(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            anchor_id=result.anchor.id,
        )
        await session.commit()

    # New published anchor is valid, versioned, and frozen to the edited text.
    assert result.anchor.status == AnchorStatus.VALID.value
    assert result.anchor.anchor_key.startswith(f"repair:{ids['anchor_id']}:")
    assert result.anchor.anchor_key != result.repaired_anchor.anchor_key
    assert result.anchor.published_asset_revision_id == ids["asset_id"]
    assert result.anchor.source_start == EDITED_EXCERPT_START
    assert result.anchor.source_end == EDITED_EXCERPT_END
    assert manifest.text_version_hash == EDITED_CONTENT_HASH
    assert manifest.anchor_key == result.anchor.anchor_key
    assert len(result.anchor.publish_manifest_hash) == 64

    # Old anchor is preserved as history and still explicitly stale.
    old = await _anchor_by_id(runtime_factory, anchor_id=ids["anchor_id"])
    assert old is not None
    assert old.status == AnchorStatus.NEEDS_REPAIR.value
    assert old.excerpt == EXCERPT
    assert old.source_start == _EXCERPT_START
    assert old.anchor_key == result.repaired_anchor.anchor_key

    # Repair proposal moved to valid (append-only projection).
    proposal = await _proposal_by_id(runtime_factory, proposal_id=ids["repair_proposal_id"])
    assert proposal is not None
    assert proposal.status == AnchorStatus.VALID.value
    assert proposal.published_asset_revision_id == ids["asset_id"]
    assert proposal.publish_manifest_hash == result.anchor.publish_manifest_hash

    # Exactly two anchors exist for the owner: the old history + the repair.
    assert (
        await _count_for_owner(runtime_factory, IllustrationAnchor, owner_id=ids["owner_id"])
        == 2
    )


async def test_approve_repair_rejects_unapproved(
    runtime_factory, migrated_postgres: str
):
    ids = await _proposed_repair(
        runtime_factory, migrated_postgres, suffix=f"ap_pend_{uuid.uuid4().hex[:6]}"
    )
    # No Web approval decision → the deterministic publisher fails closed.
    with pytest.raises(AnchorPublishError) as exc:
        async with runtime_factory() as session:
            await approve_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                proposal_id=ids["repair_proposal_id"],
            )
    assert "approved" in str(exc.value)
    assert (
        await _count_for_owner(runtime_factory, IllustrationAnchor, owner_id=ids["owner_id"])
        == 1
    )


async def test_approve_repair_rejects_when_anchor_no_longer_stale(
    runtime_factory, migrated_postgres: str
):
    ids = await _proposed_repair(
        runtime_factory, migrated_postgres, suffix=f"ap_rev_{uuid.uuid4().hex[:6]}"
    )
    # The text reverted to the original before the approval was applied.
    await _edit_chapter(
        runtime_factory, chapter_id=ids["chapter_id"], content=CHAPTER_TEXT
    )
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=ids["repair_approval_id"],
            owner_id=ids["owner_id"],
            mode="once",
        )
        await session.commit()
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await approve_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                proposal_id=ids["repair_proposal_id"],
            )
    assert "needs_repair anchor" in str(exc.value)
    assert (
        await _count_for_owner(runtime_factory, IllustrationAnchor, owner_id=ids["owner_id"])
        == 1
    )


async def test_approve_repair_rejects_asset_not_proposal_ready(
    runtime_factory, migrated_postgres: str
):
    ids = await _proposed_repair(
        runtime_factory, migrated_postgres, suffix=f"ap_ast_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        await session.execute(
            text("UPDATE asset_revisions SET approval_state = 'candidate' WHERE id = :id"),
            {"id": ids["asset_id"]},
        )
        await confirm(
            session,
            request_id=ids["repair_approval_id"],
            owner_id=ids["owner_id"],
            mode="once",
        )
        await session.commit()
    with pytest.raises(AnchorPublishError) as exc:
        async with runtime_factory() as session:
            await approve_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                proposal_id=ids["repair_proposal_id"],
            )
    assert "proposal_ready" in str(exc.value)
    assert (
        await _count_for_owner(runtime_factory, IllustrationAnchor, owner_id=ids["owner_id"])
        == 1
    )


async def test_approve_repair_rejects_plain_proposal(
    runtime_factory, migrated_postgres: str
):
    """A non-repair proposal cannot be approved through the repair surface."""
    ids = await _stale_anchor(
        runtime_factory, migrated_postgres, suffix=f"ap_plain_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(AnchorRepairError) as exc:
        async with runtime_factory() as session:
            await approve_anchor_repair(
                session,
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                proposal_id=ids["proposal_id"],
            )
    assert "not a repair candidate" in str(exc.value)


async def test_repair_proposal_is_append_only(
    runtime_factory, migrated_postgres: str
):
    """The repair candidate row is immutable: an in-place mutation fails closed."""
    ids = await _proposed_repair(
        runtime_factory, migrated_postgres, suffix=f"ap_imm_{uuid.uuid4().hex[:6]}"
    )
    with pytest.raises(ValueError):
        async with runtime_factory() as session:
            proposal = await session.get(
                IllustrationAnchorProposal, ids["repair_proposal_id"]
            )
            proposal.excerpt = "mutated excerpt"
            await session.commit()
