"""Three-dimension v1.2 audit integration tests (Phase 29-04).

REQ-QA-02/03; decisions D-02..D-06 from 29-CONTEXT.md; consumes the Phase 28-04
``CandidateManifest``/``DimensionResult`` contract (Task 3).

Covers: the full clean audit → ``qualified_candidate`` with per-dimension
independent status/evidence/risks; missing or mismatched evidence → ``blocked``;
manifest checksum/parity/blocked_reason propagation; header lineage binding; no
single completion percentage; no promotion vocabulary; Phase 22 independence;
replayability; and the audit never mutating STATE/ROADMAP or writing pointers.

The test collects live evidence (files, imports, capability functions, migration
heads, e2e spec markers) from the actual repository and reconciles it through
``run_audit`` — the audit itself never writes anything.

Pure tests: no database, no provider transport.
"""

from __future__ import annotations

import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

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
from app.services.qualification.audit import (
    AuditDimension,
    AuditDimensionStatus,
    BrowserEvidence,
    LiveCodeEvidence,
    audit_has_promotion_capability,
    audit_has_provider_capability,
    audit_mutates_planning_state,
    migration_heads,
    run_audit,
    scan_forbidden_writes_in_files,
)
from app.services.qualification.gold_set import load_gold_set
from app.services.qualification.runner import run_qualification

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = REPO_ROOT / "backend"
GOLD_PATH = BACKEND_ROOT / "evals" / "reading_qa_v1.json"
QUAL_DIR = BACKEND_ROOT / "app" / "services" / "qualification"
NM_DIR = BACKEND_ROOT / "app" / "services" / "narrative_memory"
MIGRATIONS_DIR = BACKEND_ROOT / "migrations" / "versions"
STATE_PATH = REPO_ROOT / ".planning" / "STATE.md"
ROADMAP_PATH = REPO_ROOT / ".planning" / "ROADMAP.md"
E2E_DIR = REPO_ROOT / "frontend" / "e2e"

EXPECTED_MIGRATION_HEAD = "20260801_2801"

REQUIRED_FILES = (
    "backend/app/services/qualification/__init__.py",
    "backend/app/services/qualification/gold_set.py",
    "backend/app/services/qualification/rubric.py",
    "backend/app/services/qualification/metrics.py",
    "backend/app/services/qualification/report.py",
    "backend/app/services/qualification/runner.py",
    "backend/app/services/qualification/audit.py",
    "backend/app/services/narrative_memory/contracts.py",
    "backend/app/services/narrative_memory/manifest_contract.py",
    "backend/app/services/narrative_memory/qualification_verdict.py",
    "backend/evals/reading_qa_v1.json",
    "frontend/e2e/reader-chat-quality.spec.ts",
    "frontend/e2e/analysis-chat-quality.spec.ts",
)

IMPORTABLE_MODULES = (
    "app.services.qualification.gold_set",
    "app.services.qualification.rubric",
    "app.services.qualification.metrics",
    "app.services.qualification.report",
    "app.services.qualification.runner",
    "app.services.qualification.audit",
    "app.services.narrative_memory.contracts",
    "app.services.narrative_memory.manifest_contract",
)

SCAN_FILES = tuple(
    str(path.relative_to(BACKEND_ROOT))
    for path in (
        QUAL_DIR / "gold_set.py",
        QUAL_DIR / "rubric.py",
        QUAL_DIR / "metrics.py",
        QUAL_DIR / "report.py",
        QUAL_DIR / "runner.py",
        QUAL_DIR / "audit.py",
        NM_DIR / "contracts.py",
        NM_DIR / "manifest_contract.py",
        NM_DIR / "qualification_verdict.py",
    )
)

# Code-level provider markers that must never appear in the qualified services.
PROVIDER_MARKERS = ("litellm", "openai", "httpx", "asyncpg", "requests")

LINEAGE = {"hierarchy_build_id": "b" * 64, "commit": "912ca6b423d6c2309bc2972cbfc083c4eaa280e1"}


def _repo_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError:
        pass
    return "912ca6b423d6c2309bc2972cbfc083c4eaa280e1"


COMMIT = _repo_commit()


