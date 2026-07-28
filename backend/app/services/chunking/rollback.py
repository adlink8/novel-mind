"""Joint DB/index/pointer/manifest rollback for chunk builds (07-05)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.chunking.builds import InMemoryBuildStore
from app.services.chunking.reconcile import reconcile_build


def rollback_to_build(
    store: InMemoryBuildStore,
    *,
    novel_id: int,
    target_build_id: str,
) -> dict[str, Any]:
    """Restore active pointer to target and drop candidate residue from failed build."""
    if target_build_id not in store.builds:
        return {"ok": False, "error": "target_build_not_found"}
    target = store.builds[target_build_id]
    if target.novel_id != novel_id:
        return {"ok": False, "error": "novel_mismatch"}

    prev = store.get_active(novel_id)
    store.active[novel_id] = target_build_id

    # Mark any later committed/failed candidates rolled back in journal
    for bid, rec in list(store.builds.items()):
        if rec.novel_id != novel_id or bid == target_build_id:
            continue
        if rec.status in ("committed", "prepared", "built", "failed", "reconciled"):
            journal = list(rec.journal) + [
                {
                    "event": "rolled_back",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "restored_active": target_build_id,
                    "previous_active": prev,
                }
            ]
            store.builds[bid] = rec.model_copy(
                update={
                    "status": "rolled_back",
                    "journal": journal,
                    "is_candidate": True,
                }
            )

    report = reconcile_build(store, target_build_id, cleanup=True)
    return {
        "ok": True,
        "active": target_build_id,
        "previous": prev,
        "reconcile_clean": report.clean,
    }
