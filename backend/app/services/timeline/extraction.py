"""Idempotent chapter extraction over frozen evidence packages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.analysis import AnalysisChapterStage, ModelCallAttempt
from app.schemas.timeline import EventCandidate, TimelineExtraction
from app.services.timeline.budget import BudgetGate
from app.services.timeline.evidence import EvidencePackage, validate_extraction
from app.services.timeline.model_gateway import (
    GatewayAttempt,
    ModelCallFailed,
    ModelDeployment,
    TimelineModelGateway,
)


@dataclass(frozen=True)
class ExactCacheKey:
    stage: str
    source_snapshot_hash: str
    hierarchy_build_id: str
    hierarchy_checksum: str
    unit_id: str
    evidence_package_hash: str
    prompt_hash: str
    schema_hash: str
    model_provider: str
    model_id: str
    model_revision: str
    decoding_hash: str
    config_hash: str

    @classmethod
    def for_package(cls, package: EvidencePackage, **identity: str) -> "ExactCacheKey":
        return cls(
            identity["stage"],
            package.source_snapshot_hash,
            package.hierarchy_build_id,
            package.hierarchy_checksum,
            package.unit_id,
            package.package_hash,
            identity["prompt_hash"],
            identity["schema_hash"],
            identity["model_provider"],
            identity["model_id"],
            identity["model_revision"],
            identity["decoding_hash"],
            identity["config_hash"],
        )

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(self.__dict__.values())

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_tuple()).encode()).hexdigest()


@dataclass(frozen=True)
class PublishedCandidate:
    candidate: EventCandidate
    owner_id: int
    novel_id: int
    chapter_id: int
    publication_status: str = "provisional"


@dataclass(frozen=True)
class ExtractionAudit:
    attempt_id: int
    run_id: int
    stage_key: str
    status: str
    cache_key: str
    artifact_checksum: str | None
    cache_source_attempt_id: int | None = None
    gateway_attempt: GatewayAttempt | None = None


@dataclass(frozen=True)
class CacheEntry:
    extraction: TimelineExtraction
    artifact_checksum: str
    source_attempt_id: int


@dataclass(frozen=True)
class ExtractionResult:
    events: list[PublishedCandidate]
    artifact_checksum: str
    source_attempt_id: int
    cache_hit: bool


@dataclass(frozen=True)
class PersistentCacheHit:
    gateway_output: dict
    artifact_checksum: str
    source_attempt_id: int


async def load_persistent_exact_cache(
    sessions: async_sessionmaker[AsyncSession],
    cache_key: str,
) -> PersistentCacheHit | None:
    """Recover a complete validated artifact from PostgreSQL after process restart."""
    async with sessions() as session:
        attempts = list(
            (
                await session.scalars(
                    select(ModelCallAttempt)
                    .where(
                        ModelCallAttempt.cache_key == cache_key,
                        ModelCallAttempt.status == "succeeded",
                    )
                    .order_by(ModelCallAttempt.id.desc())
                )
            ).all()
        )
        for attempt in attempts:
            stage = await session.scalar(
                select(AnalysisChapterStage).where(
                    AnalysisChapterStage.run_id == attempt.run_id,
                    AnalysisChapterStage.stage_key == attempt.stage_key,
                    AnalysisChapterStage.status == "completed",
                )
            )
            checkpoint = dict(stage.checkpoint or {}) if stage is not None else {}
            output = checkpoint.get("gateway_output")
            if isinstance(output, dict) and stage.artifact_checksum:
                return PersistentCacheHit(output, stage.artifact_checksum, attempt.id)
    return None


class InMemoryExtractionStore:
    """Deterministic adapter mirroring cache, attempt audit and partial publication writes."""

    def __init__(self) -> None:
        self.cache: dict[str, CacheEntry] = {}
        self.audits: list[ExtractionAudit] = []
        self.published: dict[int, list[PublishedCandidate]] = {}
        self._next_attempt_id = 1

    def next_attempt_id(self) -> int:
        value = self._next_attempt_id
        self._next_attempt_id += 1
        return value


class TimelineChapterExtractor:
    def __init__(
        self,
        gateway: TimelineModelGateway,
        store: InMemoryExtractionStore,
        *,
        deployment: ModelDeployment,
        budget: BudgetGate,
        prompt: str,
        prompt_hash: str,
        schema_hash: str,
        decoding_hash: str,
        config_hash: str,
    ) -> None:
        self.gateway, self.store = gateway, store
        self.deployment, self.budget, self.prompt = deployment, budget, prompt
        self.prompt_hash, self.schema_hash = prompt_hash, schema_hash
        self.decoding_hash, self.config_hash = decoding_hash, config_hash

    async def extract(
        self, *, run_id: int, version_id: int, package: EvidencePackage
    ) -> ExtractionResult:
        key = ExactCacheKey.for_package(
            package,
            stage="chapter_extract",
            prompt_hash=self.prompt_hash,
            schema_hash=self.schema_hash,
            model_provider=self.deployment.provider,
            model_id=self.deployment.model_id,
            model_revision=self.deployment.revision,
            decoding_hash=self.decoding_hash,
            config_hash=self.config_hash,
        )
        stage_key = f"chapter_extract:{package.chapter_id}:{package.unit_id}"
        cached = self.store.cache.get(key.digest)
        if cached is not None:
            events = self._publish(version_id, package, cached.extraction)
            self.store.audits.append(
                ExtractionAudit(
                    self.store.next_attempt_id(),
                    run_id,
                    stage_key,
                    "call-skipped",
                    key.digest,
                    cached.artifact_checksum,
                    cached.source_attempt_id,
                )
            )
            return ExtractionResult(
                events, cached.artifact_checksum, cached.source_attempt_id, True
            )

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": self._package_payload(package)},
        ]
        try:
            result = await self.gateway.generate(
                deployment=self.deployment,
                schema=TimelineExtraction,
                messages=messages,
                budget=self.budget,
                run_id=run_id,
                stage_key=stage_key,
                max_input_tokens=max(
                    256, sum(len(unit.text) for unit in package.units) * 2
                ),
                max_output_tokens=1024,
                business_validator=lambda output: validate_extraction(package, output),
            )
        except ModelCallFailed as exc:
            for attempt in exc.attempts:
                self.store.audits.append(
                    ExtractionAudit(
                        self.store.next_attempt_id(),
                        run_id,
                        stage_key,
                        attempt.status,
                        key.digest,
                        None,
                        gateway_attempt=attempt,
                    )
                )
            raise

        attempt_ids: list[int] = []
        for attempt in result.attempts:
            attempt_id = self.store.next_attempt_id()
            attempt_ids.append(attempt_id)
            self.store.audits.append(
                ExtractionAudit(
                    attempt_id,
                    run_id,
                    stage_key,
                    attempt.status,
                    key.digest,
                    None,
                    gateway_attempt=attempt,
                )
            )
        artifact = result.output.model_dump_json(exclude_none=False)
        artifact_checksum = hashlib.sha256(artifact.encode()).hexdigest()
        source_attempt_id = attempt_ids[-1]
        self.store.audits[-1] = ExtractionAudit(
            source_attempt_id,
            run_id,
            stage_key,
            "succeeded",
            key.digest,
            artifact_checksum,
            gateway_attempt=result.attempts[-1],
        )
        self.store.cache[key.digest] = CacheEntry(
            result.output, artifact_checksum, source_attempt_id
        )
        events = self._publish(version_id, package, result.output)
        return ExtractionResult(events, artifact_checksum, source_attempt_id, False)

    def _publish(
        self, version_id: int, package: EvidencePackage, extraction: TimelineExtraction
    ) -> list[PublishedCandidate]:
        validate_extraction(package, extraction)
        events = [
            PublishedCandidate(
                event, package.owner_id, package.novel_id, package.chapter_id
            )
            for event in extraction.events
        ]
        self.store.published[version_id] = events
        return events

    @staticmethod
    def _package_payload(package: EvidencePackage) -> str:
        return json.dumps(
            {
                "scope": {
                    "owner_id": package.owner_id,
                    "novel_id": package.novel_id,
                    "chapter_id": package.chapter_id,
                    "unit_id": package.unit_id,
                },
                "lineage": {
                    "source_snapshot_hash": package.source_snapshot_hash,
                    "hierarchy_build_id": package.hierarchy_build_id,
                    "hierarchy_checksum": package.hierarchy_checksum,
                    "evidence_package_hash": package.package_hash,
                },
                "evidence": [unit.__dict__ for unit in package.units],
            },
            sort_keys=True,
        )