@pytest.fixture(scope="module")
def gold_set():
    return load_gold_set(GOLD_PATH)


# ---------------------------------------------------------------------------
# Qualification report / artifacts (29-02 runner reused as the report source)
# ---------------------------------------------------------------------------


def _header(gold_set) -> dict:
    return {
        "db_fingerprint": "db-fp-audit-001",
        "dataset_version": gold_set.dataset_version,
        "source_snapshot": gold_set.source_snapshot_hash,
        "commit": COMMIT,
        "model": "queryplan-nm-candidate.v1",
        "prompt": "prompt-hash-audit",
        "schema_version": "reading-qa-canon.v1",
        "config": "config-hash-audit",
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
        "latency_ms": 12.0,
        "calls": 2,
        "input_tokens": 60,
        "output_tokens": 40,
        "cost_usd": 0.002,
        "fallback_used": False,
        "provider_error": None,
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
# CandidateManifest builders (consistent header + dimensions, recomputed hashes)
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
    **overrides: Any,
) -> DimensionResult:
    kwargs = dict(
        source_snapshot_hash=str(
            overrides.pop("source_snapshot_hash", overrides.pop("snapshot", "a" * 64))
        ),
        cutoff=int(overrides.pop("cutoff", 6)),
        owner_id=int(overrides.pop("owner_id", 1)),
        version_id=int(overrides.pop("version_id", 1)),
        version_key=str(overrides.pop("version_key", "v1")),
        budget=overrides.pop("budget", _budget()),
        lineage=dict(overrides.pop("lineage", LINEAGE)),
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


def _manifest(*dimensions: DimensionResult, **overrides: Any) -> CandidateManifest:
    kwargs = dict(
        source_snapshot_hash=str(
            overrides.pop("source_snapshot_hash", overrides.pop("snapshot", "a" * 64))
        ),
        cutoff=int(overrides.pop("cutoff", 6)),
        owner_id=int(overrides.pop("owner_id", 1)),
        version_id=int(overrides.pop("version_id", 1)),
        version_key=str(overrides.pop("version_key", "v1")),
        budget=overrides.pop("budget", _budget()),
        lineage=dict(overrides.pop("lineage", LINEAGE)),
    )
    placeholder = CandidateManifest(
        dimensions=tuple(dimensions),
        checksum="0" * 64,
        **kwargs,
    )
    return placeholder.model_copy(
        update={"checksum": candidate_manifest_checksum(placeholder)}
    )


def _dimensions(snapshot: str, **overrides: Any) -> tuple[DimensionResult, ...]:
    """Five-dimension set sharing one snapshot/cutoff/owner/version."""
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


def _consistent_manifest(snapshot: str, **overrides: Any) -> CandidateManifest:
    dims = _dimensions(snapshot, **overrides)
    kwargs = dict(snapshot=snapshot)
    for field, value in overrides.items():
        if field == "snapshot":
            kwargs["source_snapshot_hash"] = value
        else:
            kwargs[field] = value
    return _manifest(*dims, **kwargs)


def _clean_report(gold_set) -> tuple:
    """Run the 29-02 runner with clean artifacts + consistent manifests."""
    candidate = _clean_artifacts(gold_set)
    manifest = _consistent_manifest(gold_set.source_snapshot_hash)
    report = run_qualification(
        gold_set=gold_set,
        header=_header(gold_set),
        candidate_artifacts=candidate,
        baseline_artifacts=deepcopy(candidate),
        candidate_manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert report.verdict == "qualified_candidate", report.blocked_reasons
    return report, manifest


# ---------------------------------------------------------------------------
# Live evidence collection
# ---------------------------------------------------------------------------


def _capability_violations() -> list[str]:
    """Import every capability function and collect any that returns True."""
    checks: list[tuple[str, Any]] = []
    from app.services.qualification import audit, gold_set, metrics, report, runner

    for module, names in (
        (audit, ("audit_has_promotion_capability", "audit_has_provider_capability")),
        (runner, ("runner_has_promotion_capability", "runner_has_provider_capability")),
        (report, ("report_has_promotion_capability", "report_has_provider_capability")),
        (metrics, ("metrics_has_promotion_capability", "metrics_has_provider_capability")),
        (gold_set, ("gold_set_has_promotion_capability", "gold_set_has_forbidden_capability")),
    ):
        for name in names:
            fn = getattr(module, name)
            checks.append((f"{module.__name__}.{name}", bool(fn())))
    from app.services.narrative_memory.qualification_verdict import (
        verdict_has_promotion_capability,
        verdict_has_provider_capability,
    )

    checks.append(("verdict_has_promotion_capability", bool(verdict_has_promotion_capability())))
    checks.append(("verdict_has_provider_capability", bool(verdict_has_provider_capability())))
    return [name for name, enabled in checks if enabled]


def _word_search(marker: str, source: str) -> bool:
    return (
        re.search(r"(?<![A-Za-z_0-9])" + re.escape(marker) + r"(?![A-Za-z_0-9])", source)
        is not None
    )


def _provider_imports() -> list[str]:
    found: list[str] = []
    for rel in SCAN_FILES:
        source = (BACKEND_ROOT / rel).read_text(encoding="utf-8")
        for marker in PROVIDER_MARKERS:
            if _word_search(marker, source):
                found.append(f"{rel}:{marker}")
    return sorted(found)


def _live_code_evidence() -> LiveCodeEvidence:
    missing = [f for f in REQUIRED_FILES if not (REPO_ROOT / f).is_file()]
    failed: list[str] = []
    import importlib

    for module in IMPORTABLE_MODULES:
        try:
            importlib.import_module(module)
        except Exception:
            failed.append(module)
    forbidden = scan_forbidden_writes_in_files(
        [BACKEND_ROOT / rel for rel in SCAN_FILES]
    )
    heads = migration_heads(MIGRATIONS_DIR)
    return LiveCodeEvidence(
        required_files=REQUIRED_FILES,
        missing_files=tuple(missing),
        importable_modules=IMPORTABLE_MODULES,
        failed_modules=tuple(failed),
        capability_violations=tuple(_capability_violations()),
        forbidden_vocabulary=tuple(forbidden),
        provider_imports=tuple(_provider_imports()),
        migration_head=heads[0] if len(heads) == 1 else None,
        migration_single_head=len(heads) == 1,
        expected_migration_head=EXPECTED_MIGRATION_HEAD,
    )


def _spec_text() -> str:
    parts: list[str] = []
    for name in ("reader-chat-quality.spec.ts", "analysis-chat-quality.spec.ts"):
        path = E2E_DIR / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _observed_browser(executed: bool = False) -> BrowserEvidence:
    text = _spec_text()
    specs = tuple(
        f"frontend/e2e/{name}"
        for name in ("reader-chat-quality.spec.ts", "analysis-chat-quality.spec.ts")
        if (E2E_DIR / name).is_file()
    )
    return BrowserEvidence(
        spec_paths=specs,
        executed=executed,
        spoiler_safe_asserted="SECRET_FUTURE" in text,
        citation_jump_asserted=(
            "reader-citation-highlight" in text or "reader-chat-citation" in text
        ),
        partial_failure_asserted=any(
            marker in text for marker in ("abstain", "partial", "failed")
        ),
        accessibility_asserted=("aria-live" in text or "focus" in text),
        mobile_asserted="390px" in text,
    )


def _clean_browser() -> BrowserEvidence:
    return BrowserEvidence(
        spec_paths=(
            "frontend/e2e/reader-chat-quality.spec.ts",
            "frontend/e2e/analysis-chat-quality.spec.ts",
        ),
        executed=True,
        observed_test_count=33,
        spoiler_safe_asserted=True,
        citation_jump_asserted=True,
        partial_failure_asserted=True,
        accessibility_asserted=True,
        mobile_asserted=True,
    )


def _audit(gold_set, report=None, manifest=None, browser=None, **overrides):
    baseline = overrides.pop(
        "baseline_manifest",
        _consistent_manifest(gold_set.source_snapshot_hash) if manifest is not None else None,
    )
    kwargs = dict(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=_live_code_evidence(),
        browser=browser if browser is not None else _clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=baseline,
        phase22_green_observed=overrides.pop("phase22_green_observed", 0),
    )
    kwargs.update(overrides)
    return run_audit(**kwargs)


# ---------------------------------------------------------------------------
# 1. Full clean audit → qualified_candidate (D-02, D-05)
# ---------------------------------------------------------------------------


def test_full_clean_audit_qualifies_candidate(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest)

    assert audit.verdict == "qualified_candidate"
    assert audit.blocked_reasons == ()
    assert audit.checksum_valid

    statuses = {d.dimension: d.status for d in audit.dimensions}
    assert statuses[AuditDimension.IMPLEMENTATION_READINESS] == AuditDimensionStatus.VERIFIED
    assert statuses[AuditDimension.SAMPLE_DATA_COVERAGE] == AuditDimensionStatus.VERIFIED
    assert statuses[AuditDimension.QUALITY_QUALIFICATION] == AuditDimensionStatus.VERIFIED

    # Phase 22 0/3 stays independent and never changes the NM verdict.
    assert audit.phase22.blocked is True
    assert audit.phase22.green_observed == 0


def test_each_dimension_has_independent_status_evidence_and_risks(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest)

    assert len(audit.dimensions) == 3
    for dimension in audit.dimensions:
        # Evidence links bind every verdict (D-02).
        assert dimension.evidence, f"{dimension.dimension} carries no evidence"
        assert dimension.status in set(AuditDimensionStatus)

    by_dim = {d.dimension: d for d in audit.dimensions}
    impl = by_dim[AuditDimension.IMPLEMENTATION_READINESS]
    data = by_dim[AuditDimension.SAMPLE_DATA_COVERAGE]
    qual = by_dim[AuditDimension.QUALITY_QUALIFICATION]

    evidence_kinds = {link.kind for link in qual.evidence}
    assert "db_fingerprint" in evidence_kinds
    assert "commit" in evidence_kinds
    assert "report" in evidence_kinds
    assert "browser" in evidence_kinds
    assert any(link.kind == "schema_migration" for link in impl.evidence)
    assert any(link.kind == "manifest" for link in data.evidence)
    assert any(link.kind == "dataset_snapshot" for link in data.evidence)


def test_audit_binds_header_lineage(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest)

    assert audit.header.db_fingerprint == "db-fp-audit-001"
    assert audit.header.dataset_version == gold_set.dataset_version
    assert audit.header.source_snapshot == gold_set.source_snapshot_hash
    assert audit.header.commit == COMMIT
    assert audit.header.model and audit.header.prompt
    assert audit.header.schema_version and audit.header.config
    assert audit.header.budget
    # Report header lineage is bound to the audit header.
    assert audit.dimensions[
        list(AuditDimension).index(AuditDimension.QUALITY_QUALIFICATION)
    ].status == AuditDimensionStatus.VERIFIED


# ---------------------------------------------------------------------------
# 2. No single completion percentage / no promotion (D-05)
# ---------------------------------------------------------------------------


def test_no_single_completion_percentage(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest)
    dump = audit.model_dump(mode="json")
    for banned in (
        "completion_percentage",
        "overall_score",
        "total_score",
        "single_score",
        "aggregate_score",
        "percent",
    ):
        assert banned not in json.dumps(dump), f"audit leaks single score: {banned}"
    assert audit.has_completion_percentage is False


def test_verdict_only_two_values_across_scenarios(gold_set):
    report, manifest = _clean_report(gold_set)
    scenarios = [
        _audit(gold_set, report=report, manifest=manifest),
        _audit(gold_set, report=None, manifest=manifest),
        _audit(gold_set, report=report, manifest=None),
        _audit(gold_set, report=report, manifest=manifest, browser=BrowserEvidence()),
    ]
    for audit in scenarios:
        assert audit.verdict in ("qualified_candidate", "blocked")


def test_no_promotion_vocabulary_in_audit_report(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest)
    banned = (
        "promote",
        "promoted",
        "promotion",
        "active_pointer",
        "production_ready",
        "cutover",
    )
    for reason in audit.blocked_reasons:
        for word in banned:
            assert word not in reason, f"forbidden word in blocked_reason: {reason}"
    for dimension in audit.dimensions:
        for reason in dimension.blocked_reasons:
            for word in banned:
                assert word not in reason, f"forbidden word in dimension reason: {reason}"
    # No promotion/pointer field exists anywhere in the audit model surface.
    dump = audit.model_dump(mode="json")
    for field in ("promotion", "active_pointer", "current_version", "default_version"):
        assert field not in dump, f"forbidden audit field: {field}"


# ---------------------------------------------------------------------------
# 3. Replayability / determinism
# ---------------------------------------------------------------------------


def test_audit_replayable_and_deterministic(gold_set):
    report, manifest = _clean_report(gold_set)
    first = _audit(gold_set, report=report, manifest=manifest)
    second = _audit(gold_set, report=report, manifest=manifest)
    assert first.checksum == second.checksum
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 4. Quality dimension: missing evidence and header lineage (D-02)
# ---------------------------------------------------------------------------


def test_missing_report_blocks_quality(gold_set):
    _, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=None, manifest=manifest)
    assert audit.verdict == "blocked"
    assert "report_missing" in audit.blocked_reasons
    qual = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.QUALITY_QUALIFICATION
    )
    assert qual.status == AuditDimensionStatus.BLOCKED
    assert "report_missing" in qual.blocked_reasons


