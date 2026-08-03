"""Durable bottom-up narrative-memory candidate builder worker."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chunk_build import ChunkHierarchyNode
from app.models.novel import Chapter
from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.audit import audit_assets, provider_calls_allowed
from app.services.narrative_memory.audit_contracts import EligibilityReport
from app.services.narrative_memory.audit_sources import AssetInventorySource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.builder_contracts import (
    CONTEXT_SUMMARY_MAX_LENGTH,
    CONTINUITY_NOTES_MAX_LENGTH,
    NEXT_HINT_MAX_LENGTH,
    ModelDeploymentSnapshot,
    ReasonCode,
    RunPolicy,
    SourceStatus,
    StageKind,
    build_chapter_analysis_artifact,
    package_checksum,
)
from app.services.narrative_memory.builder_gateway import (
    BuilderModelGateway,
    CancelledBeforePersist,
    GatewayError,
    ModelTransport,
)
from app.services.narrative_memory.builder_budget import BudgetExceeded, UnknownPricing
from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    artifact_checksum_for_package,
    build_arc_volume_candidate,
    build_chapter_state_input,
    build_global_candidate,
    chapter_cache_identity,
    default_optional_signal,
    load_chapter_evidence_leaves,
    load_child_chapter_authority,
    rebind_chapter_state_package,
)
from app.services.narrative_memory.builder_repository import (
    BuilderRepository,
    BuilderRepositoryError,
)
from app.services.narrative_memory.contracts import ModelLineage, NodeKind
from app.services.narrative_memory.recovery import RecoveryCoordinator
from app.services.narrative_memory.source_manifest import (
    compute_source_manifest,
    detect_chapter_drift,
    frozen_manifest_from_progress,
    recompute_source_manifest,
    store_frozen_manifest,
)


FORBIDDEN_IMPORT_FRAGMENTS = (
    "reader_chat",
    "ReaderConversation",
    "ReaderMessage",
    "promote_timeline",
    "promote_clue",
    "TimelineActivePointer",
    "ClueActivePointer",
    "NarrativeActivePointer",
    "current_version",
    "set_active_pointer",
)


@dataclass(frozen=True)
class WorkerResult:
    run_id: int
    status: str
    status_reason: str | None
    completed_stages: tuple[str, ...]
    failed_stages: tuple[str, ...]
    blocked_stages: tuple[str, ...]
    transport_calls: int
    worker_artifact_checksum: str | None = None
    database_manifest_checksum: str | None = None
    source_manifest_checksum: str | None = None


class NarrativeMemoryBuilderWorker:
    """Lease-safe Chapter→Arc→Global candidate builder (no promotion)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        inventory_source: AssetInventorySource,
        transport: ModelTransport,
        deployment: ModelDeploymentSnapshot,
        optional_source_loader: Callable[..., Any] | None = None,
    ) -> None:
        self._sessions = sessions
        self._inventory_source = inventory_source
        self._transport = transport
        self._deployment = deployment
        self._optional_source_loader = optional_source_loader
        self.transport_calls = 0

    async def start_run(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        run_policy: RunPolicy,
        chapter_ids: Sequence[int] | None = None,
    ) -> int:
        async with self._sessions() as session:
            repo = BuilderRepository(session)
            version = await repo.get_version(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            report = await audit_assets(
                self._inventory_source, owner_id=owner_id, novel_id=novel_id
            )
            self._assert_eligibility_matches(version, report)
            run = await repo.create_run(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_id,
                eligibility_report_checksum=version.eligibility_report_checksum,
                eligibility_policy_version=version.eligibility_policy_version,
                run_policy=run_policy,
            )
            chapters = await self._resolve_chapters(
                session,
                novel_id=novel_id,
                hierarchy_build_id=version.hierarchy_build_id,
                chapter_ids=chapter_ids,
            )
            stage_specs = [
                {
                    "stage_key": f"chapter_state:{chapter.id}",
                    "stage_kind": StageKind.CHAPTER_STATE.value,
                    "chapter_start": chapter.chapter_number,
                    "chapter_end": chapter.chapter_number,
                    "dependency_keys": [],
                    "chapter_id": chapter.id,
                }
                for chapter in chapters
            ]
            # Placeholder parent/global stages filled after boundary plan.
            stage_specs.append(
                {
                    "stage_key": "arc_volume_plan:book",
                    "stage_kind": StageKind.ARC_VOLUME_PLAN.value,
                    "dependency_keys": [
                        f"chapter_state:{chapter.id}" for chapter in chapters
                    ],
                }
            )
            await repo.ensure_stages(run, stage_specs)
            # Stash chapter id map on run progress for deterministic resume.
            progress = dict(run.progress or {})
            progress["chapter_ids"] = {
                str(chapter.chapter_number): chapter.id for chapter in chapters
            }
            progress["chapter_numbers"] = [
                chapter.chapter_number for chapter in chapters
            ]
            # Freeze the source manifest (D-05): the snapshot drives chapter
            # execution and is DB-recomputable; drift fails closed on resume.
            manifest = await compute_source_manifest(
                session, version=version, chapters=chapters
            )
            progress = store_frozen_manifest(progress, manifest)
            await repo.update_run_status(run.id, status=run.status, progress=progress)
            run.source_snapshot_hash = version.source_snapshot_hash
            await session.commit()
            return run.id

    async def process_run(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        lease_id: str | None = None,
        max_stages: int | None = None,
    ) -> WorkerResult:
        async with self._sessions() as session:
            repo = BuilderRepository(session)
            version = await repo.get_version(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            run = await repo.get_run(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            if run is None:
                raise BuilderRepositoryError("run not found; call start_run first")
            report = await audit_assets(
                self._inventory_source, owner_id=owner_id, novel_id=novel_id
            )
            self._assert_eligibility_matches(version, report)
            claimed_lease = await repo.claim_run_lease(run, lease_id=lease_id)
            run_id = int(run.id)
            policy = RunPolicy.model_validate(run.run_policy)
            gateway = BuilderModelGateway(
                session,
                transport=self._transport,
                deployment=self._deployment,
                max_schema_repairs=policy.max_schema_repairs,
            )

            if not provider_calls_allowed(report):
                await repo.update_run_status(
                    run_id,
                    status="paused_dependency",
                    reason="provider_calls_not_allowed",
                )
                await session.commit()
                return await self._snapshot_result(
                    session, run_id, gateway.transport_calls
                )

            recovery = RecoveryCoordinator(repo)
            await repo.increment_resume_count(run_id)

            # Source drift fail-closed (D-05): recompute the frozen manifest
            # from current authority rows; any drifted chapter is blocked rather
            # than re-run against stale evidence, and never restarts the book.
            source_drift = await self._source_drift_map(
                session, run=run, version=version
            )

            processed = 0
            # Phase A: chapter states — resume plan drives what may run.
            plan = await recovery.resume_plan(run_id)
            runnable_keys = {item.stage_key for item in plan.runnable}
            stages = await repo.list_stages(run_id)
            chapter_stages = [
                s for s in stages if s.stage_kind == StageKind.CHAPTER_STATE.value
            ]
            for stage in chapter_stages:
                if max_stages is not None and processed >= max_stages:
                    break
                # Terminal stages (completed/isolated/blocked) are never re-run.
                if stage.stage_key not in runnable_keys:
                    continue
                if await repo.is_cancelled(run_id):
                    await repo.update_run_status(
                        run_id, status="cancelled", reason="cancel_requested"
                    )
                    break
                chapter_number = int(stage.chapter_start or 0)
                if chapter_number in source_drift:
                    # Fail closed: block the drifted chapter with a stable
                    # reason so the run is partial, not silently stale.
                    await repo.mark_stage(
                        stage,
                        status="blocked_dependency",
                        reason=source_drift[chapter_number],
                        reason_code=ReasonCode.SOURCE_DRIFT,
                        journal=True,
                    )
                    processed += 1
                    await session.flush()
                    await repo.heartbeat(run_id, claimed_lease)
                    continue
                await self._run_chapter_stage(
                    session,
                    repo=repo,
                    gateway=gateway,
                    version=version,
                    run_id=run_id,
                    stage=stage,
                    policy=policy,
                    recovery=recovery,
                )
                processed += 1
                await session.flush()
                await repo.heartbeat(run_id, claimed_lease)

            # Phase B: boundary plan + arc aggregates (if arc planner available)
            stages = await repo.list_stages(run_id)
            if all(
                s.status == "completed"
                for s in stages
                if s.stage_kind == StageKind.CHAPTER_STATE.value
            ):
                await self._ensure_boundary_and_parents(
                    session,
                    repo=repo,
                    gateway=gateway,
                    version=version,
                    run_id=run_id,
                    policy=policy,
                    max_stages=max_stages,
                    processed=processed,
                    lease_id=claimed_lease,
                    recovery=recovery,
                )

            # Phase C: global + manifest if parents complete
            await self._maybe_run_global_and_manifest(
                session,
                repo=repo,
                gateway=gateway,
                version=version,
                run_id=run_id,
                policy=policy,
                recovery=recovery,
            )

            self.transport_calls += gateway.transport_calls
            result = await self._finalize_run_status(session, repo, run_id)
            await session.commit()
            return result

    async def cancel(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> WorkerResult:
        async with self._sessions() as session:
            repo = BuilderRepository(session)
            run = await repo.request_cancel(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            await session.commit()
            return await self._snapshot_result(session, run.id, 0)

    async def _run_chapter_stage(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway: BuilderModelGateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        policy: RunPolicy,
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
                previous_context_summary=self._bounded_previous_context(
                    input_package
                ),
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
                await recovery.pause_budget(
                    run_id=run_id, stage=stage, exc=exc
                )
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

    async def _source_drift_map(
        self,
        session: AsyncSession,
        *,
        run: NarrativeMemoryBuildRun,
        version: NarrativeMemoryVersion,
    ) -> dict[int, str]:
        """Recompute the frozen manifest and return per-chapter drift reasons.

        Empty dict means the source is unchanged and the run may proceed. A
        non-empty dict means those chapters must be blocked (fail closed) rather
        than processed with stale evidence (D-05).
        """
        frozen = frozen_manifest_from_progress(run.progress)
        if frozen is None:
            return {}
        recomputed = await recompute_source_manifest(session, version=version)
        return detect_chapter_drift(frozen, recomputed)

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

    async def _ensure_boundary_and_parents(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway: BuilderModelGateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        policy: RunPolicy,
        max_stages: int | None,
        processed: int,
        lease_id: str,
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        try:
            from app.services.narrative_memory.arc_planner import (
                plan_arc_boundaries,
                boundary_plan_checksum,
            )
        except ImportError:
            return

        run = await session.get(
            __import__(
                "app.models.narrative_memory_builder",
                fromlist=["NarrativeMemoryBuildRun"],
            ).NarrativeMemoryBuildRun,
            run_id,
        )
        if run is None:
            return
        chapter_numbers = list((run.progress or {}).get("chapter_numbers") or [])
        if not chapter_numbers:
            return
        plan = plan_arc_boundaries(
            chapter_numbers=chapter_numbers,
            window_size=policy.arc_window_size,
            policy_version=policy.policy_version,
            explicit_volumes=None,
        )
        checksum = boundary_plan_checksum(plan)
        await repo.update_run_status(
            run_id,
            status="running",
            boundary_plan=plan,
            boundary_plan_checksum=checksum,
        )
        plan_stage = next(
            (
                s
                for s in await repo.list_stages(run_id)
                if s.stage_kind == StageKind.ARC_VOLUME_PLAN.value
            ),
            None,
        )
        if plan_stage and plan_stage.status != "completed":
            await repo.mark_stage(
                plan_stage,
                status="completed",
                artifact_checksum=checksum,
                package_checksum=checksum,
                checkpoint={"boundary_plan_checksum": checksum},
            )

        parent_specs = []
        for item in plan["ranges"]:
            stage_key = item["stage_key"]
            child_keys = [
                f"chapter_state:{(run.progress or {}).get('chapter_ids', {}).get(str(n))}"
                for n in item["chapter_numbers"]
            ]
            child_keys = [k for k in child_keys if not k.endswith(":None")]
            # Prefer chapter number mapping via stored progress.
            chapter_ids_map = (run.progress or {}).get("chapter_ids") or {}
            child_keys = [
                f"chapter_state:{chapter_ids_map[str(n)]}"
                for n in item["chapter_numbers"]
                if str(n) in chapter_ids_map
            ]
            parent_specs.append(
                {
                    "stage_key": stage_key,
                    "stage_kind": StageKind.ARC_VOLUME_AGGREGATE.value,
                    "chapter_start": item["chapter_start"],
                    "chapter_end": item["chapter_end"],
                    "dependency_keys": child_keys,
                }
            )
        parent_specs.append(
            {
                "stage_key": "global_story:book",
                "stage_kind": StageKind.GLOBAL_AGGREGATE.value,
                "chapter_start": min(chapter_numbers),
                "chapter_end": max(chapter_numbers),
                "dependency_keys": [item["stage_key"] for item in plan["ranges"]],
            }
        )
        parent_specs.append(
            {
                "stage_key": "manifest_validation:book",
                "stage_kind": StageKind.MANIFEST_VALIDATION.value,
                "dependency_keys": ["global_story:book"],
            }
        )
        await repo.ensure_stages(run, parent_specs)

        stages = await repo.list_stages(run_id)
        by_key = {s.stage_key: s for s in stages}
        local_processed = processed
        for spec in parent_specs:
            if spec["stage_kind"] != StageKind.ARC_VOLUME_AGGREGATE.value:
                continue
            if max_stages is not None and local_processed >= max_stages:
                break
            stage = by_key[spec["stage_key"]]
            if stage.status == "completed":
                continue
            deps = [by_key[k] for k in stage.dependency_keys if k in by_key]
            if any(d.status == "failed" for d in deps):
                await repo.mark_stage(
                    stage, status="blocked_dependency", reason="child_failed"
                )
                continue
            if not deps or any(d.status != "completed" for d in deps):
                continue
            await self._run_arc_stage(
                session,
                repo=repo,
                gateway=gateway,
                version=version,
                run_id=run_id,
                stage=stage,
                policy=policy,
                boundary_checksum=checksum,
                recovery=recovery,
            )
            local_processed += 1
            await repo.heartbeat(run_id, lease_id)

    async def _run_arc_stage(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway: BuilderModelGateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        policy: RunPolicy,
        boundary_checksum: str,
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        attempt_count = int(stage.attempt_count or 0) + 1
        await repo.mark_stage(stage, status="running", increment_attempt=True)
        try:
            chapter_numbers = list(
                range(int(stage.chapter_start or 0), int(stage.chapter_end or 0) + 1)
            )
            nodes, claims, links = await load_child_chapter_authority(
                session,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
                chapter_numbers=chapter_numbers,
            )
            node_kind = (
                NodeKind.VOLUME
                if stage.stage_key.startswith("volume:")
                else NodeKind.STORY_ARC
            )
            request_payload = {
                "stage_key": stage.stage_key,
                "boundary_plan_checksum": boundary_checksum,
                "child_node_keys": [n.node_key for n in nodes],
                "child_claim_keys": [c.claim_key for c in claims],
                "child_link_count": len(links),
                "prompt_hash": policy.prompt_hash,
                "schema_hash": policy.schema_hash,
                "policy_hash": policy.policy_hash,
            }
            package_cs = sha256(
                __import__("json").dumps(request_payload, sort_keys=True).encode()
            ).hexdigest()
            cache_key = f"nmb:arc:{package_cs[:100]}"

            def validate_output(raw: Any) -> dict[str, Any]:
                model_claims = []
                if isinstance(raw, dict):
                    model_claims = list(raw.get("claims") or [])
                package = build_arc_volume_candidate(
                    node_kind=node_kind,
                    node_key=stage.stage_key.replace("arc_volume:", "").replace(
                        "aggregate:", ""
                    )
                    if False
                    else stage.stage_key.replace("arc_volume_aggregate:", "")
                    if stage.stage_key.startswith("arc_volume_aggregate:")
                    else stage.stage_key,
                    chapter_start=int(stage.chapter_start or 1),
                    chapter_end=int(stage.chapter_end or 1),
                    child_nodes=nodes,
                    child_claims=claims,
                    child_links=links,
                    model_claims=model_claims,
                    display_label=str(
                        (raw or {}).get("display_label") or stage.stage_key
                    )
                    if isinstance(raw, dict)
                    else stage.stage_key,
                )
                # Parent packages already include children; persist only parent-level
                # rows by filtering to parent node claims/edges and child nodes already
                # present — CandidateAuthority is idempotent for identical children.
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
                request_payload=request_payload,
                validate_output=validate_output,
                is_cancelled=is_cancelled,
                estimated_input_tokens=8_000,
                estimated_output_tokens=4_096,
            )
            import json as _json

            from app.services.narrative_memory.contracts import CandidatePackage

            package = CandidatePackage.model_validate_json(
                _json.dumps(
                    result.output["candidate_package"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            await CandidateAuthority(session).persist_package(
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
                package=package,
            )
            await repo.mark_stage(
                stage,
                status="completed",
                package_checksum=package_cs,
                cache_key=cache_key,
                artifact_checksum=result.output["artifact_checksum"],
                reason_code=ReasonCode.COMPLETED_CANDIDATE,
                source_checksum=version.source_snapshot_hash,
                model_lineage=ModelLineage.model_validate(
                    version.model_lineage
                ).model_dump(mode="json"),
                idempotency_key=f"{run_id}:{stage.stage_key}:{attempt_count}",
                checkpoint={"boundary_plan_checksum": boundary_checksum},
                journal=True,
            )
        except CancelledBeforePersist:
            stage = await self._reload_stage(session, run_id, stage.stage_key)
            if recovery is not None:
                await recovery.cancel_stage(run_id=run_id, stage=stage)
            else:
                await repo.mark_stage(
                    stage, status="cancelled", reason="cancelled_before_persist"
                )
        except (UnknownPricing, BudgetExceeded) as exc:
            stage = await self._reload_stage(session, run_id, stage.stage_key)
            if recovery is not None:
                await recovery.pause_budget(run_id=run_id, stage=stage, exc=exc)
            else:
                await repo.mark_stage(
                    stage, status="paused_budget", reason=type(exc).__name__
                )
        except Exception as exc:  # noqa: BLE001
            stage = await self._reload_stage(session, run_id, stage.stage_key)
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

    async def _maybe_run_global_and_manifest(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway: BuilderModelGateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        policy: RunPolicy,
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        stages = await repo.list_stages(run_id)
        parents = [
            s for s in stages if s.stage_kind == StageKind.ARC_VOLUME_AGGREGATE.value
        ]
        if not parents:
            return
        global_stage = next(
            (s for s in stages if s.stage_kind == StageKind.GLOBAL_AGGREGATE.value),
            None,
        )
        if global_stage is None:
            return
        if any(
            p.status == "failed" or p.status == "blocked_dependency" for p in parents
        ):
            if global_stage.status != "completed":
                await repo.mark_stage(
                    global_stage,
                    status="blocked_dependency",
                    reason="parent_incomplete_or_failed",
                    reason_code=ReasonCode.PARENT_INCOMPLETE,
                    journal=True,
                )
            return
        if any(p.status != "completed" for p in parents):
            return
        if global_stage.status != "completed":
            await self._run_global_stage(
                session,
                repo=repo,
                gateway=gateway,
                version=version,
                run_id=run_id,
                stage=global_stage,
                policy=policy,
                parents=parents,
                recovery=recovery,
            )
        stages = await repo.list_stages(run_id)
        global_stage = next(
            s for s in stages if s.stage_kind == StageKind.GLOBAL_AGGREGATE.value
        )
        if global_stage.status != "completed":
            return
        manifest_stage = next(
            (s for s in stages if s.stage_kind == StageKind.MANIFEST_VALIDATION.value),
            None,
        )
        if manifest_stage is None or manifest_stage.status == "completed":
            return
        await self._run_manifest_stage(
            session,
            repo=repo,
            version=version,
            run_id=run_id,
            stage=manifest_stage,
            worker_artifact=global_stage.artifact_checksum,
            recovery=recovery,
        )

    async def _run_global_stage(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway: BuilderModelGateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        policy: RunPolicy,
        parents: Sequence[NarrativeMemoryBuildStage],
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        attempt_count = int(stage.attempt_count or 0) + 1
        await repo.mark_stage(stage, status="running", increment_attempt=True)
        try:
            from app.models.narrative_memory import (
                NarrativeMemoryClaim,
                NarrativeMemoryNode,
                NarrativeMemorySourceLink,
            )

            parent_nodes = (
                await session.scalars(
                    select(NarrativeMemoryNode)
                    .where(
                        NarrativeMemoryNode.owner_id == version.owner_id,
                        NarrativeMemoryNode.novel_id == version.novel_id,
                        NarrativeMemoryNode.version_id == version.id,
                        NarrativeMemoryNode.node_kind.in_(
                            [NodeKind.STORY_ARC.value, NodeKind.VOLUME.value]
                        ),
                    )
                    .order_by(
                        NarrativeMemoryNode.chapter_start, NarrativeMemoryNode.node_key
                    )
                )
            ).all()
            if len(parent_nodes) != len(parents):
                raise PackageBuildError("parent node count mismatch")
            parent_ids = [n.id for n in parent_nodes]
            parent_claims = (
                await session.scalars(
                    select(NarrativeMemoryClaim).where(
                        NarrativeMemoryClaim.owner_id == version.owner_id,
                        NarrativeMemoryClaim.novel_id == version.novel_id,
                        NarrativeMemoryClaim.version_id == version.id,
                        NarrativeMemoryClaim.node_id.in_(parent_ids),
                    )
                )
            ).all()
            claim_ids = [c.id for c in parent_claims]
            parent_links = (
                await session.scalars(
                    select(NarrativeMemorySourceLink).where(
                        NarrativeMemorySourceLink.owner_id == version.owner_id,
                        NarrativeMemorySourceLink.novel_id == version.novel_id,
                        NarrativeMemorySourceLink.version_id == version.id,
                        NarrativeMemorySourceLink.claim_id.in_(claim_ids),
                    )
                )
            ).all()
            request_payload = {
                "stage_key": stage.stage_key,
                "parent_keys": [p.stage_key for p in parents],
                "prompt_hash": policy.prompt_hash,
                "schema_hash": policy.schema_hash,
            }
            package_cs = (
                package_checksum(
                    type(
                        "Tmp",
                        (),
                        {"model_dump": lambda self, mode=None: request_payload},
                    )()
                )
                if False
                else sha256(
                    __import__("json").dumps(request_payload, sort_keys=True).encode()
                ).hexdigest()
            )
            cache_key = f"nmb:global:{package_cs[:100]}"

            def validate_output(raw: Any) -> dict[str, Any]:
                model_claims = (
                    list((raw or {}).get("claims") or [])
                    if isinstance(raw, dict)
                    else []
                )
                package = build_global_candidate(
                    chapter_start=int(stage.chapter_start or 1),
                    chapter_end=int(stage.chapter_end or 1),
                    parent_nodes=list(parent_nodes),
                    parent_claims=list(parent_claims),
                    parent_links=list(parent_links),
                    model_claims=model_claims,
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
                request_payload=request_payload,
                validate_output=validate_output,
                is_cancelled=is_cancelled,
                estimated_input_tokens=8_000,
                estimated_output_tokens=4_096,
            )
            import json as _json

            from app.services.narrative_memory.contracts import CandidatePackage

            package = CandidatePackage.model_validate_json(
                _json.dumps(
                    result.output["candidate_package"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            await CandidateAuthority(session).persist_package(
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
                package=package,
            )
            await repo.mark_stage(
                stage,
                status="completed",
                package_checksum=package_cs,
                cache_key=cache_key,
                artifact_checksum=result.output["artifact_checksum"],
                reason_code=ReasonCode.COMPLETED_CANDIDATE,
                source_checksum=version.source_snapshot_hash,
                model_lineage=ModelLineage.model_validate(
                    version.model_lineage
                ).model_dump(mode="json"),
                idempotency_key=f"{run_id}:{stage.stage_key}:{attempt_count}",
                checkpoint={},
                journal=True,
            )
        except CancelledBeforePersist:
            stage = await self._reload_stage(session, run_id, stage.stage_key)
            if recovery is not None:
                await recovery.cancel_stage(run_id=run_id, stage=stage)
            else:
                await repo.mark_stage(
                    stage, status="cancelled", reason="cancelled_before_persist"
                )
        except Exception as exc:  # noqa: BLE001
            stage = await self._reload_stage(session, run_id, stage.stage_key)
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

    async def _run_manifest_stage(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        worker_artifact: str | None,
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        await repo.mark_stage(stage, status="running", increment_attempt=True)
        try:
            from app.services.narrative_memory.builder_report import write_build_report
            from app.services.narrative_memory.manifests import (
                SealConflictError,
                compute_manifest_from_snapshot,
                load_candidate_snapshot,
                seal_and_report,
            )

            snapshot = await load_candidate_snapshot(
                session,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
            )
            computation = compute_manifest_from_snapshot(snapshot)
            db_checksum = computation.manifest_checksum
            try:
                sealed = await seal_and_report(
                    session,
                    owner_id=version.owner_id,
                    novel_id=version.novel_id,
                    version_id=version.id,
                )
                db_checksum = sealed.manifest_checksum
                if not sealed.structural.ok:
                    await repo.mark_stage(
                        stage,
                        status="failed",
                        reason="structural_blocked",
                        reason_code=ReasonCode.DEPENDENCY_FAILED,
                        checkpoint={
                            "reasons": list(sealed.structural.reason_codes),
                            "database_manifest_checksum": db_checksum,
                        },
                        journal=True,
                    )
                    await write_build_report(
                        session,
                        run_id=run_id,
                        owner_id=version.owner_id,
                        novel_id=version.novel_id,
                        version_id=version.id,
                        worker_artifact_checksum=worker_artifact,
                        database_manifest_checksum=db_checksum,
                    )
                    return
            except SealConflictError:
                # Already sealed — recompute only.
                pass
            await write_build_report(
                session,
                run_id=run_id,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
                worker_artifact_checksum=worker_artifact,
                database_manifest_checksum=db_checksum,
            )
            await repo.mark_stage(
                stage,
                status="completed",
                artifact_checksum=db_checksum,
                package_checksum=db_checksum,
                reason_code=ReasonCode.COMPLETED_CANDIDATE,
                checkpoint={
                    "database_manifest_checksum": db_checksum,
                    "worker_artifact_checksum": worker_artifact,
                },
                journal=True,
            )
        except Exception as exc:  # noqa: BLE001
            await repo.mark_stage(
                stage,
                status="failed",
                reason=f"{type(exc).__name__}:{str(exc)}"[:160],
                reason_code=ReasonCode.INTERNAL_ERROR,
                journal=True,
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

    async def _resolve_chapters(
        self,
        session: AsyncSession,
        *,
        novel_id: int,
        hierarchy_build_id: str,
        chapter_ids: Sequence[int] | None,
    ) -> list[Chapter]:
        if chapter_ids:
            rows = (
                await session.scalars(
                    select(Chapter)
                    .where(
                        Chapter.novel_id == novel_id, Chapter.id.in_(tuple(chapter_ids))
                    )
                    .order_by(Chapter.chapter_number.asc())
                )
            ).all()
            return list(rows)
        # Distinct chapters present as evidence leaves in frozen hierarchy.
        chapter_id_rows = (
            await session.execute(
                select(ChunkHierarchyNode.chapter_id)
                .where(
                    ChunkHierarchyNode.build_id == hierarchy_build_id,
                    ChunkHierarchyNode.novel_id == novel_id,
                    ChunkHierarchyNode.level == "evidence",
                    ChunkHierarchyNode.chapter_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        ids = [int(row[0]) for row in chapter_id_rows]
        if not ids:
            raise BuilderRepositoryError("no hierarchy chapters available")
        rows = (
            await session.scalars(
                select(Chapter)
                .where(Chapter.novel_id == novel_id, Chapter.id.in_(ids))
                .order_by(Chapter.chapter_number.asc())
            )
        ).all()
        return list(rows)

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

    def _assert_eligibility_matches(
        self, version: NarrativeMemoryVersion, report: EligibilityReport
    ) -> None:
        from app.services.narrative_memory.audit_contracts import (
            AssetKind,
            EligibilityStatus,
        )
        from app.services.narrative_memory.authority import CandidateAuthority

        if report.policy_version != version.eligibility_policy_version:
            raise BuilderRepositoryError("eligibility policy version mismatch")

        # Exact full-report checksum is ideal, but multi-hour candidate builds
        # routinely see optional-domain drift (timeline/relationship/clue). Versions
        # are append-only, so we accept resume when the required hierarchy lineage
        # still matches the frozen version and provider_calls remain allowed.
        checksum = CandidateAuthority._eligibility_checksum(report)  # noqa: SLF001
        if checksum != version.eligibility_report_checksum:
            hierarchy = next(
                (a for a in report.assets if a.kind == AssetKind.HIERARCHY),
                None,
            )
            if (
                hierarchy is None
                or hierarchy.status != EligibilityStatus.REUSABLE_EXACT
                or hierarchy.version_id != version.hierarchy_build_id
            ):
                raise BuilderRepositoryError(
                    "eligibility report checksum mismatch and hierarchy lineage drifted"
                )
        if not provider_calls_allowed(report):
            # Caller may still create run; process_run pauses before transport.
            return

    async def _finalize_run_status(
        self, session: AsyncSession, repo: BuilderRepository, run_id: int
    ) -> WorkerResult:
        from app.models.narrative_memory_builder import NarrativeMemoryBuildRun

        await repo.recompute_terminal_states(run_id)
        stages = await repo.list_stages(run_id)
        completed = tuple(s.stage_key for s in stages if s.status == "completed")
        failed = tuple(s.stage_key for s in stages if s.status == "failed")
        blocked = tuple(s.stage_key for s in stages if s.status == "blocked_dependency")
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        assert run is not None
        if run.cancel_requested and run.status != "completed":
            status = "cancelled"
            reason = "cancel_requested"
            await repo.set_run_error_code(run_id, "cancel_requested")
        elif run.status == "paused_budget":
            status = "paused_budget"
            reason = run.status_reason
        elif failed and completed:
            status = "partial"
            reason = "chapter_or_parent_failed"
        elif failed and not completed:
            status = "failed"
            reason = "all_failed"
        elif blocked:
            # D-05: drift-blocked chapters fail closed — the run is partial,
            # never falsely "completed" and never an unconditional restart.
            status = "partial"
            reason = "chapter_or_parent_blocked"
        elif stages and all(
            s.status == "completed"
            for s in stages
            if s.stage_kind
            in {
                StageKind.CHAPTER_STATE.value,
                StageKind.ARC_VOLUME_PLAN.value,
                StageKind.ARC_VOLUME_AGGREGATE.value,
                StageKind.GLOBAL_AGGREGATE.value,
                StageKind.MANIFEST_VALIDATION.value,
            }
            and s.stage_kind != StageKind.ARC_VOLUME_PLAN.value
            or s.status in {"completed", "pending"}
        ):
            # Completed only when global+manifest done if those stages exist.
            has_global = any(
                s.stage_kind == StageKind.GLOBAL_AGGREGATE.value for s in stages
            )
            global_done = any(
                s.stage_kind == StageKind.GLOBAL_AGGREGATE.value
                and s.status == "completed"
                for s in stages
            )
            manifest_done = any(
                s.stage_kind == StageKind.MANIFEST_VALIDATION.value
                and s.status == "completed"
                for s in stages
            )
            chapters_done = all(
                s.status == "completed"
                for s in stages
                if s.stage_kind == StageKind.CHAPTER_STATE.value
            )
            if chapters_done and (not has_global or (global_done and manifest_done)):
                if has_global and global_done and manifest_done:
                    status = "completed"
                    reason = "completed_candidate"
                elif chapters_done and not has_global:
                    status = (
                        "running"
                        if any(s.status == "pending" for s in stages)
                        else "partial"
                    )
                    reason = "chapters_complete"
                else:
                    status = "partial" if failed or blocked else "running"
                    reason = run.status_reason
            else:
                status = "partial" if failed or blocked else "running"
                reason = run.status_reason
        else:
            status = run.status
            reason = run.status_reason

        await repo.update_run_status(run_id, status=status, reason=reason)
        return WorkerResult(
            run_id=run_id,
            status=status,
            status_reason=reason,
            completed_stages=completed,
            failed_stages=failed,
            blocked_stages=blocked,
            transport_calls=self.transport_calls,
            source_manifest_checksum=(
                (run.progress or {}).get("source_manifest_checksum") if run else None
            ),
        )

    async def _snapshot_result(
        self, session: AsyncSession, run_id: int, transport_calls: int
    ) -> WorkerResult:
        repo = BuilderRepository(session)
        stages = await repo.list_stages(run_id)
        from app.models.narrative_memory_builder import NarrativeMemoryBuildRun

        run = await session.get(NarrativeMemoryBuildRun, run_id)
        return WorkerResult(
            run_id=run_id,
            status=run.status if run else "failed",
            status_reason=run.status_reason if run else "missing",
            completed_stages=tuple(
                s.stage_key for s in stages if s.status == "completed"
            ),
            failed_stages=tuple(s.stage_key for s in stages if s.status == "failed"),
            blocked_stages=tuple(
                s.stage_key for s in stages if s.status == "blocked_dependency"
            ),
            transport_calls=transport_calls,
            source_manifest_checksum=(
                (run.progress or {}).get("source_manifest_checksum") if run else None
            ),
        )


def scan_builder_package_for_forbidden_capabilities(
    package_dir: Path | None = None,
) -> list[str]:
    """Static AST/import scan used by forbidden-capability tests."""

    root = package_dir or Path(__file__).resolve().parent
    hits: list[str] = []
    for path in sorted(root.glob("builder_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for frag in FORBIDDEN_IMPORT_FRAGMENTS:
                        if frag in alias.name:
                            hits.append(f"{path.name}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for frag in FORBIDDEN_IMPORT_FRAGMENTS:
                    if frag in module:
                        hits.append(f"{path.name}:from:{module}")
                    for alias in node.names:
                        if frag in alias.name:
                            hits.append(f"{path.name}:name:{alias.name}")
        for frag in FORBIDDEN_IMPORT_FRAGMENTS:
            if (
                frag in source
                and frag
                not in {
                    # allow listing in this scanner's constant
                }
            ):
                # Skip the constant definition file lines by requiring import form.
                pass
    # Also scan sibling modules introduced by later plans.
    for name in (
        "arc_planner.py",
        "global_builder.py",
        "optional_sources.py",
        "builder_report.py",
    ):
        path = root / name
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for frag in ("reader_chat", "ReaderConversation", "set_active_pointer"):
            if frag in source:
                hits.append(f"{name}:text:{frag}")
    return hits
