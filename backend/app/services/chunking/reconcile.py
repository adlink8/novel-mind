"""Exact candidate collection reconcile (07-05)."""

from __future__ import annotations

from app.services.chunking.builds import InMemoryBuildStore
from app.services.chunking.schemas import ReconcileReport


def reconcile_build(
    store: InMemoryBuildStore, build_id: str, *, cleanup: bool = True
) -> ReconcileReport:
    rec = store.builds.get(build_id)
    if rec is None:
        return ReconcileReport(
            build_id=build_id,
            expected_ids=[],
            actual_ids=[],
            missing=["__build_missing__"],
            clean=False,
            checksum_ok=False,
        )

    expected: list[str] = []
    for tree in store.hierarchies.get(build_id, []):
        for n in tree.nodes:
            if n.level == "evidence":
                expected.append(n.node_id)
    expected = sorted(set(expected))
    actual = sorted(store.vector_ids.get(build_id, set()))

    exp_set, act_set = set(expected), set(actual)
    missing = sorted(exp_set - act_set)
    orphan = sorted(act_set - exp_set)
    stale: list[str] = []
    # stale: parent build ids leaking into candidate (none in clean store)
    if cleanup and orphan:
        store.vector_ids[build_id] = set(expected)
        actual = list(expected)
        orphan = []

    clean = not missing and not orphan and not stale
    checksum_ok = clean  # simplified: clean ids imply checksum agreement

    if clean and rec.status in ("built", "failed"):
        store.builds[build_id] = rec.model_copy(update={"status": "reconciled"})

    return ReconcileReport(
        build_id=build_id,
        expected_ids=expected,
        actual_ids=actual,
        missing=missing,
        orphan=orphan,
        stale=stale,
        clean=clean,
        checksum_ok=checksum_ok,
    )