def test_blocked_report_propagates_reasons(gold_set):
    candidate = _clean_artifacts(gold_set)
    candidate["local_01"]["provider_error"] = "provider_timeout"
    manifest = _consistent_manifest(gold_set.source_snapshot_hash)
    blocked_report = run_qualification(
        gold_set=gold_set,
        header=_header(gold_set),
        candidate_artifacts=candidate,
        baseline_artifacts=_clean_artifacts(gold_set),
        candidate_manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert blocked_report.verdict == "blocked"
    audit = _audit(gold_set, report=blocked_report, manifest=manifest)
    assert audit.verdict == "blocked"
    assert "report_verdict_blocked" in audit.blocked_reasons
    assert "report_reason:provider_unavailable" in audit.blocked_reasons


def test_header_source_snapshot_mismatch_blocks(gold_set):
    report, manifest = _clean_report(gold_set)
    bad_header = {**_header(gold_set), "source_snapshot": "f" * 64}
    audit = run_audit(
        header=bad_header,
        gold_set=gold_set,
        live_code=_live_code_evidence(),
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "report_source_snapshot_mismatch" in audit.blocked_reasons
    qual = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.QUALITY_QUALIFICATION
    )
    assert qual.status == AuditDimensionStatus.BLOCKED


def test_missing_browser_evidence_blocks(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest, browser=BrowserEvidence())
    assert audit.verdict == "blocked"
    assert "browser_evidence_missing" in audit.blocked_reasons


def test_browser_env_limited_marks_partial_and_blocks(gold_set):
    """Real-world 29-03 state: specs bound but execution env-limited."""
    report, manifest = _clean_report(gold_set)
    audit = _audit(
        gold_set,
        report=report,
        manifest=manifest,
        browser=_observed_browser(executed=False),
    )
    qual = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.QUALITY_QUALIFICATION
    )
    assert qual.status == AuditDimensionStatus.PARTIAL
    assert qual.blocked_reasons == ()  # not a hard failure
    assert any(r.code == "browser_execution_env_limited" for r in qual.risks)
    # A non-verified dimension means NM is not fully qualified.
    assert audit.verdict == "blocked"
    assert "dimension_partial:quality_qualification" in audit.blocked_reasons


