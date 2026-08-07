"""DimensionResult/CandidateManifest parity gate for the qualification runner.

Phase 29-02 / REQ-QA-02; decisions D-02, D-04; consumes the Phase 28-04
``CandidateManifest`` contract. The candidate and leaf baseline must carry the
identical snapshot/cutoff/owner/version/budget/lineage/blocked_reason or the
runner fails closed with a stable blocked reason and stops metric aggregation.
Pure tests — no database, no provider.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

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
from app.services.qualification.gold_set import load_gold_set
from app.services.qualification.runner import (
    CODE_MANIFEST_CHECKSUM_FAILED,
    CODE_MANIFEST_PARITY_FAILED,
    run_qualification,
)

pytestmark = [pytest.mark.integration]

GOLD_PATH = Path(__file__).resolve().parents[3] / "evals" / "reading_qa_v1.json"
LINEAGE = {
    "hierarchy_build_id": "b" * 64,
    "commit": "912ca6b423d6c2309bc2972cbfc083c4eaa280e1",
}


@pytest.fixture(scope="module")
def gold_set():
    return load_gold_set(GOLD_PATH)


def _header(gold_set) -> dict:
    return {
        "db_fingerprint": "db-fp-manifest-001",
        "dataset_version": gold_set.dataset_version,
        "source_snapshot": gold_set.source_snapshot_hash,
        "commit": "912ca6b423d6c2309bc2972cbfc083c4eaa280e1",
        "model": "queryplan-nm-candidate.v1",
        "prompt": "prompt-hash-001",
        "schema_version": "reading-qa-canon.v1",
        "config": "config-hash-001",
        "budget": {
            "max_calls": 100,
            "max_input_tokens": 50_000,
            "max_output_tokens": 20_000,
            "max_cost_usd": "5.00",
        },
    }


def _common_fields() -> dict:
    return {
        "faithfulness": 1.0,
        "relevance": 1.0,
        "latency_ms": 10.0,
        "calls": 2,
        "input_tokens": 60,
        "output_tokens": 40,
        "cost_usd": 0.002,
        "fallback_used": False,
    }


def _clean_artifacts(gold_set) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sample in gold_set.samples:
        if sample.expected_answerability == "answerable":
            sa = sample.source_answers[0]
            out[sample.id] = {
                "answer": sa.answer,
                "cited_evidence": [r.model_dump(mode="json") for r in sa.evidence],
                "retrieved_leaf_ids": [r.evidence_key() for r in sa.evidence],
                "abstained": False,
            }
        else:
            out[sample.id] = {
                "answer": "",
                "cited_evidence": [],
                "retrieved_leaf_ids": [],
                "abstained": True,
            }
        out[sample.id].update(_common_fields())
    return out


# ---------------------------------------------------------------------------
# Manifest builders (consistent header + dimensions, recomputed checksums)
# ---------------------------------------------------------------------------


def _budget(**overrides) -> BudgetTotals:
    base = dict(
        calls=10,
        input_tokens=2_000,
        output_tokens=1_000,
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
        source_snapshot_hash=overrides.pop(
            "source_snapshot_hash", overrides.pop("snapshot", "default-snapshot")
        ),
        cutoff=overrides.pop("cutoff", 6),
        owner_id=overrides.pop("owner_id", 1),
        version_id=overrides.pop("version_id", 1),
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
        source_snapshot_hash=overrides.pop(
            "source_snapshot_hash", overrides.pop("snapshot", "default-snapshot")
        ),
        cutoff=overrides.pop("cutoff", 6),
        owner_id=overrides.pop("owner_id", 1),
        version_id=overrides.pop("version_id", 1),
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


def _dimensions(snapshot: str, **overrides) -> tuple[DimensionResult, ...]:
    """Consistent dimension set sharing one snapshot/cutoff/owner/version.

    Header-level overrides are mirrored onto every dimension so the manifest
    stays internally consistent (dimension parity must still hold).
    """
    return (
        _dimension(
            DimensionKind.TIMELINE,
            DimensionStatus.AVAILABLE,
            snapshot=snapshot,
            **overrides,
        ),
        _dimension(
            DimensionKind.RELATIONSHIP,
            DimensionStatus.AVAILABLE,
            snapshot=snapshot,
            **overrides,
        ),
        _dimension(
            DimensionKind.CLUE,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason="clue_unavailable",
            snapshot=snapshot,
            **overrides,
        ),
        _dimension(
            DimensionKind.CHARACTER,
            DimensionStatus.PARTIAL,
            progress=0.5,
            snapshot=snapshot,
            **overrides,
        ),
        _dimension(
            DimensionKind.WORLD,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason="no_candidate_content",
            snapshot=snapshot,
            **overrides,
        ),
    )


def _consistent_manifest(snapshot: str, **overrides) -> CandidateManifest:
    dims = _dimensions(snapshot, **overrides)
    kwargs = dict(snapshot=snapshot)
    for field, value in overrides.items():
        if field == "snapshot":
            kwargs["source_snapshot_hash"] = value
        else:
            kwargs[field] = value
    return _manifest(*dims, **kwargs)


def _run(gold_set, candidate, baseline, cand_manifest, base_manifest, **overrides):
    kwargs = dict(
        gold_set=gold_set,
        header=_header(gold_set),
        candidate_artifacts=candidate,
        baseline_artifacts=baseline,
        candidate_manifest=cand_manifest,
        baseline_manifest=base_manifest,
    )
    kwargs.update(overrides)
    return run_qualification(**kwargs)


# ---------------------------------------------------------------------------
# Consistent manifests pass and buckets run
# ---------------------------------------------------------------------------


def test_consistent_manifests_pass_and_run_buckets(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        _consistent_manifest(snapshot),
    )
    assert report.verdict == "qualified_candidate"
    assert report.blocked_reasons == ()
    assert len(report.buckets) == 8
    # Manifest snapshot preserved in the report.
    assert report.manifest is not None
    assert report.manifest.source_snapshot_hash == snapshot
    assert report.manifest.owner_id == gold_set.owner_id
    assert report.manifest.version_id == gold_set.version_id


def test_report_preserves_dimension_status_progress_blocked_reason(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    manifest = _consistent_manifest(snapshot)
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        manifest,
        _consistent_manifest(snapshot),
    )
    dims = {d.dimension: d for d in report.manifest.dimensions}
    assert dims["clue"].status == "blocked"
    assert dims["clue"].blocked_reason == "clue_unavailable"
    assert dims["world"].status == "blocked"
    assert dims["world"].blocked_reason == "no_candidate_content"
    assert dims["character"].status == "partial"
    assert dims["character"].progress == 0.5
    assert dims["timeline"].status == "available"
    # A blocked dimension is never hidden.
    blocked = [d for d in report.manifest.dimensions if d.status == "blocked"]
    assert len(blocked) == 2


# ---------------------------------------------------------------------------
# Parity mismatches fail closed and stop aggregation
# ---------------------------------------------------------------------------


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
def test_manifest_field_mismatch_fails_closed(gold_set, field: str, tamper: object):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)
    base_manifest = _consistent_manifest(snapshot, **{field: tamper})
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        base_manifest,
    )
    assert report.verdict == "blocked"
    assert CODE_MANIFEST_PARITY_FAILED in report.blocked_reasons
    assert report.buckets == ()  # metric aggregation stopped


def test_manifest_budget_mismatch_fails_closed(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    other_budget = _budget(calls=99)
    cand_manifest = _consistent_manifest(snapshot)
    base_manifest = _consistent_manifest(snapshot, budget=other_budget)
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        base_manifest,
    )
    assert report.verdict == "blocked"
    assert CODE_MANIFEST_PARITY_FAILED in report.blocked_reasons
    assert report.buckets == ()


def test_manifest_lineage_mismatch_fails_closed(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)
    base_manifest = _consistent_manifest(
        snapshot, lineage={"hierarchy_build_id": "e" * 64, "commit": "other"}
    )
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        base_manifest,
    )
    assert report.verdict == "blocked"
    assert CODE_MANIFEST_PARITY_FAILED in report.blocked_reasons
    assert report.buckets == ()


def test_manifest_blocked_reason_mismatch_fails_closed(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)

    dims = list(_dimensions(snapshot))
    dims[0] = _dimension(
        DimensionKind.TIMELINE,
        DimensionStatus.BLOCKED,
        progress=0.0,
        blocked_reason="timeline_unavailable",
        snapshot=snapshot,
    )
    base_manifest = _manifest(*dims, snapshot=snapshot)
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        base_manifest,
    )
    assert report.verdict == "blocked"
    assert CODE_MANIFEST_PARITY_FAILED in report.blocked_reasons
    assert "blocked_reason_mismatch:timeline" in report.blocked_reasons
    assert report.buckets == ()


def test_manifest_missing_dimension_fails_closed(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)
    # Baseline manifest drops the WORLD dimension entirely.
    dims = [d for d in _dimensions(snapshot) if d.dimension != DimensionKind.WORLD]
    base_manifest = _manifest(*dims, snapshot=snapshot)
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        base_manifest,
    )
    assert report.verdict == "blocked"
    assert CODE_MANIFEST_PARITY_FAILED in report.blocked_reasons
    assert report.buckets == ()


def test_forged_manifest_checksum_fails_closed(gold_set):
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)
    forged = cand_manifest.model_copy(update={"checksum": "f" * 64})
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        forged,
    )
    assert report.verdict == "blocked"
    assert CODE_MANIFEST_CHECKSUM_FAILED in report.blocked_reasons
    assert report.buckets == ()


def test_manifest_snapshot_forgery_with_metrics_run(gold_set):
    """Even a clean metric run blocks when a manifest escapes the snapshot."""
    cand = _clean_artifacts(gold_set)
    snapshot = gold_set.source_snapshot_hash
    cand_manifest = _consistent_manifest(snapshot)
    # Manifest claims a different snapshot than the gold set.
    forged = _consistent_manifest("c" * 64)
    report = _run(
        gold_set,
        cand,
        deepcopy(cand),
        cand_manifest,
        forged,
    )
    assert report.verdict == "blocked"
    assert report.buckets == ()
