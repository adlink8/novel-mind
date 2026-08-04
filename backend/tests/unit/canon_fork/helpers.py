"""Shared deterministic fixtures for Phase 35-03 canon-fork unit tests."""

from __future__ import annotations

from app.services.canon_fork.contracts import CanonScope, build_scope
from app.services.canon_fork.retrieval import CanonIndexRecord

HEX64 = "a" * 64
HEX64_B = "b" * 64


def _scope(
    space: str = "user_interpretation",
    *,
    owner_id: int = 1,
    novel_id: int = 2,
    namespace: str = "user:1",
    version_key: str = "v1",
    through_chapter: int = 3,
    source_snapshot_hash: str = HEX64,
    cutoff_snapshot_hash: str = HEX64_B,
) -> CanonScope:
    return build_scope(
        owner_id=owner_id,
        novel_id=novel_id,
        space=space,
        namespace=namespace,
        version_key=version_key,
        source_snapshot_hash=source_snapshot_hash,
        through_chapter=through_chapter,
        cutoff_snapshot_hash=cutoff_snapshot_hash,
    )


def _record(
    key: str = "original:chapter:1",
    *,
    chapter: int = 1,
    hash_: str = HEX64,
    snapshot: str = HEX64,
    artifact_id: int | None = None,
    namespace: str = "",
    version_key: str = "",
) -> CanonIndexRecord:
    return CanonIndexRecord(
        candidate_key=key,
        chapter_number=chapter,
        content_hash=hash_,
        source_snapshot_hash=snapshot,
        artifact_id=artifact_id,
        namespace=namespace,
        version_key=version_key,
    )
