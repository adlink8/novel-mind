"""Lineage hash helpers and shared status constants (rag_quality package)."""

from __future__ import annotations

from typing import Any

from app.schemas.eval import (
    INVALID_LINEAGE_REASON,
    LEGACY_INCOMPARABLE_REASON,
    ChunkerLineage,
)
from app.services.rag_fixture import stable_hash

_SHA256_HEX_LEN = 64


def recompute_chunker_config_hash(chunker_config: dict[str, Any] | None) -> str:
    """Canonical config hash — never trust a caller-supplied config hash alone."""
    return stable_hash(chunker_config if isinstance(chunker_config, dict) else {})


def canonicalize_chunker_lineage(
    lineage: ChunkerLineage | dict[str, Any] | None,
    *,
    expected_source_snapshot_hash: str | None = None,
    expected_chunk_manifest_hash: str | None = None,
) -> tuple[ChunkerLineage | None, str | None]:
    """Normalize five-tuple lineage or return (None, reason).

    Reasons:
      - legacy_incomparable: missing / empty (no invented hashes)
      - invalid_lineage: present but malformed or mismatched evidence
    """
    if lineage is None:
        return None, LEGACY_INCOMPARABLE_REASON
    if isinstance(lineage, dict):
        if not lineage:
            return None, LEGACY_INCOMPARABLE_REASON
        try:
            lineage = ChunkerLineage.model_validate(lineage)
        except Exception as exc:
            return None, f"{INVALID_LINEAGE_REASON}: {exc}"

    name = (lineage.chunker_name or "").strip()
    version = (lineage.chunker_version or "").strip()
    if not name or not version:
        return None, LEGACY_INCOMPARABLE_REASON

    cfg = lineage.chunker_config if isinstance(lineage.chunker_config, dict) else {}
    config_hash = recompute_chunker_config_hash(cfg)
    # If caller sent a config hash and it disagrees with recomputed → invalid.
    if lineage.chunker_config_hash and lineage.chunker_config_hash != config_hash:
        return None, f"{INVALID_LINEAGE_REASON}: chunker_config_hash mismatch"

    for label, value in (
        ("chunk_manifest_hash", lineage.chunk_manifest_hash),
        ("source_snapshot_hash", lineage.source_snapshot_hash),
    ):
        if not value or len(value) != _SHA256_HEX_LEN:
            return None, f"{INVALID_LINEAGE_REASON}: {label} must be sha256 hex"

    if (
        expected_source_snapshot_hash
        and lineage.source_snapshot_hash != expected_source_snapshot_hash
    ):
        return None, f"{INVALID_LINEAGE_REASON}: source_snapshot_hash mismatch"
    if (
        expected_chunk_manifest_hash
        and lineage.chunk_manifest_hash != expected_chunk_manifest_hash
    ):
        return None, f"{INVALID_LINEAGE_REASON}: chunk_manifest_hash mismatch"

    canonical = lineage.model_copy(
        update={
            "chunker_name": name,
            "chunker_version": version,
            "chunker_config": cfg,
            "chunker_config_hash": config_hash,
        }
    )
    return canonical, None


def lineage_five_tuple(
    lineage: ChunkerLineage | dict[str, Any] | None,
) -> dict[str, str] | None:
    """Extract five-tuple for hashing; None if incomplete (never invent)."""
    canonical, err = canonicalize_chunker_lineage(lineage)
    if canonical is None or err is not None:
        return None
    return canonical.five_tuple()


def build_quality_input_hash(
    *,
    snapshot_manifest_hash: str | None,
    case_fixture_hashes: list[str | None],
    baseline: dict[str, Any] | None,
    policy_hash_value: str | None = None,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None,
) -> str:
    """Input identity includes complete canonical five-tuple lineage when present."""
    five = lineage_five_tuple(chunker_lineage)
    return stable_hash(
        {
            "snapshot": snapshot_manifest_hash,
            "cases": case_fixture_hashes,
            "baseline": baseline,
            "policy_hash": policy_hash_value,
            "chunker_lineage": five,
        }
    )


def build_stage_cache_key(
    *,
    run_input_hash: str | None,
    case_id: str,
    fixture_hash: str | None,
    repetition: int,
    top_k: int,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
) -> str:
    """Idempotency key binds run input (incl. lineage) so cross-chunker never collides."""
    five = lineage_five_tuple(chunker_lineage)
    digest = stable_hash(
        {
            "run_input_hash": run_input_hash,
            "case_id": case_id,
            "fixture_hash": fixture_hash,
            "repetition": repetition,
            "top_k": top_k,
            "chunker_lineage": five,
        }
    )
    return f"{case_id}:r{repetition}:{digest[:16]}"


COMPARABLE_STATUSES = frozenset({"passed", "qualified"})
NON_COMPARABLE_TERMINAL = frozenset(
    {
        "failed_policy",
        "quality_regression",
        "blocked_dependency",
        "invalid_fixture",
        "invalid_lineage",
        "quarantined",
        "cancelled",
    }
)

# Stages after frozen fixtures are accepted for SUT evaluation.
SUT_STAGES = (
    "queued",
    "validating",
    "retrieving",
    "answering",
    "scoring",
    "arbitrating",
)
