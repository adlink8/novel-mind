"""Chapter-state processing mixin for the narrative-memory builder worker.

Extracted from ``builder_worker.py`` (Phase 28-01 whole-book builder): this
mixin owns the per-chapter candidate build seam — resolving the chapter id for
a stage, loading evidence leaves + optional signals, building the deterministic
chapter input package + cache identity, invoking the model gateway with bounded
candidate context/continuity digests, persisting the candidate package through
``CandidateAuthority``, and isolating every failure class (cancel / budget /
package-build / gateway / repository / unknown) through the recovery
coordinator. It never promotes and never writes active pointers (D-02/D-07).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.builder_budget import BudgetExceeded, UnknownPricing
from app.services.narrative_memory.builder_contracts import (
    CONTEXT_SUMMARY_MAX_LENGTH,
    CONTINUITY_NOTES_MAX_LENGTH,
    NEXT_HINT_MAX_LENGTH,
    ReasonCode,
    SourceStatus,
    build_chapter_analysis_artifact,
)
from app.services.narrative_memory.builder_gateway import (
    CancelledBeforePersist,
    GatewayError,
)
from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    artifact_checksum_for_package,
    build_chapter_state_input,
    chapter_cache_identity,
    default_optional_signal,
    load_chapter_evidence_leaves,
    rebind_chapter_state_package,
)
from app.services.narrative_memory.builder_repository import (
    BuilderRepository,
    BuilderRepositoryError,
)
from app.services.narrative_memory.contracts import ModelLineage
from app.services.narrative_memory.recovery import RecoveryCoordinator


class ChapterStateWorkerMixin:
    """Per-chapter candidate build seam (see module docstring)."""

    async def _run_chapter_stage(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        policy,
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        stage_key = stage.stage_key
        chapter_number = int(stage.chapter_start or 0)
        chapter_id = await self._chapter_id_for_stage(session, repo, run_id, stage)
        attempt_count = int(stage.attempt_count or 0) + 1
        idempotency_key = f"{run_id}:{stage_key}:{attempt_count}"
        await repo.mark_stage(
            stage,
            status="running",
            increment_attempt=True,
            reason_code=None,
            idempotency_key=idempotency_key,
        )
        try:
            leaves = await load_chapter_evidence_leaves(
                session,
                hierarchy_build_id=version.hierarchy_build_id,
                novel_id=version.novel_id,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                source_snapshot_hash=version.source_snapshot_hash,
            )
            optional_signals = await self._load_optional_signals(
                session,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version=version,
                chapter_number=chapter_number,
            )
            lineage = ModelLineage.model_validate(version.model_lineage)
            input_package = build_chapter_state_input(
                version=version,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                evidence_leaves=leaves,
                optional_signals=optional_signals,
                prompt_hash=policy.prompt_hash,
                schema_hash=policy.schema_hash,
                model_lineage=lineage,
                decoding_hash=policy.decoding_hash,
                config_hash=policy.config_hash,
                policy_hash=policy.policy_hash,
            )
            package_cs, cache_key = chapter_cache_identity(input_package)
            # Bounded candidate context/continuity artifact (D-08). Digests are
            # compressed payloads only; the next hint is spoiler-safe by
            # construction (references only chapters <= cutoff).
            analysis_artifact = build_chapter_analysis_artifact(
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                source_snapshot_hash=version.source_snapshot_hash,
                input_hash=package_cs,
                spoiler_policy_version=policy.spoiler_policy_version,
                max_length=max(
                    CONTEXT_SUMMARY_MAX_LENGTH,
                    NEXT_HINT_MAX_LENGTH,
                    CONTINUITY_NOTES_MAX_LENGTH,
                ),
                context_payload=input_package.model_dump(mode="json"),
                chunk_reprs=[
                    leaf.model_dump(mode="json")
                    for leaf in input_package.evidence_leaves
                ],
                previous_context_summary=self._bounded_previous_context(input_package),
                next_context_hint=self._safe_next_hint(input_package),
                continuity_notes=(
                    f"source_snapshot:{version.source_snapshot_hash[:12]};"
                    f"input:{package_cs[:12]}"
                ),
            )

            def validate_output(raw: Any) -> dict[str, Any]:
                # Cache hits store the already-validated envelope.
                if (
                    isinstance(raw, dict)
                    and "candidate_package" in raw
                    and "artifact_checksum" in raw
                ):
                    from app.services.narrative_memory.contracts import (
                        CandidatePackage,
                    )

                    package = CandidatePackage.model_validate(raw["candidate_package"])
                    return {
                        "candidate_package": package.model_dump(mode="json"),
                        "artifact_checksum": str(raw["artifact_checksum"]),
                    }
                package = rebind_chapter_state_package(
                    input_package=input_package,
                    model_output=raw if isinstance(raw, dict) else {"claims": []},
                )
                return {
                    "candidate_package": package.model_dump(mode="json"),
                    "artifact_checksum": artifact_checksum_for_package(package),
                }

            async def is_cancelled() -> bool:
                return await repo.is_cancelled(run_id)

            result = await gateway.execute_structured(
                run_id=run_id,
                stage_key=stage.stage_key,
                cache_key=cache_key,
                request_payload=input_package.model_dump(mode="json"),
                validate_output=validate_output,
                is_cancelled=is_cancelled,
                # Full chapter evidence packages routinely exceed the gateway
                # defaults (800/1200); under-reserve causes BudgetExceeded on settle.
                estimated_input_tokens=48_000,
                estimated_output_tokens=8_192,
            )
            import json as _json

            from app.services.narrative_memory.contracts import CandidatePackage

            candidate = result.output["candidate_package"]
            package = CandidatePackage.model_validate_json(
                _json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            )
            authority = CandidateAuthority(session)
            await authority.persist_package(
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
                package=package,
            )
            artifact = result.output["artifact_checksum"]
            await repo.mark_stage(
                stage,
                status="completed",
                package_checksum=package_cs,
                cache_key=cache_key,
                artifact_checksum=artifact,
                reason_code=ReasonCode.COMPLETED_CANDIDATE,
                source_checksum=version.source_snapshot_hash,
                model_lineage=lineage.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                checkpoint={
                    "chapter_id": chapter_id,
                    "chapter_number": chapter_number,
                    "cache_hit": result.cache_hit,
                    "attempt_count": attempt_count,
                    "calls": result.attempt_number,
                    "chapter_analysis_artifact": analysis_artifact.model_dump(
                        mode="json"
                    ),
                    "chapter_digest": analysis_artifact.chapter_digest,
                },
                journal=True,
            )
        except CancelledBeforePersist:
            stage = await self._reload_stage(session, run_id, stage_key)
            if recovery is not None:
                await recovery.cancel_stage(run_id=run_id, stage=stage)
            else:
                await repo.mark_stage(
                    stage, status="cancelled", reason="cancelled_before_persist"
                )
            raise
        except (UnknownPricing, BudgetExceeded) as exc:
            stage = await self._reload_stage(session, run_id, stage_key)
            if recovery is not None:
                await recovery.pause_budget(run_id=run_id, stage=stage, exc=exc)
            else:
                await repo.mark_stage(
                    stage, status="paused_budget", reason=type(exc).__name__
                )
                await repo.update_run_status(
                    run_id, status="paused_budget", reason=type(exc).__name__
                )
        except (PackageBuildError, GatewayError, BuilderRepositoryError) as exc:
            stage = await self._reload_stage(session, run_id, stage_key)
            if recovery is not None:
                await recovery.isolate_chapter(
                    session,
                    run_id=run_id,
                    stage=stage,
                    exc=exc,
                    attempt_count=attempt_count,
                )
            else:
                await repo.mark_stage(stage, status="failed", reason=str(exc)[:160])
        except Exception as exc:  # noqa: BLE001 - durable failure isolation
            if session.in_transaction() and session.is_active is False:
                await session.rollback()
            stage = await self._reload_stage(session, run_id, stage_key)
            if recovery is not None:
                await recovery.isolate_chapter(
                    session,
                    run_id=run_id,
                    stage=stage,
                    exc=exc,
                    attempt_count=attempt_count,
                )
            else:
                await repo.mark_stage(
                    stage,
                    status="failed",
                    reason=f"{type(exc).__name__}:{exc}"[:160],
                )

    @staticmethod
    def _bounded_previous_context(input_package) -> str:
        """Deterministic bounded summary of the frozen inputs for this chapter."""
        return (
            f"Frozen snapshot {input_package.source_snapshot_hash[:12]}, "
            f"hierarchy {input_package.hierarchy_build_id[:12]}, "
            f"{len(input_package.evidence_leaves)} evidence leaves, "
            f"cutoff chapter {input_package.chapter_number}."
        )

    @staticmethod
    def _safe_next_hint(input_package) -> str:
        """Disambiguation-only next hint, safe at the chapter cutoff by construction.

        It references only the current chapter and its evidence spans — never a
        fact from a later chapter.
        """
        return (
            f"Continue disambiguation within chapter {input_package.chapter_number}; "
            f"evidence spans {len(input_package.evidence_leaves)} leaves."
        )

    async def _reload_stage(
        self, session: AsyncSession, run_id: int, stage_key: str
    ) -> NarrativeMemoryBuildStage:
        row = await session.scalar(
            select(NarrativeMemoryBuildStage).where(
                NarrativeMemoryBuildStage.run_id == run_id,
                NarrativeMemoryBuildStage.stage_key == stage_key,
            )
        )
        if row is None:
            raise BuilderRepositoryError(f"stage {stage_key} missing after error")
        return row

    async def _load_optional_signals(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version: NarrativeMemoryVersion,
        chapter_number: int,
    ) -> list:
        if self._optional_source_loader is not None:
            return list(
                await self._optional_source_loader(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version=version,
                    chapter_number=chapter_number,
                )
            )
        try:
            from app.services.narrative_memory.optional_sources import (
                load_optional_signals,
            )

            return list(
                await load_optional_signals(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version=version,
                    chapter_number=chapter_number,
                )
            )
        except ImportError:
            return [
                default_optional_signal(
                    source_kind="timeline", status=SourceStatus.HEALTHY_EMPTY
                ),
                default_optional_signal(
                    source_kind="relationship", status=SourceStatus.HEALTHY_EMPTY
                ),
                default_optional_signal(
                    source_kind="clue", status=SourceStatus.HEALTHY_EMPTY
                ),
            ]

    async def _chapter_id_for_stage(
        self,
        session: AsyncSession,
        repo: BuilderRepository,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
    ) -> int:
        # stage_key format chapter_state:{chapter_id}
        suffix = stage.stage_key.split(":", 1)[-1]
        if suffix.isdigit():
            return int(suffix)
        run = await session.get(
            __import__(
                "app.models.narrative_memory_builder",
                fromlist=["NarrativeMemoryBuildRun"],
            ).NarrativeMemoryBuildRun,
            run_id,
        )
        mapping = (run.progress or {}).get("chapter_ids") if run else {}
        chapter_number = str(stage.chapter_start)
        if mapping and chapter_number in mapping:
            return int(mapping[chapter_number])
        raise BuilderRepositoryError(f"cannot resolve chapter id for {stage.stage_key}")
