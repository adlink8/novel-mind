"""Hierarchy-assembly mixin for the narrative-memory builder worker.

Extracted from ``builder_worker.py`` (Phase 28-03 candidate hierarchy): this
mixin owns the boundary plan + arc-volume aggregate + global-aggregate seams.
It derives arc boundaries from the chapter plan (arc planner), registers the
parent/global/manifest stage specs, runs each arc aggregate and the global
aggregate through the model gateway with deterministic checksums, and
orchestrates manifest validation once the global stage completes. Outputs are
immutable candidate-only nodes/claims — never canon, never a pointer/promotion/
cutover write path (D-06/D-07/D-09).
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.builder_budget import BudgetExceeded, UnknownPricing
from app.services.narrative_memory.builder_contracts import (
    ReasonCode,
    StageKind,
    package_checksum,
)
from app.services.narrative_memory.builder_gateway import CancelledBeforePersist
from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    artifact_checksum_for_package,
    build_arc_volume_candidate,
    build_global_candidate,
    load_child_chapter_authority,
)
from app.services.narrative_memory.builder_repository import (
    BuilderRepository,
)
from app.services.narrative_memory.contracts import ModelLineage, NodeKind
from app.services.narrative_memory.recovery import RecoveryCoordinator


class HierarchyWorkerMixin:
    """Boundary plan + arc/global candidate assembly (see module docstring)."""

    async def _ensure_boundary_and_parents(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        gateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        policy,
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
        gateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        policy,
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
        gateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        policy,
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
        gateway,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        policy,
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