# ---------------------------------------------------------------------------
# 5. Sample-data dimension: manifest parity and coverage (D-04, Task 3)
# ---------------------------------------------------------------------------


def test_missing_manifest_blocks_sample_data(gold_set):
    report, _ = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=None)
    assert audit.verdict == "blocked"
    assert "manifest_missing" in audit.blocked_reasons
    data = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.SAMPLE_DATA_COVERAGE
    )
    assert data.status == AuditDimensionStatus.BLOCKED


def test_missing_baseline_manifest_blocks_pair_parity(gold_set):
    report, manifest = _clean_report(gold_set)
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=_live_code_evidence(),
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=None,
    )
    assert audit.verdict == "blocked"
    assert "baseline_manifest_missing" in audit.blocked_reasons
    assert "manifest_pair_parity_failed" in audit.blocked_reasons


def test_manifest_checksum_forgery_blocks(gold_set):
    report, manifest = _clean_report(gold_set)
    forged = manifest.model_copy(update={"checksum": "f" * 64})
    audit = _audit(gold_set, report=report, manifest=forged)
    assert audit.verdict == "blocked"
    assert "manifest_checksum_failed" in audit.blocked_reasons


def test_manifest_snapshot_mismatch_vs_gold_blocks(gold_set):
    report, _ = _clean_report(gold_set)
    foreign_manifest = _consistent_manifest("c" * 64)
    audit = _audit(gold_set, report=report, manifest=foreign_manifest)
    assert audit.verdict == "blocked"
    assert "manifest_snapshot_mismatch" in audit.blocked_reasons


