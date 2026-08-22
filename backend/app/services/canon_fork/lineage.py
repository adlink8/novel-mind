"""Frozen citation lineage for a Canon Fork (Phase 35-02, D-35-03).

A fork's citation lineage seals the source-leaf provenance the derivative is
bound to. Every leaf record binds ``leaf_key`` + ``chapter_number`` +
``content_hash`` to the frozen ``source_snapshot_hash``; any leaf that does not
replay from the sealed snapshot fails closed before the fork can be materialized
(no silent citation drift).

This module is pure and deterministic (no database, no write path).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

CANON_FORK_SCHEMA_VERSION = "canon-fork.v1"
CANON_FORK_LINEAGE_PREFIX = "canon-fork.v1:lineage"

FORK_LEAF_NAMESPACE = "original:chapters"


@dataclass(frozen=True)
class ForkLeafCitation:
    """One frozen source leaf citation anchored on the fork's snapshot."""

    leaf_key: str
    chapter_number: int
    content_hash: str
    source_snapshot_hash: str

    def to_payload(self) -> dict[str, str | int]:
        return {
            "leaf_key": self.leaf_key,
            "chapter_number": self.chapter_number,
            "content_hash": self.content_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
        }


def canonical_lineage_hash(payload: dict) -> str:
    """Byte-replayable canonical hash for fork lineage records."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(f"{CANON_FORK_LINEAGE_PREFIX}\n{encoded}".encode("utf-8")).hexdigest()


def leaf_citation_hash(
    *, chapter_number: int, content_hash: str, source_snapshot_hash: str
) -> str:
    """Deterministic identity of one frozen source leaf citation."""
    return canonical_lineage_hash(
        {
            "kind": f"{CANON_FORK_SCHEMA_VERSION}:leaf",
            "chapter_number": chapter_number,
            "content_hash": content_hash,
            "source_snapshot_hash": source_snapshot_hash,
        }
    )


def build_leaf_lineage(
    *,
    source_snapshot_hash: str,
    chapter_numbers: list[int],
    content_hashes: dict[int, str],
    through_chapter: int,
) -> tuple[ForkLeafCitation, ...]:
    """Freeze the source leaves visible at the cutoff, oldest chapter first.

    Only leaves at or below the server-derived cutoff are sealed; a future leaf
    can never enter the lineage (cutoff-scoped, T-35-02-02).
    """
    leaves: list[ForkLeafCitation] = []
    for chapter_number in sorted(chapter_numbers):
        if chapter_number > through_chapter:
            continue
        content_hash = content_hashes.get(chapter_number)
        if not content_hash:
            raise ValueError(
                f"missing content hash for chapter {chapter_number}: "
                "lineage cannot freeze a leaf without its snapshot hash"
            )
        leaves.append(
            ForkLeafCitation(
                leaf_key=f"chapter:{chapter_number}",
                chapter_number=chapter_number,
                content_hash=content_hash,
                source_snapshot_hash=source_snapshot_hash,
            )
        )
    return tuple(leaves)


def validate_leaf_lineage(
    lineage: list[dict],
    *,
    source_snapshot_hash: str,
    through_chapter: int,
) -> None:
    """Fail closed on stale, missing or out-of-cutoff leaves.

    Every leaf must replay from the sealed source snapshot, stay at or below the
    cutoff and carry a 64-hex content hash. A drift fails closed with a stable
    reason before the fork is consumed.
    """
    if not lineage:
        raise ValueError(
            "empty citation lineage: a fork must seal at least one source leaf"
        )
    for leaf in lineage:
        if leaf.get("source_snapshot_hash") != source_snapshot_hash:
            raise ValueError(
                "stale_citation_leaf: leaf source_snapshot_hash does not match "
                "the frozen fork snapshot"
            )
        chapter_number = leaf.get("chapter_number")
        if not isinstance(chapter_number, int) or chapter_number < 1:
            raise ValueError(
                "invalid_citation_leaf: chapter_number must be a positive integer"
            )
        if chapter_number > through_chapter:
            raise ValueError(
                "beyond_cutoff_leaf: citation lineage includes a leaf past the "
                "server-derived cutoff"
            )
        content_hash = leaf.get("content_hash")
        if not isinstance(content_hash, str) or len(content_hash) != 64:
            raise ValueError("invalid_citation_leaf: content_hash must be 64-hex")
        leaf_key = leaf.get("leaf_key")
        if not leaf_key or str(leaf_key) != f"chapter:{chapter_number}":
            raise ValueError(
                "invalid_citation_leaf: leaf_key does not match chapter_number"
            )


def lineage_payload(lineage: list[dict] | tuple[ForkLeafCitation, ...]) -> list[dict]:
    """Normalize frozen lineage records to plain JSON payloads."""
    if lineage and isinstance(lineage[0], ForkLeafCitation):
        return [leaf.to_payload() for leaf in lineage]  # type: ignore[union-attr]
    return [dict(record) for record in lineage]


def lineage_hash(lineage: list[dict]) -> str:
    """Deterministic hash of the normalized lineage payload."""
    return canonical_lineage_hash({"lineage": lineage})


__all__ = [
    "CANON_FORK_LINEAGE_PREFIX",
    "CANON_FORK_SCHEMA_VERSION",
    "FORK_LEAF_NAMESPACE",
    "ForkLeafCitation",
    "build_leaf_lineage",
    "canonical_lineage_hash",
    "leaf_citation_hash",
    "lineage_hash",
    "lineage_payload",
    "validate_leaf_lineage",
]
