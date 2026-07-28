"""Content-hash delta planning and zero-write incremental refresh."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeRelationCandidate
from app.models.knowledge_unit import (
    NarrativeRefreshRun,
    NarrativeIndexBuild,
    NarrativeSourceSnapshot,
    NarrativeSourceSnapshotItem,
    NarrativeSourceWatermark,
    NarrativeUnit,
)
from app.services.knowledge_units.materialize import stable_hash
from app.services.knowledge_units.materialize import narrative_unit_materializer
from app.services.knowledge_units.canonicalize import narrative_canonicalizer
from app.services.knowledge_units.indexing import narrative_indexing_service


async def complete_refresh(
    db: AsyncSession,
    *,
    plan: DeltaPlan,
    owner_id: int,
    novel_id: int,
    domain_profile: str,
    approved_by: str,
    evidence_secret: str,
    fixture_path: str,
    indexing_service=None,
    retrieve=None,
    store=None,
    stage_hook: Callable[[str, int], Awaitable[None]] | None = None,
) -> RefreshReport:
    """Durable state machine; every completed stage survives a new DB session."""
    from app.services.vector_store import vector_store
    from app.services.knowledge_units.eval import (
        candidate_retriever,
        evaluate_candidate,
        load_fixture,
    )
    from app.services.knowledge_units.promotion import narrative_promotion_service
    from app.services.knowledge_units.reconcile import (
        read_actual_collection,
        reconcile_build,
    )
    from app.services.knowledge_units.rollback import (
        advance_watermark,
        collection_checkpoint_probe,
        rollback_journal,
    )

    chosen_store = store or vector_store
    try:
        report = await rebuild_affected_candidate(
            db,
            plan=plan,
            owner_id=owner_id,
            novel_id=novel_id,
            domain_profile=domain_profile,
        )
        if report.status == "no_change":
            return report
        run = await db.get(NarrativeRefreshRun, report.run_id)
        build = await db.get(NarrativeIndexBuild, run.candidate_build_id)
        state = dict(run.delta_manifest or {})
        if not state.get("stage"):
            await _checkpoint(db, run, stage="candidate")
            await _notify(stage_hook, "candidate", run.id)

        service = indexing_service or narrative_indexing_service
        if _stage_before(run, "indexed"):
            if build.status != "candidate" or not build.collection_name:
                await service.build_candidate(db, build_id=build.id)
            await _checkpoint(db, run, stage="indexed", build_id=build.id)
            await _notify(stage_hook, "indexed", run.id)

        if _stage_before(run, "evaluated"):
            adapter = retrieve or candidate_retriever(build, db=db)
            evaluation = await evaluate_candidate(
                load_fixture(fixture_path),
                build=build,
                retrieve=adapter,
                signing_secret=evidence_secret,
            )
            await _checkpoint(db, run, stage="evaluated", evaluation=evaluation)
            await _notify(stage_hook, "evaluated", run.id)
        else:
            evaluation = run.delta_manifest["evaluation"]

        if _stage_before(run, "promotion_prepared"):
            actual = await read_actual_collection(build, chosen_store)
            before = await reconcile_build(db, build_id=build.id, actual_items=actual)
            reconcile_payload = {
                **{name: getattr(before, name) for name in before.__dataclass_fields__},
                "collection": build.collection_name,
            }
            journal = await narrative_promotion_service.prepare(
                db,
                candidate_build_id=build.id,
                candidate_checksum=build.manifest_checksum,
                eval_reports=[evaluation],
                reconcile_report=reconcile_payload,
                approved_by=approved_by,
                evidence_secret=evidence_secret,
            )
            await _checkpoint(
                db,
                run,
                stage="promotion_prepared",
                journal_id=journal.id,
                pre_reconcile=reconcile_payload,
            )
            await _notify(stage_hook, "promotion_prepared", run.id)
        else:
            from app.models.knowledge_unit import NarrativePromotionJournal

            journal = await db.get(
                NarrativePromotionJournal, run.delta_manifest["journal_id"]
            )

        if _stage_before(run, "promoted"):
            if journal.status == "rolled_back":
                # Resume an interrupted release without restoring its watermark.
                build.status = "candidate"
                journal.status = "prepared"
                await db.flush()
            if journal.status == "prepared":
                await narrative_promotion_service.commit(
                    db,
                    journal_id=journal.id,
                    candidate_checksum=build.manifest_checksum,
                    evidence_secret=evidence_secret,
                )
            await _checkpoint(db, run, stage="promoted")
            await _notify(stage_hook, "promoted", run.id)

        actual = await read_actual_collection(build, chosen_store)
        after = await reconcile_build(db, build_id=build.id, actual_items=actual)
        await _checkpoint(
            db,
            run,
            stage="post_reconciled",
            post_reconcile={
                name: getattr(after, name) for name in after.__dataclass_fields__
            },
        )
        await _notify(stage_hook, "post_reconciled", run.id)
        await advance_watermark(
            db, build_id=build.id, snapshot_id=plan.after_snapshot_id, reconcile=after
        )
        run.status = "committed"
        run.counters = {
            **run.counters,
            "chroma": len(actual),
            "pointer": 1,
            "watermark": 1,
        }
        await _checkpoint(db, run, stage="committed")
        return RefreshReport("committed", run.id, plan, run.counters)
    except Exception as exc:
        await db.rollback()
        run = await db.scalar(
            select(NarrativeRefreshRun).where(
                NarrativeRefreshRun.run_key
                == stable_hash(
                    {
                        "scope": [owner_id, novel_id, domain_profile],
                        "delta": plan.delta_checksum,
                    }
                )[:120]
            )
        )
        if run is None:
            raise
        state = dict(run.delta_manifest or {})
        journal_id = state.get("journal_id")
        if journal_id is not None:
            from app.models.knowledge_unit import NarrativePromotionJournal

            journal = await db.get(NarrativePromotionJournal, journal_id)
            if journal is not None and journal.status == "committed":
                await rollback_journal(
                    db,
                    journal_id=journal.id,
                    collection_probe=collection_checkpoint_probe(chosen_store),
                )
                state["stage"] = "promotion_prepared"
                state["recovery"] = "rolled_back_after_interruption"
        run.status = "failed"
        run.error_detail = f"{type(exc).__name__}: {exc}"
        run.delta_manifest = state
        await db.commit()
        raise


_STAGES = (
    "candidate",
    "indexed",
    "evaluated",
    "promotion_prepared",
    "promoted",
    "post_reconciled",
    "committed",
)


def _stage_before(run: NarrativeRefreshRun, target: str) -> bool:
    current = (run.delta_manifest or {}).get("stage", "candidate")
    return _STAGES.index(current) < _STAGES.index(target)


async def _checkpoint(
    db: AsyncSession, run: NarrativeRefreshRun, *, stage: str, **artifacts: Any
) -> None:
    state = dict(run.delta_manifest or {})
    state.update(artifacts)
    state["stage"] = stage
    run.delta_manifest = state
    run.status = "committed" if stage == "committed" else "candidate"
    run.error_detail = None
    await db.commit()


async def _notify(
    hook: Callable[[str, int], Awaitable[None]] | None, stage: str, run_id: int
) -> None:
    if hook is not None:
        await hook(stage, run_id)


@dataclass(frozen=True, slots=True)
class DeltaPlan:
    before_snapshot_id: int | None
    after_snapshot_id: int
    added: tuple[int, ...]
    changed: tuple[int, ...]
    removed: tuple[int, ...]
    affected_subjects: tuple[str, ...]
    delta_checksum: str

    @property
    def no_change(self) -> bool:
        return not (self.added or self.changed or self.removed)


@dataclass(frozen=True, slots=True)
class RefreshReport:
    status: str
    run_id: int | None
    delta: DeltaPlan
    writes: dict[str, int]


async def prepare_delta(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    domain_profile: str,
    after_snapshot_id: int,
) -> DeltaPlan:
    after = await db.get(NarrativeSourceSnapshot, after_snapshot_id)
    if after is None or (after.owner_id, after.novel_id, after.domain_profile) != (
        owner_id,
        novel_id,
        domain_profile,
    ):
        raise ValueError("after snapshot is outside refresh scope")
    watermark = await db.scalar(
        select(NarrativeSourceWatermark).where(
            NarrativeSourceWatermark.owner_id == owner_id,
            NarrativeSourceWatermark.novel_id == novel_id,
            NarrativeSourceWatermark.domain_profile == domain_profile,
        )
    )
    before_id = watermark.snapshot_id if watermark else None
    after_items = await _items(db, after_snapshot_id)
    before_items = await _items(db, before_id) if before_id else {}
    added = tuple(sorted(set(after_items) - set(before_items)))
    removed = tuple(sorted(set(before_items) - set(after_items)))
    changed = tuple(
        sorted(
            key
            for key in set(after_items) & set(before_items)
            if after_items[key] != before_items[key]
        )
    )
    affected_ids = set(added + changed + removed)
    subjects = (
        set(
            (
                await db.scalars(
                    select(NarrativeUnit.subject_key).where(
                        NarrativeUnit.owner_id == owner_id,
                        NarrativeUnit.novel_id == novel_id,
                        NarrativeUnit.source_judgment_id.in_(affected_ids),
                    )
                )
            ).all()
        )
        if affected_ids
        else set()
    )
    if added or changed:
        candidate_ids = [
            row.source_candidate_id
            for row in (
                await db.scalars(
                    select(NarrativeSourceSnapshotItem).where(
                        NarrativeSourceSnapshotItem.snapshot_id == after_snapshot_id,
                        NarrativeSourceSnapshotItem.source_judgment_id.in_(
                            set(added + changed)
                        ),
                    )
                )
            ).all()
        ]
        candidates = (
            list(
                (
                    await db.scalars(
                        select(KnowledgeRelationCandidate).where(
                            KnowledgeRelationCandidate.id.in_(candidate_ids)
                        )
                    )
                ).all()
            )
            if candidate_ids
            else []
        )
        subjects.update(
            f"{candidate.source_kind}:{candidate.source_id}" for candidate in candidates
        )
    payload = {
        "before": before_id,
        "after": after_snapshot_id,
        "added": added,
        "changed": changed,
        "removed": removed,
        "subjects": sorted(subjects),
    }
    return DeltaPlan(
        before_id,
        after_snapshot_id,
        added,
        changed,
        removed,
        tuple(sorted(subjects)),
        stable_hash(payload),
    )


async def execute_refresh(
    db: AsyncSession,
    *,
    plan: DeltaPlan,
    owner_id: int,
    novel_id: int,
    domain_profile: str,
) -> RefreshReport:
    zero = {"llm": 0, "canonical": 0, "chroma": 0, "pointer": 0, "watermark": 0}
    if plan.no_change:
        return RefreshReport("no_change", None, plan, zero)
    run_key = stable_hash(
        {"scope": [owner_id, novel_id, domain_profile], "delta": plan.delta_checksum}
    )[:120]
    existing = await db.scalar(
        select(NarrativeRefreshRun).where(NarrativeRefreshRun.run_key == run_key)
    )
    if existing is not None:
        return RefreshReport(
            existing.status, existing.id, plan, existing.counters or zero
        )
    run = NarrativeRefreshRun(
        owner_id=owner_id,
        novel_id=novel_id,
        domain_profile=domain_profile,
        run_key=run_key,
        status="prepared",
        before_snapshot_id=plan.before_snapshot_id,
        after_snapshot_id=plan.after_snapshot_id,
        delta_manifest={
            "added": list(plan.added),
            "changed": list(plan.changed),
            "removed": list(plan.removed),
            "checksum": plan.delta_checksum,
        },
        affected_subjects=list(plan.affected_subjects),
        counters=zero,
    )
    db.add(run)
    await db.flush()
    return RefreshReport("prepared", run.id, plan, zero)


async def rebuild_affected_candidate(
    db: AsyncSession,
    *,
    plan: DeltaPlan,
    owner_id: int,
    novel_id: int,
    domain_profile: str,
) -> RefreshReport:
    """Rebuild changed subjects while carrying forward unaffected canonical units."""
    prepared = await execute_refresh(
        db,
        plan=plan,
        owner_id=owner_id,
        novel_id=novel_id,
        domain_profile=domain_profile,
    )
    if plan.no_change:
        return prepared
    existing_run = await db.get(NarrativeRefreshRun, prepared.run_id)
    if existing_run.candidate_build_id is not None:
        return RefreshReport(
            existing_run.status,
            existing_run.id,
            plan,
            existing_run.counters,
        )
    affected_ids = set(plan.changed + plan.removed)
    old_units = (
        list(
            (
                await db.scalars(
                    select(NarrativeUnit).where(
                        NarrativeUnit.owner_id == owner_id,
                        NarrativeUnit.novel_id == novel_id,
                        NarrativeUnit.source_judgment_id.in_(affected_ids),
                        NarrativeUnit.status.in_(("candidate", "active")),
                    )
                )
            ).all()
        )
        if affected_ids
        else []
    )
    for unit in old_units:
        unit.status = "deprecated"
        unit.lifecycle_status = (
            "deleted" if unit.source_judgment_id in set(plan.removed) else "deprecated"
        )
    materialized = await narrative_unit_materializer.materialize_snapshot(
        db,
        snapshot_id=plan.after_snapshot_id,
        judgment_ids=set(plan.added + plan.changed),
    )
    canonical = await narrative_canonicalizer.canonicalize_snapshot(
        db, snapshot_id=plan.after_snapshot_id
    )
    build = await narrative_indexing_service.prepare_build(
        db,
        snapshot_id=plan.after_snapshot_id,
        config={"mode": "incremental", "delta": plan.delta_checksum},
    )
    run = await db.get(NarrativeRefreshRun, prepared.run_id)
    writes = {
        "llm": 0,
        "canonical": materialized.created + canonical.canonicalized + len(old_units),
        "chroma": 0,
        "pointer": 0,
        "watermark": 0,
    }
    run.status = "candidate"
    run.candidate_build_id = build.id
    run.counters = writes
    await db.flush()
    return RefreshReport("candidate", run.id, plan, writes)


async def _items(db: AsyncSession, snapshot_id: int) -> dict[int, str]:
    rows = list(
        (
            await db.scalars(
                select(NarrativeSourceSnapshotItem).where(
                    NarrativeSourceSnapshotItem.snapshot_id == snapshot_id
                )
            )
        ).all()
    )
    return {row.source_judgment_id: row.item_content_hash for row in rows}
