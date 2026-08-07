"""Cross-dimension lineage parity tests for the Phase 28→29 manifest contract.

REQ-NM-03/04, D-07: ``DimensionResult`` and ``CandidateManifest`` share one
snapshot/cutoff/owner/version/budget/lineage contract. A consistent lineage
passes; any mismatch on snapshot/cutoff/owner/version/budget/lineage or a
blocked dimension without a stable ``blocked_reason`` fails closed. These are
pure contract tests — no database, no transport.
"""

from __future__ import annotations

import pytest

from app.services.narrative_memory.contracts import (
    BudgetTotals,
    CandidateManifest,
    DimensionKind,
    DimensionResult,
    DimensionStatus,
    candidate_manifest_checksum,
    dimension_result_checksum,
)
from app.services.narrative_memory.manifest_contract import (
    ManifestContractError,
    assert_no_pointer_fields,
    dimension_parity_report,
    manifest_parity_ok,
    validate_candidate_manifest,
)

pytestmark = pytest.mark.unit

SNAP = "a" * 64
LINEAGE = {"hierarchy_build_id": "b" * 64}


def _budget(**overrides) -> BudgetTotals:
    base = dict(
        calls=3,
        input_tokens=100,
        output_tokens=50,
        cost_usd="0.5",
        cache_hits=1,
    )
    base.update(overrides)
    return BudgetTotals(**base)


def _dimension(
    kind: DimensionKind,
    status: DimensionStatus,
    *,
    progress: float = 1.0,
    blocked_reason: str | None = None,
    **overrides,
) -> DimensionResult:
    kwargs = dict(
        source_snapshot_hash=overrides.pop("snapshot", SNAP),
        cutoff=overrides.pop("cutoff", 3),
        owner_id=overrides.pop("owner_id", 1),
        version_id=overrides.pop("version_id", 2),
        version_key=overrides.pop("version_key", "v1"),
        budget=overrides.pop("budget", _budget()),
        lineage=overrides.pop("lineage", LINEAGE),
    )
    placeholder = DimensionResult(
        dimension=kind,
        status=status,
        progress=progress,
        blocked_reason=blocked_reason,
        checksum="0" * 64,
        **kwargs,
    )
    return placeholder.model_copy(
        update={"checksum": dimension_result_checksum(placeholder)}
    )


def _manifest(*dimensions, **overrides) -> CandidateManifest:
    kwargs = dict(
        source_snapshot_hash=overrides.pop("snapshot", SNAP),
        cutoff=overrides.pop("cutoff", 3),
        owner_id=overrides.pop("owner_id", 1),
        version_id=overrides.pop("version_id", 2),
        version_key=overrides.pop("version_key", "v1"),
        budget=overrides.pop("budget", _budget()),
        lineage=overrides.pop("lineage", LINEAGE),
    )
    placeholder = CandidateManifest(
        dimensions=tuple(dimensions),
        checksum="0" * 64,
        **kwargs,
    )
    return placeholder.model_copy(
        update={"checksum": candidate_manifest_checksum(placeholder)}
    )


def _consistent_manifest() -> CandidateManifest:
    dimensions = (
        _dimension(DimensionKind.TIMELINE, DimensionStatus.AVAILABLE),
        _dimension(DimensionKind.RELATIONSHIP, DimensionStatus.PARTIAL, progress=0.5),
        _dimension(
            DimensionKind.CLUE,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason="clue_unavailable",
        ),
        _dimension(DimensionKind.CHARACTER, DimensionStatus.AVAILABLE),
        _dimension(
            DimensionKind.WORLD,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason="no_candidate_content",
        ),
    )
    return _manifest(*dimensions)


def test_consistent_lineage_passes_parity_and_full_validation():
    manifest = _consistent_manifest()
    assert manifest_parity_ok(manifest) is True
    report = dimension_parity_report(manifest)
    assert report.mismatches == ()
    assert all(verdict.ok for verdict in report.dimension_verdicts)
    # Full fail-closed validation (checksum + schema + parity + pointer) passes.
    validate_candidate_manifest(manifest)