def test_manifest_dimension_parity_mismatch_blocks(gold_set):
    report, manifest = _clean_report(gold_set)
    dims = list(_dimensions(gold_set.source_snapshot_hash))
    # One dimension escapes the shared cutoff contract; header stays at 6.
    dims[0] = _dimension(
        DimensionKind.TIMELINE,
        DimensionStatus.AVAILABLE,
        cutoff=9,
        snapshot=gold_set.source_snapshot_hash,
    )
    broken = _manifest(*dims, snapshot=gold_set.source_snapshot_hash)
    audit = _audit(gold_set, report=report, manifest=broken)
    assert audit.verdict == "blocked"
    assert "manifest_parity_failed" in audit.blocked_reasons
    assert any(r.startswith("parity_mismatch:") for r in audit.blocked_reasons)
    assert any(r.startswith("parity_field:") for r in audit.blocked_reasons)
    data = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.SAMPLE_DATA_COVERAGE
    )
    assert data.status == AuditDimensionStatus.BLOCKED


def test_missing_dimension_kind_blocks_sample_data(gold_set):
    report, _ = _clean_report(gold_set)
    dims = [
        d for d in _dimensions(gold_set.source_snapshot_hash)
        if d.dimension != DimensionKind.WORLD
    ]
    partial_manifest = _manifest(*dims, snapshot=gold_set.source_snapshot_hash)
    audit = _audit(gold_set, report=report, manifest=partial_manifest)
    assert audit.verdict == "blocked"
    assert "manifest_dimension_missing:world" in audit.blocked_reasons


