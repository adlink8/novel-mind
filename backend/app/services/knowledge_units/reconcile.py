"""Exact PostgreSQL manifest versus actual Chroma ID reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import NarrativeIndexBuild, NarrativeUnit


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    build_id: int
    expected: tuple[str, ...]
    actual: tuple[str, ...]
    missing: tuple[str, ...]
    orphan: tuple[str, ...]
    duplicate: tuple[str, ...]
    wrong_owner: tuple[str, ...]
    deleted: tuple[str, ...]
    deprecated: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not any((self.missing, self.orphan, self.duplicate, self.wrong_owner, self.deleted, self.deprecated))


async def reconcile_build(db: AsyncSession, *, build_id: int, actual_items: list[dict[str, Any]]) -> ReconcileReport:
    build = await db.get(NarrativeIndexBuild, build_id)
    if build is None:
        raise ValueError("build not found")
    units = list((await db.scalars(select(NarrativeUnit).where(NarrativeUnit.owner_id == build.owner_id, NarrativeUnit.novel_id == build.novel_id, NarrativeUnit.domain_profile == build.domain_profile, NarrativeUnit.unit_stage == "canonical").order_by(NarrativeUnit.canonical_id, NarrativeUnit.id))).all())
    expected = tuple(f"unit_{unit.canonical_id}_{unit.id}" for unit in units if unit.status in {"candidate", "active"} and unit.lifecycle_status in {"current", "disputed"})
    actual = tuple(str(item["id"]) for item in actual_items)
    duplicates = tuple(sorted({item for item in actual if actual.count(item) > 1}))
    wrong_owner = tuple(sorted(str(item["id"]) for item in actual_items if int(item.get("metadata", {}).get("owner_id", -1)) != build.owner_id or int(item.get("metadata", {}).get("novel_id", -1)) != build.novel_id))
    by_id = {f"unit_{unit.canonical_id}_{unit.id}": unit for unit in units}
    deleted = tuple(sorted(item for item in actual if item in by_id and by_id[item].lifecycle_status == "deleted"))
    deprecated = tuple(sorted(item for item in actual if item in by_id and (by_id[item].lifecycle_status == "deprecated" or by_id[item].status == "deprecated")))
    return ReconcileReport(build.id, tuple(sorted(expected)), tuple(sorted(actual)), tuple(sorted(set(expected) - set(actual))), tuple(sorted(set(actual) - set(expected))), duplicates, wrong_owner, deleted, deprecated)