def test_checksum_is_stable_across_json_roundtrip():
    manifest = _consistent_manifest()
    reparsed = CandidateManifest.model_validate_json(manifest.model_dump_json())
    assert reparsed.checksum == candidate_manifest_checksum(reparsed)
    for left, right in zip(manifest.dimensions, reparsed.dimensions):
        assert left.checksum == dimension_result_checksum(right)


@pytest.mark.parametrize(
    "field,tamper",
    [
        ("source_snapshot_hash", "f" * 64),
        ("cutoff", 9),
        ("owner_id", 99),
        ("version_id", 99),
        ("version_key", "other-version"),
    ],
)
def test_dimension_snapshot_cutoff_owner_version_mismatch_fails_closed(
    field: str, tamper: object
):
    base = _consistent_manifest()
    dims = list(base.dimensions)
    tampered = dims[0].model_copy(update={field: tamper, "checksum": "0" * 64})
    tampered = tampered.model_copy(
        update={"checksum": dimension_result_checksum(tampered)}
    )
    manifest = _manifest(*[tampered, *dims[1:]])
    assert manifest_parity_ok(manifest) is False
    assert dimension_parity_report(manifest).mismatches == ("timeline",)
    with pytest.raises(ManifestContractError):
        validate_candidate_manifest(manifest)


def test_budget_mismatch_fails_closed():
    base = _consistent_manifest()
    dims = list(base.dimensions)
    other_budget = _budget(calls=9)
    tampered = dims[0].model_copy(update={"budget": other_budget, "checksum": "0" * 64})
    tampered = tampered.model_copy(
        update={"checksum": dimension_result_checksum(tampered)}
    )
    manifest = _manifest(*[tampered, *dims[1:]])
    assert manifest_parity_ok(manifest) is False
    with pytest.raises(ManifestContractError):
        validate_candidate_manifest(manifest)


def test_lineage_mismatch_fails_closed():
    base = _consistent_manifest()
    dims = list(base.dimensions)
    tampered = dims[0].model_copy(
        update={"lineage": {"hierarchy_build_id": "different"}, "checksum": "0" * 64}
    )
    tampered = tampered.model_copy(
        update={"checksum": dimension_result_checksum(tampered)}
    )
    manifest = _manifest(*[tampered, *dims[1:]])
    assert manifest_parity_ok(manifest) is False
    with pytest.raises(ManifestContractError):
        validate_candidate_manifest(manifest)


def test_blocked_dimension_without_reason_is_a_parity_failure():
    # Model-level guard rejects blocked-without-reason at construction.
    with pytest.raises(ValueError):
        _dimension(
            DimensionKind.WORLD,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason=None,
        )


def test_unexpected_blocked_reason_fails_closed():
    # A blocked_reason on a non-blocked dimension is rejected at the model
    # level (fail closed) before any parity check can pass.
    with pytest.raises(ValueError):
        _dimension(
            DimensionKind.TIMELINE,
            DimensionStatus.AVAILABLE,
            progress=1.0,
            blocked_reason="timeline_unavailable",
        )


def test_checksum_tamper_fails_closed():
    manifest = _consistent_manifest()
    manifest = manifest.model_copy(
        update={"checksum": candidate_manifest_checksum(manifest)}
    )
    tampered = manifest.model_copy(update={"checksum": "f" * 64})
    with pytest.raises(ManifestContractError):
        validate_candidate_manifest(tampered)


def test_pointer_fields_are_rejected_deep():
    with pytest.raises(ManifestContractError):
        assert_no_pointer_fields({"dimensions": [{"active_pointer": "x"}]})
    with pytest.raises(ManifestContractError):
        assert_no_pointer_fields({"nested": {"promote": True}})
    with pytest.raises(ManifestContractError):
        assert_no_pointer_fields({"reader_chat": "chat"})
    # Clean payloads pass.
    assert_no_pointer_fields({"dimensions": [{"status": "available"}]})


def test_manifest_carries_candidate_only_header():
    manifest = _consistent_manifest()
    assert manifest.schema_version == "candidate-manifest.v1"
    # Candidate-only: never a pointer, promotion, or chat key at any depth.
    raw = manifest.model_dump(mode="json")
    assert_no_pointer_fields(raw)
    for forbidden in ("active_pointer", "promote", "reader_chat"):
        assert forbidden not in raw
