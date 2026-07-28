"""Fresh PostgreSQL observer for Phase 17 qualification independence.

Recomputes lineage and production pointer digests; never trusts runner JSON alone.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import (
    NarrativeMemoryManifest,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.services.narrative_memory.qualification_contracts import (
    QualificationFixture,
    QualificationPolicy,
    stable_checksum,
)

# Explicit production selector tables that must be byte-equal before/after.
PRODUCTION_POINTER_TABLES = (
    "chunk_active_pointers",
    "timeline_active_pointers",
    "clue_active_pointers",
    "active_baselines",
    "narrative_active_pointers",
)

# Tables that look like selectors but are never narrative-memory production.
EXCLUDED_FROM_POINTER_SCAN = frozenset(
    {
        "narrative_memory_qualification_runs",
        "narrative_memory_qualification_case_results",
        "narrative_memory_qualification_reports",
        "narrative_memory_versions",
        "narrative_memory_nodes",
        "narrative_memory_claims",
        "narrative_memory_edges",
        "narrative_memory_source_links",
        "narrative_memory_manifests",
        "narrative_memory_validation_reports",
        "narrative_memory_build_runs",
        "narrative_memory_build_stages",
        "narrative_memory_build_budget_ledgers",
        "narrative_memory_build_budget_reservations",
        "narrative_memory_build_model_call_attempts",
        "narrative_memory_build_reports",
        "narrative_memory_rebuild_plans",
        "narrative_memory_rebuild_items",
        "narrative_memory_reuse_reports",
    }
)

SELECTOR_NAME_FRAGMENTS = (
    "active_pointer",
    "active_baseline",
    "current_version",
    "promotion_journal",
    "pointer_journal",
)


class VerifierResult:
    def __init__(
        self,
        *,
        ok: bool,
        reasons: list[str],
        pointer_digest: str,
        verifier_checksum: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.ok = ok
        self.reasons = tuple(sorted(set(reasons)))
        self.pointer_digest = pointer_digest
        self.verifier_checksum = verifier_checksum
        self.details = details or {}


async def snapshot_production_pointers(session: AsyncSession) -> dict[str, Any]:
    """Canonical complete-row snapshot of production selection authorities."""

    def _sync_snapshot(sync_sess) -> dict[str, Any]:
        insp = inspect(sync_sess.get_bind())
        tables = set(insp.get_table_names())
        snap: dict[str, Any] = {}
        unknown_selectors: list[str] = []
        for name in sorted(tables):
            if name in EXCLUDED_FROM_POINTER_SCAN:
                continue
            lower = name.lower()
            is_known = name in PRODUCTION_POINTER_TABLES
            looks_like = any(frag in lower for frag in SELECTOR_NAME_FRAGMENTS)
            if looks_like and not is_known and "narrative_memory" in lower:
                unknown_selectors.append(name)
                continue
            if not is_known and not looks_like:
                continue
            rows = (
                sync_sess.execute(text(f'SELECT * FROM "{name}" ORDER BY 1'))
                .mappings()
                .all()
            )
            serial = []
            for row in rows:
                item = {}
                for k, v in dict(row).items():
                    if hasattr(v, "isoformat"):
                        item[k] = v.isoformat()
                    else:
                        item[k] = v
                serial.append(item)
            snap[name] = serial
        if unknown_selectors:
            snap["__unknown_selectors__"] = unknown_selectors
        return snap

    return await session.run_sync(_sync_snapshot)


def pointer_digest(snapshot: dict[str, Any]) -> str:
    return stable_checksum(snapshot)


async def recompute_phase_lineage(
    session: AsyncSession,
    fixture: QualificationFixture,
) -> list[str]:
    """Fresh re-derive of Phase 13 version/manifest and Phase 14 build complete."""
    reasons: list[str] = []
    version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == fixture.owner_id,
            NarrativeMemoryVersion.novel_id == fixture.novel_id,
            NarrativeMemoryVersion.id == fixture.version_id,
        )
    )
    if version is None:
        reasons.append("version_missing")
        return reasons
    if version.source_snapshot_hash != fixture.source_snapshot_hash:
        reasons.append("source_snapshot_mismatch")
    if version.hierarchy_build_id != fixture.hierarchy_build_id:
        reasons.append("hierarchy_build_mismatch")
    if version.hierarchy_checksum != fixture.hierarchy_checksum:
        reasons.append("hierarchy_checksum_mismatch")

    manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == fixture.owner_id,
            NarrativeMemoryManifest.novel_id == fixture.novel_id,
            NarrativeMemoryManifest.version_id == fixture.version_id,
            NarrativeMemoryManifest.manifest_checksum
            == fixture.candidate_manifest_checksum,
        )
    )
    if manifest is None:
        reasons.append("manifest_missing_or_mismatch")

    run = await session.scalar(
        select(NarrativeMemoryBuildRun).where(
            NarrativeMemoryBuildRun.owner_id == fixture.owner_id,
            NarrativeMemoryBuildRun.novel_id == fixture.novel_id,
            NarrativeMemoryBuildRun.version_id == fixture.version_id,
            NarrativeMemoryBuildRun.status == "completed",
        )
    )
    if run is None:
        reasons.append("build_incomplete")

    # no narrative-memory active pointer table
    def _list_tables(sync_sess) -> set[str]:
        return set(inspect(sync_sess.bind).get_table_names())

    tables = await session.run_sync(_list_tables)
    for t in tables:
        if t.startswith("narrative_memory") and (
            "active_pointer" in t or "promotion" in t or "current" in t
        ):
            reasons.append(f"illegal_nm_selector_table:{t}")
    return reasons


async def verify_qualification(
    session: AsyncSession,
    *,
    fixture: QualificationFixture,
    policy: QualificationPolicy,
    pointer_before: dict[str, Any],
    pointer_after: dict[str, Any] | None = None,
    require_version_rows: bool = True,
) -> VerifierResult:
    """Independent observer. Opens nothing itself — caller provides fresh session."""
    reasons: list[str] = []

    if pointer_before.get("__unknown_selectors__"):
        reasons.append("unknown_selector_authority")

    before_digest = pointer_digest(pointer_before)
    after_snap = pointer_after if pointer_after is not None else pointer_before
    after_digest = pointer_digest(after_snap)
    if before_digest != after_digest:
        reasons.append("pointer_before_after_mismatch")

    if require_version_rows:
        reasons.extend(await recompute_phase_lineage(session, fixture))

    # fixture/policy integrity
    if not fixture.checksum() or not policy.checksum():
        reasons.append("fixture_policy_hash_empty")

    body = {
        "fixture_checksum": fixture.checksum(),
        "policy_checksum": policy.checksum(),
        "pointer_before": before_digest,
        "pointer_after": after_digest,
        "reasons": sorted(set(reasons)),
    }
    v_cs = stable_checksum(body)
    ok = not reasons
    return VerifierResult(
        ok=ok,
        reasons=list(reasons),
        pointer_digest=after_digest,
        verifier_checksum=v_cs,
        details={"pointer_before_digest": before_digest},
    )


def verifier_has_repair_capability() -> bool:
    return False


def verifier_has_promotion_capability() -> bool:
    return False