def test_blocked_reason_propagates_through_manifest_snapshot(gold_set):
    """A blocked dimension keeps its status + blocked_reason end-to-end (Task 3)."""
    report, manifest = _clean_report(gold_set)
    audit = _audit(gold_set, report=report, manifest=manifest)

    # Report snapshot preserved the blocked dimension with its stable reason.
    snapshot_dims = {
        d.dimension: d for d in report.manifest.dimensions
    }
    assert snapshot_dims["clue"].status == "blocked"
    assert snapshot_dims["clue"].blocked_reason == "clue_unavailable"
    assert snapshot_dims["world"].status == "blocked"
    assert snapshot_dims["world"].blocked_reason == "no_candidate_content"

    # The audit's data dimension stays verified: parity holds, reasons bound.
    data = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.SAMPLE_DATA_COVERAGE
    )
    assert data.status == AuditDimensionStatus.VERIFIED


def test_report_manifest_blocked_reason_mismatch_blocks(gold_set):
    """The report snapshot must match the bound manifest blocked_reason."""
    report, manifest = _clean_report(gold_set)
    # Report claims a different blocked_reason than the manifest carries.
    snapshot = report.manifest.model_copy(
        update={
            "dimensions": tuple(
                d.model_copy(update={"blocked_reason": "other_reason"})
                if d.dimension == "clue"
                else d
                for d in report.manifest.dimensions
            )
        }
    )
    tampered = report.model_copy(update={"manifest": snapshot})
    audit = _audit(gold_set, report=tampered, manifest=manifest)
    assert audit.verdict == "blocked"
    assert "report_manifest_blocked_reason_mismatch:clue" in audit.blocked_reasons


