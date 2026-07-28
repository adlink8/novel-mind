"""Delta planner for incremental candidate rebuilds (07-05)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IncrementalDelta:
    full: bool
    no_op: bool
    changed_chapter_ids: list[int]
    reason: str


def plan_incremental_delta(
    *,
    prev_chapter_hashes: dict[int, str],
    current_chapter_hashes: dict[int, str],
    prev_chunker_lineage: dict[str, str] | None,
    current_chunker_lineage: dict[str, str],
    force_full: bool = False,
) -> IncrementalDelta:
    """Compare chapter content hashes and chunker lineage.

    - force_full / missing parent / chunker lineage change affecting all → full
    - only changed chapters listed otherwise
    - identical hashes + lineage → no_op (zero LLM / index writes expected)
    """
    if force_full or not prev_chapter_hashes:
        return IncrementalDelta(
            full=True,
            no_op=False,
            changed_chapter_ids=sorted(current_chapter_hashes.keys()),
            reason="full_rebuild",
        )

    if prev_chunker_lineage != current_chunker_lineage:
        return IncrementalDelta(
            full=True,
            no_op=False,
            changed_chapter_ids=sorted(current_chapter_hashes.keys()),
            reason="chunker_lineage_changed",
        )

    changed: list[int] = []
    for cid, h in current_chapter_hashes.items():
        if prev_chapter_hashes.get(cid) != h:
            changed.append(cid)
    # deleted chapters (in prev not in current) also mark change scope
    for cid in prev_chapter_hashes:
        if cid not in current_chapter_hashes:
            changed.append(cid)

    changed = sorted(set(changed))
    if not changed:
        return IncrementalDelta(
            full=False, no_op=True, changed_chapter_ids=[], reason="no_change"
        )
    return IncrementalDelta(
        full=False,
        no_op=False,
        changed_chapter_ids=changed,
        reason="chapter_hash_delta",
    )
