"""Stable parity/checksum validation for the Phase 28→29 candidate manifest.

REQ-NM-03/04, D-07: ``CandidateManifest`` and every ``DimensionResult`` share
one immutable snapshot/cutoff/owner/version/budget/lineage contract. Phase 28
closure, Phase 29-02 evaluation and Phase 29-04 audit must consume only this
contract and reject a manifest whose checksum or per-dimension parity is
inconsistent. Any missing or divergent parity field fails closed, and a blocked
dimension without a stable ``blocked_reason`` is itself a parity failure.

Everything here is pure — no database, no transport, no pointer writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.narrative_memory.builder_contracts import (
    FORBIDDEN_PACKAGE_KEYS,
    _stable_json,
)
from app.services.narrative_memory.contracts import (
    CANDIDATE_MANIFEST_SCHEMA_VERSION,
    DIMENSION_RESULT_SCHEMA_VERSION,
    CandidateManifest,
    DimensionResult,
    DimensionStatus,
    candidate_manifest_checksum,
    dimension_result_checksum,
)

# The shared parity contract fields that must be identical between every
# DimensionResult and the CandidateManifest header.
PARITY_FIELDS = (
    "source_snapshot_hash",
    "cutoff",
    "owner_id",
    "version_id",
    "version_key",
    "budget",
    "lineage",
)

# Pointer/promotion vocabulary that must never appear in a candidate manifest.
FORBIDDEN_MANIFEST_KEYS = frozenset(
    {
        "active_pointer",
        "promote",
        "promotion",
        "current_version",
        "default_version",
        "production_version",
        "reader_chat",
        "conversation_id",
        "chat_text",
    }
)


class ManifestContractError(ValueError):
    """Fail-closed error for a broken candidate manifest contract."""


@dataclass(frozen=True)
class DimensionParityVerdict:
    dimension: str
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class ManifestParityReport:
    """Per-dimension parity verdicts; ``ok`` is true only when all pass."""

    ok: bool
    dimension_verdicts: tuple[DimensionParityVerdict, ...]
    mismatches: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dimension_verdicts": [
                {
                    "dimension": verdict.dimension,
                    "ok": verdict.ok,
                    "reason": verdict.reason,
                }
                for verdict in self.dimension_verdicts
            ],
            "mismatches": list(self.mismatches),
        }


def _dimension_payload(result: DimensionResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def dimension_parity_report(manifest: CandidateManifest) -> ManifestParityReport:
    """Compare every dimension's parity fields to the manifest header.

    Returns a fail-closed report: any dimension missing or diverging on
    snapshot/cutoff/owner/version/budget/lineage, or carrying a blocked state
    without a stable ``blocked_reason``, is a parity mismatch.
    """
    header = _dimension_payload(manifest)
    verdicts: list[DimensionParityVerdict] = []
    mismatches: list[str] = []
    for result in manifest.dimensions:
        payload = _dimension_payload(result)
        reasons: list[str] = []
        for field in PARITY_FIELDS:
            expected = header.get(field)
            actual = payload.get(field)
            if actual != expected:
                reasons.append(f"{field}_mismatch")
        if payload.get("status") == DimensionStatus.BLOCKED.value and not payload.get(
            "blocked_reason"
        ):
            reasons.append("blocked_reason_missing")
        if payload.get("blocked_reason") and payload.get("status") != (
            DimensionStatus.BLOCKED.value
        ):
            reasons.append("blocked_reason_unexpected")
        ok = not reasons
        if not ok:
            mismatches.append(str(result.dimension))
        verdicts.append(
            DimensionParityVerdict(
                dimension=str(result.dimension),
                ok=ok,
                reason=";".join(reasons) if reasons else None,
            )
        )
    return ManifestParityReport(
        ok=all(verdict.ok for verdict in verdicts),
        dimension_verdicts=tuple(verdicts),
        mismatches=tuple(sorted(mismatches)),
    )


def manifest_parity_ok(manifest: CandidateManifest) -> bool:
    return dimension_parity_report(manifest).ok


def assert_manifest_parity(manifest: CandidateManifest) -> None:
    """Fail closed when per-dimension parity does not hold."""
    report = dimension_parity_report(manifest)
    if not report.ok:
        raise ManifestContractError(
            f"candidate manifest parity failed: {report.mismatches}"
        )


def assert_no_pointer_fields(payload: dict[str, Any]) -> None:
    """Fail closed if a closure/progress payload carries pointer vocabulary.

    Deep scans the payload so a nested ``active_pointer``/``promote``/chat key
    can never slip into a candidate report (D-07/D-10).
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in FORBIDDEN_MANIFEST_KEYS:
                raise ManifestContractError(f"forbidden manifest key: {key}")
            assert_no_pointer_fields(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            assert_no_pointer_fields(value)


def validate_candidate_manifest(manifest: CandidateManifest) -> CandidateManifest:
    """Full fail-closed validation: checksum, schema, parity, candidate-only.

    Also runs the shared builder ``FORBIDDEN_PACKAGE_KEYS`` guard so no
    reader-chat or promotion key survives at any nesting depth.
    """
    if manifest.schema_version != CANDIDATE_MANIFEST_SCHEMA_VERSION:
        raise ManifestContractError(
            f"unexpected manifest schema_version: {manifest.schema_version}"
        )
    expected_checksum = candidate_manifest_checksum(manifest)
    if manifest.checksum != expected_checksum:
        raise ManifestContractError("candidate manifest checksum mismatch")
    for result in manifest.dimensions:
        if result.schema_version != DIMENSION_RESULT_SCHEMA_VERSION:
            raise ManifestContractError(
                f"unexpected dimension schema_version: {result.schema_version}"
            )
        expected_dimension_checksum = dimension_result_checksum(result)
        if result.checksum != expected_dimension_checksum:
            raise ManifestContractError("dimension result checksum mismatch")
    assert_manifest_parity(manifest)
    raw = json.loads(manifest.model_dump_json())
    assert_no_pointer_fields(raw)
    forbidden = FORBIDDEN_PACKAGE_KEYS.intersection(_flatten_keys(raw))
    if forbidden:
        raise ManifestContractError(
            f"forbidden package keys in manifest: {sorted(forbidden)}"
        )
    return manifest


def _flatten_keys(payload: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(payload, dict):
        keys.update(payload)
        for value in payload.values():
            keys.update(_flatten_keys(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            keys.update(_flatten_keys(value))
    return keys


def manifest_contract_repr(manifest: CandidateManifest) -> str:
    """Stable canonical repr for audit and cross-phase comparison."""
    payload = manifest.model_dump(mode="json")
    payload.pop("checksum", None)
    payload.pop("dimensions", None)
    return _stable_json(payload)
