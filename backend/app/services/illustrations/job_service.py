"""Illustration job service (durable, idempotent, owner-scoped) (Phase 33-02).

从原 ``app/api/illustrations.py`` 拆出：本文件承载 IllustrationJobService 及
服务层全局 seam（asset storage 注入点）。

- ``IllustrationJobService`` — server-side gated、幂等的持久化 job 创建，以及
  owner/novel 作用域内的 job/asset 读取与重试。
- ``set_illustration_asset_storage`` / ``storage`` — 资产字节后端注入点
  （集成测试通过此 seam 覆盖字节后端；未注入时回落默认存储根）。
- ``IllustrationJobConflict`` / ``IllustrationJobNotFound`` — 服务层错误，
  路由层将其映射为 409 / 404。

Nothing here publishes: Phase 33 ends at candidate for Phase 34.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.illustration import AssetRevision
from app.models.illustration_job import (
    ILLUSTRATION_JOB_NONTERMINAL_STATUSES,
    IllustrationJob,
)
from app.schemas.illustration import (
    IllustrationJobContract,
    PriceSnapshot,
    build_illustration_idempotency_key,
    validate_illustration_job_contract,
)
from app.services.illustrations.gateway import (
    build_illustration_lineage,
    check_generation_prompt_gate,
)
from app.services.illustrations.storage import AssetStorage
from app.services.illustrations.worker import (
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MOCK_ILLUSTRATION_PROVIDER,
)

if TYPE_CHECKING:
    from app.api.illustrations import IllustrationGenerationRequest

# Only the deterministic mock provider is configured in this slice. A
# provider-neutral API means the request surface is provider-independent; a
# non-configured provider fails closed before any job is created.
SUPPORTED_ILLUSTRATION_PROVIDERS = frozenset({MOCK_ILLUSTRATION_PROVIDER, "hunyuan"})


class IllustrationJobConflict(ValueError):
    """A conflicting durable-job state that cannot replay."""


class IllustrationJobNotFound(ValueError):
    """A job is outside the explicit owner/novel scope (404-equivalent)."""


class IllustrationJobService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_job(
        self, *, owner_id: int, novel_id: int, request: IllustrationGenerationRequest
    ) -> tuple[IllustrationJob, bool]:
        """Server-side gated, idempotent durable job creation (D-33-01).

        Only an approved + non-stale PromptRevision may generate. A duplicate
        idempotency key replays the existing nonterminal/succeeded job; a
        terminal failed job must go through the explicit retry route.
        """
        if request.provider not in SUPPORTED_ILLUSTRATION_PROVIDERS:
            raise IllustrationJobConflict(
                f"illustration provider {request.provider!r} is not configured; "
                f"supported: {sorted(SUPPORTED_ILLUSTRATION_PROVIDERS)}"
            )
        prompt_row = await check_generation_prompt_gate(
            self._session,
            owner_id=owner_id,
            novel_id=novel_id,
            prompt_revision_id=request.prompt_revision_id,
        )
        lineage = build_illustration_lineage(
            prompt_revision=prompt_row,
            provider=request.provider,
            model=request.model,
            width=request.width,
            height=request.height,
            max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        )
        idempotency_key = build_illustration_idempotency_key(
            owner_id, novel_id, lineage
        )
        price_snapshot = _price_snapshot_for(request.provider, request.model)
        job_contract = IllustrationJobContract(
            schema_version="illustration.v1",
            artifact_kind="illustration_job",
            owner_id=owner_id,
            novel_id=novel_id,
            job_key=request.job_key,
            lineage=lineage,
            price_snapshot=price_snapshot.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        validate_illustration_job_contract(job_contract)

        existing = await self._job_by_idempotency_key(idempotency_key)
        if existing is not None:
            if (
                existing.status in ILLUSTRATION_JOB_NONTERMINAL_STATUSES
                or existing.status == "succeeded"
            ):
                return existing, True
            raise IllustrationJobConflict(
                "a terminal job with this lineage already exists; retry it "
                "explicitly instead of resubmitting"
            )

        row = IllustrationJob(
            owner_id=owner_id,
            novel_id=novel_id,
            job_key=request.job_key,
            idempotency_key=idempotency_key,
            status="queued",
            status_reason=None,
            error_code=None,
            lease_id=None,
            lease_expires_at=None,
            heartbeat_at=None,
            cancel_requested=False,
            retry_count=0,
            scene_spec_hash=lineage.scene_spec_hash,
            prompt_revision_id=lineage.prompt_revision_id,
            prompt_revision_hash=lineage.prompt_revision_hash,
            visual_bible_revision_id=lineage.visual_bible_revision_id,
            visual_bible_revision_hash=lineage.visual_bible_revision_hash,
            source_snapshot_id=lineage.source_snapshot_id,
            source_snapshot_hash=lineage.source_snapshot_hash,
            cutoff_chapter=lineage.cutoff_chapter,
            model_lineage=dict(lineage.model_lineage),
            config_hash=lineage.config_hash,
            price_snapshot=price_snapshot.model_dump(mode="json"),
            response_hash=None,
            schema_version="illustration.v1",
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._job_by_idempotency_key(idempotency_key)
            if existing is None:
                raise IllustrationJobConflict(
                    "job create race: existing row not found after rollback"
                ) from None
            return existing, True
        return row, False

    async def list_jobs(self, *, owner_id: int, novel_id: int) -> list[IllustrationJob]:
        rows = (
            await self._session.scalars(
                select(IllustrationJob)
                .where(
                    IllustrationJob.owner_id == owner_id,
                    IllustrationJob.novel_id == novel_id,
                )
                .order_by(IllustrationJob.id.desc())
            )
        ).all()
        return list(rows)

    async def get_job(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob:
        job = await self._job_by_id(owner_id=owner_id, novel_id=novel_id, job_id=job_id)
        if job is None:
            raise IllustrationJobNotFound(
                "illustration job not found in the owner/novel scope"
            )
        return job

    async def retry_job(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob:
        """Re-queue an eligible terminal/paused job with the frozen lineage."""
        job = await self._job_by_id(owner_id=owner_id, novel_id=novel_id, job_id=job_id)
        if job is None:
            raise IllustrationJobNotFound(
                "illustration job not found in the owner/novel scope"
            )
        eligible = {
            "failed",
            "cancelled",
            "outcome_unknown",
            "paused_budget",
            "paused_dependency",
        }
        if job.status not in eligible:
            raise IllustrationJobConflict(
                f"job status {job.status!r} is not eligible for retry"
            )
        job.status = "queued"
        job.error_code = None
        job.status_reason = None
        await self._session.flush()
        return job

    async def list_assets(self, *, owner_id: int, novel_id: int) -> list[AssetRevision]:
        rows = (
            await self._session.scalars(
                select(AssetRevision)
                .where(
                    AssetRevision.owner_id == owner_id,
                    AssetRevision.novel_id == novel_id,
                )
                .order_by(AssetRevision.id.desc())
            )
        ).all()
        return list(rows)

    async def get_asset(
        self, *, owner_id: int, novel_id: int, asset_id: int
    ) -> AssetRevision:
        asset = await self._session.scalar(
            select(AssetRevision).where(
                AssetRevision.owner_id == owner_id,
                AssetRevision.novel_id == novel_id,
                AssetRevision.id == asset_id,
            )
        )
        if asset is None:
            raise IllustrationJobNotFound(
                "illustration asset not found in the owner/novel scope"
            )
        return asset

    async def _job_by_id(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob | None:
        return await self._session.scalar(
            select(IllustrationJob).where(
                IllustrationJob.owner_id == owner_id,
                IllustrationJob.novel_id == novel_id,
                IllustrationJob.id == job_id,
            )
        )

    async def _job_by_idempotency_key(
        self, idempotency_key: str
    ) -> IllustrationJob | None:
        return await self._session.scalar(
            select(IllustrationJob).where(
                IllustrationJob.idempotency_key == idempotency_key
            )
        )


def _price_snapshot_for(provider: str, model: str) -> PriceSnapshot:
    """Frozen pricing; cost is always settled against this snapshot."""
    if provider == "hunyuan":
        # 腾讯混元生图（ZCodeProxy）：按图计费 + token 估算价。
        return PriceSnapshot(
            provider=provider,
            model=model,
            input_price_per_million=Decimal("0.10"),
            output_price_per_million=Decimal("0.10"),
            image_price_per_image=Decimal("0.04"),
        )
    return PriceSnapshot(
        provider=provider,
        model=model,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.10"),
        image_price_per_image=Decimal("0.04"),
    )


# ---------------------------------------------------------------------------
# Asset storage seam (test override + deployment default)
# ---------------------------------------------------------------------------

_asset_storage: AssetStorage | None = None


def set_illustration_asset_storage(storage: AssetStorage | None) -> None:
    """Override the bytes backend (used by integration tests)."""
    global _asset_storage
    _asset_storage = storage


def storage() -> AssetStorage:
    if _asset_storage is not None:
        return _asset_storage
    return AssetStorage(AssetStorage.default_storage_root())


__all__ = [
    "IllustrationJobConflict",
    "IllustrationJobNotFound",
    "IllustrationJobService",
    "SUPPORTED_ILLUSTRATION_PROVIDERS",
    "set_illustration_asset_storage",
    "storage",
]