def test_pair_blocked_reason_mismatch_blocks(gold_set):
    report, manifest = _clean_report(gold_set)
    dims = list(_dimensions(gold_set.source_snapshot_hash))
    dims[0] = _dimension(
        DimensionKind.TIMELINE,
        DimensionStatus.BLOCKED,
        progress=0.0,
        blocked_reason="timeline_unavailable",
        snapshot=gold_set.source_snapshot_hash,
    )
    divergent = _manifest(*dims, snapshot=gold_set.source_snapshot_hash)
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=_live_code_evidence(),
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=divergent,
    )
    assert audit.verdict == "blocked"
    assert "pair_blocked_reason_mismatch:timeline" in audit.blocked_reasons


# ---------------------------------------------------------------------------
# 6. Implementation dimension: schema migration and candidate-only gates
# ---------------------------------------------------------------------------


def test_multi_head_migration_blocks_implementation(gold_set):
    report, manifest = _clean_report(gold_set)
    evidence = _live_code_evidence()
    evidence = LiveCodeEvidence(
        required_files=evidence.required_files,
        missing_files=evidence.missing_files,
        importable_modules=evidence.importable_modules,
        failed_modules=evidence.failed_modules,
        capability_violations=evidence.capability_violations,
        forbidden_vocabulary=evidence.forbidden_vocabulary,
        provider_imports=evidence.provider_imports,
        migration_head="multi",
        migration_single_head=False,
        expected_migration_head=EXPECTED_MIGRATION_HEAD,
    )
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=evidence,
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "schema_migration_multi_head" in audit.blocked_reasons
    impl = next(
        d
        for d in audit.dimensions
        if d.dimension == AuditDimension.IMPLEMENTATION_READINESS
    )
    assert impl.status == AuditDimensionStatus.BLOCKED


def test_migration_head_mismatch_blocks_implementation(gold_set):
    report, manifest = _clean_report(gold_set)
    evidence = _live_code_evidence()
    evidence = LiveCodeEvidence(
        required_files=evidence.required_files,
        missing_files=evidence.missing_files,
        importable_modules=evidence.importable_modules,
        failed_modules=evidence.failed_modules,
        capability_violations=evidence.capability_violations,
        forbidden_vocabulary=evidence.forbidden_vocabulary,
        provider_imports=evidence.provider_imports,
        migration_head="otherhead",
        migration_single_head=True,
        expected_migration_head=EXPECTED_MIGRATION_HEAD,
    )
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=evidence,
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "schema_migration_head_mismatch" in audit.blocked_reasons


def test_capability_violation_blocks_implementation(gold_set):
    report, manifest = _clean_report(gold_set)
    evidence = _live_code_evidence()
    evidence = LiveCodeEvidence(
        required_files=evidence.required_files,
        missing_files=evidence.missing_files,
        importable_modules=evidence.importable_modules,
        failed_modules=evidence.failed_modules,
        capability_violations=("promotion",),
        forbidden_vocabulary=evidence.forbidden_vocabulary,
        provider_imports=evidence.provider_imports,
        migration_head=evidence.migration_head,
        migration_single_head=evidence.migration_single_head,
        expected_migration_head=evidence.expected_migration_head,
    )
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=evidence,
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "capability_violation" in audit.blocked_reasons


def test_forbidden_vocabulary_blocks_implementation(gold_set):
    report, manifest = _clean_report(gold_set)
    evidence = _live_code_evidence()
    evidence = LiveCodeEvidence(
        required_files=evidence.required_files,
        missing_files=evidence.missing_files,
        importable_modules=evidence.importable_modules,
        failed_modules=evidence.failed_modules,
        capability_violations=evidence.capability_violations,
        forbidden_vocabulary=("active_pointer_assignment",),
        provider_imports=evidence.provider_imports,
        migration_head=evidence.migration_head,
        migration_single_head=evidence.migration_single_head,
        expected_migration_head=evidence.expected_migration_head,
    )
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=evidence,
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "forbidden_vocabulary_found" in audit.blocked_reasons


def test_provider_import_blocks_implementation(gold_set):
    report, manifest = _clean_report(gold_set)
    evidence = _live_code_evidence()
    evidence = LiveCodeEvidence(
        required_files=evidence.required_files,
        missing_files=evidence.missing_files,
        importable_modules=evidence.importable_modules,
        failed_modules=evidence.failed_modules,
        capability_violations=evidence.capability_violations,
        forbidden_vocabulary=evidence.forbidden_vocabulary,
        provider_imports=("qualification:litellm",),
        migration_head=evidence.migration_head,
        migration_single_head=evidence.migration_single_head,
        expected_migration_head=evidence.expected_migration_head,
    )
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=evidence,
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "provider_import_found" in audit.blocked_reasons


def test_missing_required_file_blocks_implementation(gold_set):
    report, manifest = _clean_report(gold_set)
    evidence = _live_code_evidence()
    evidence = LiveCodeEvidence(
        required_files=evidence.required_files,
        missing_files=("backend/app/services/qualification/runner.py",),
        importable_modules=evidence.importable_modules,
        failed_modules=evidence.failed_modules,
        capability_violations=evidence.capability_violations,
        forbidden_vocabulary=evidence.forbidden_vocabulary,
        provider_imports=evidence.provider_imports,
        migration_head=evidence.migration_head,
        migration_single_head=evidence.migration_single_head,
        expected_migration_head=evidence.expected_migration_head,
    )
    audit = run_audit(
        header=_header(gold_set),
        gold_set=gold_set,
        live_code=evidence,
        browser=_clean_browser(),
        report=report,
        manifest=manifest,
        baseline_manifest=_consistent_manifest(gold_set.source_snapshot_hash),
    )
    assert audit.verdict == "blocked"
    assert "required_file_missing" in audit.blocked_reasons


# ---------------------------------------------------------------------------
# 7. Live evidence collection reflects the actual repository
# ---------------------------------------------------------------------------


def test_live_code_evidence_is_clean(gold_set):
    evidence = _live_code_evidence()
    assert evidence.missing_files == ()
    assert evidence.failed_modules == ()
    assert evidence.capability_violations == ()
    assert evidence.forbidden_vocabulary == ()
    assert evidence.provider_imports == ()
    assert evidence.migration_single_head is True
    assert evidence.migration_head == EXPECTED_MIGRATION_HEAD


def test_observed_browser_evidence_reconciles(gold_set):
    observed = _observed_browser(executed=False)
    assert observed.spec_paths
    assert observed.spoiler_safe_asserted is True
    assert observed.citation_jump_asserted is True
    assert observed.partial_failure_asserted is True
    assert observed.accessibility_asserted is True
    assert observed.mobile_asserted is True


def test_phase22_green_observed_matches_ledger(gold_set):
    """Phase 22 must remain <3/3 (blocked) and is surfaced independently."""
    audit = _audit(gold_set, report=None)
    assert audit.phase22.blocked is True
    assert audit.phase22.green_observed == 0
    assert "Phase 22" in audit.phase22.detail


# ---------------------------------------------------------------------------
# 8. Audit never mutates STATE/ROADMAP and has no write/promotion capability
# ---------------------------------------------------------------------------


def test_audit_never_mutates_state_or_roadmap(gold_set):
    before_state = STATE_PATH.read_bytes()
    before_roadmap = ROADMAP_PATH.read_bytes()

    report, manifest = _clean_report(gold_set)
    _audit(gold_set, report=report, manifest=manifest)
    _audit(gold_set, report=None, manifest=manifest)

    assert STATE_PATH.read_bytes() == before_state
    assert ROADMAP_PATH.read_bytes() == before_roadmap
    assert audit_mutates_planning_state() is False


def test_audit_module_is_pure_and_read_only(gold_set):
    assert audit_has_promotion_capability() is False
    assert audit_has_provider_capability() is False

    src = (QUAL_DIR / "audit.py").read_text(encoding="utf-8")
    for forbidden in (
        "litellm",
        "openai",
        "httpx",
        "asyncpg",
        "requests",
        "write_text",
        "STATE.md",
        "ROADMAP.md",
        "Path(",
    ):
        assert forbidden not in src, f"audit.py must stay pure: {forbidden}"
    # The audit consumes evidence only — no SUMMARY/VERIFICATION generation.
    for marker in ("29-04-SUMMARY", "29-04-VERIFICATION"):
        assert marker not in src
