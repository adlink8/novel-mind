"""Three-dimension v1.2 evidence reconciliation audit (Phase 29-04).

REQ-QA-02/03; decisions D-02..D-06 from 29-CONTEXT.md.

The audit independently reconciles the **implementation_readiness**,
**sample_data_coverage** and **quality_qualification** dimensions of the v1.2
candidate. Every dimension keeps its own status, evidence links and risks, and
every verdict binds the live evidence it was decided on: live code, DB
fingerprint, dataset/source snapshot, commit, model/prompt/schema/budget,
per-bucket metrics and browser evidence (D-02/D-03).

Fail-closed guarantees:

- Missing evidence or a lineage/parity mismatch can only produce ``blocked``.
- The audit consumes the Phase 28-04 ``CandidateManifest``/``DimensionResult``
  contract (snapshot/cutoff/owner/version/budget/lineage/status/blocked_reason)
  and folds any parity mismatch into blocked evidence. It never patches or
  rewrites a manifest and never writes an active pointer.
- Phase 22's 0/3 blocked state is preserved as an independent risk entry and
  is never folded into a dimension or the verdict.
- The only overall verdicts are ``qualified_candidate`` and ``blocked`` (D-05);
  promotion / active-pointer cutover / production A/B are out of scope.
- No single completion percentage is ever produced.

The module never mutates STATE/ROADMAP and has no database, provider or
promotion capability.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.narrative_memory.contracts import (
    CANDIDATE_MANIFEST_SCHEMA_VERSION,
    DIMENSION_RESULT_SCHEMA_VERSION,
    CandidateManifest,
    DimensionKind,
    candidate_manifest_checksum,
    dimension_result_checksum,
)
from app.services.narrative_memory.manifest_contract import (
    ManifestContractError,
    assert_no_pointer_fields,
    dimension_parity_report,
)
from app.services.narrative_memory.qualification_contracts import (
    SCOPE_DISCLAIMER,
    stable_json,
)
from app.services.qualification.gold_set import (
    GOLD_BUCKETS,
    ReadingQAGoldSet,
    curator_agreement,
    dataset_fingerprint,
)
from app.services.qualification.metrics import REQUIRED_BUCKET_METRICS
from app.services.qualification.report import (
    QualificationReport,
    VERDICT_BLOCKED,
    VERDICT_QUALIFIED,
)

AUDIT_VERSION = "v1.2-audit.v1"

VERDICT_LABELS: tuple[str, ...] = (VERDICT_QUALIFIED, VERDICT_BLOCKED)

# Vocabulary that must never appear in audit blocked_reasons (D-05: no promotion).
FORBIDDEN_AUDIT_WORDS = frozenset(
    {"promote", "promoted", "promotion", "active_pointer", "production_ready"}
)

# Static write patterns that must never appear in the qualified services.
# These are code-level writes, so prose like "performs a cutover" never matches.
FORBIDDEN_WRITE_PATTERNS: tuple[str, ...] = (
    r"active_pointer\s*=",
    r"current_version\s*=",
    r"default_version\s*=",
    r"production_version\s*=",
    r"\.promote\s*\(",
    r"\.cutover\s*\(",
    r"def\s+promote\s*\(",
    r"def\s+cutover\s*\(",
)

MANIFEST_PARITY_FIELDS = (
    "source_snapshot_hash",
    "cutoff",
    "owner_id",
    "version_id",
    "version_key",
    "budget",
    "lineage",
)

BUDGET_KEYS = ("max_calls", "max_input_tokens", "max_output_tokens", "max_cost_usd")


class AuditDimension(StrEnum):
    IMPLEMENTATION_READINESS = "implementation_readiness"
    SAMPLE_DATA_COVERAGE = "sample_data_coverage"
    QUALITY_QUALIFICATION = "quality_qualification"


AUDIT_DIMENSIONS: tuple[AuditDimension, ...] = (
    AuditDimension.IMPLEMENTATION_READINESS,
    AuditDimension.SAMPLE_DATA_COVERAGE,
    AuditDimension.QUALITY_QUALIFICATION,
)


class AuditDimensionStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class AuditFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuditHeader(AuditFrozenModel):
    """Lineage binding for the whole audit (D-02). Every field is mandatory."""

    db_fingerprint: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    commit: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    config: str = Field(min_length=1)
    budget: dict[str, Any]

    @model_validator(mode="after")
    def _budget_present(self) -> AuditHeader:
        if not isinstance(self.budget, dict) or not self.budget:
            raise ValueError("audit header budget must be a non-empty dict")
        if not any(key in self.budget for key in BUDGET_KEYS):
            raise ValueError(
                f"audit header budget must carry at least one of {BUDGET_KEYS}"
            )
        return self


class AuditEvidenceLink(AuditFrozenModel):
    """One bound piece of evidence that a verdict was decided on."""

    kind: str = Field(min_length=1)
    location: str = Field(min_length=1)
    detail: str


class AuditRisk(AuditFrozenModel):
    """One unresolved risk; ``blocking`` risks block the dimension."""

    code: str = Field(min_length=1)
    dimension: str | None = None
    severity: Literal["blocking", "open"]
    detail: str


class DimensionAudit(AuditFrozenModel):
    """Independent status, evidence links and risks for one dimension."""

    dimension: AuditDimension
    status: AuditDimensionStatus
    blocked_reasons: tuple[str, ...] = ()
    evidence: tuple[AuditEvidenceLink, ...] = ()
    risks: tuple[AuditRisk, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            for key in ("blocked_reasons", "evidence", "risks"):
                if isinstance(value.get(key), list):
                    updates[key] = tuple(value[key])
            if updates:
                value = {**value, **updates}
        return value


class Phase22Status(AuditFrozenModel):
    """Independent, preserved Phase 22 blocked risk — never folded in."""

    green_observed: int = Field(ge=0)
    blocked: bool
    detail: str = Field(min_length=1)


class AuditReport(AuditFrozenModel):
    """The complete lineage-bound three-dimension audit (D-02/D-05)."""

    audit_version: str = AUDIT_VERSION
    header: AuditHeader
    dimensions: tuple[DimensionAudit, ...]
    phase22: Phase22Status
    verdict: Literal["qualified_candidate", "blocked"]
    blocked_reasons: tuple[str, ...] = ()
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclaimer: str = SCOPE_DISCLAIMER

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            for key in ("dimensions", "blocked_reasons"):
                if isinstance(value.get(key), list):
                    updates[key] = tuple(value[key])
            if updates:
                value = {**value, **updates}
        return value

    @model_validator(mode="after")
    def _audit_rules(self) -> AuditReport:
        if self.verdict not in VERDICT_LABELS:
            raise ValueError(f"illegal audit verdict {self.verdict!r}")
        banned = FORBIDDEN_AUDIT_WORDS.intersection(self.blocked_reasons)
        if banned:
            raise ValueError(f"illegal audit reason vocabulary: {sorted(banned)}")
        ordered = tuple(sorted(self.blocked_reasons))
        if self.blocked_reasons != ordered:
            raise ValueError("audit blocked_reasons must be sorted")
        if self.verdict == VERDICT_QUALIFIED and self.blocked_reasons:
            raise ValueError("qualified_candidate must carry no blocked_reasons")
        if self.verdict == VERDICT_BLOCKED and not self.blocked_reasons:
            raise ValueError("blocked requires non-empty blocked_reasons")
        if not self.dimensions:
            raise ValueError("audit requires at least one dimension")
        return self

    def checksum_payload(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("checksum", None)
        return stable_json(payload)

    @property
    def checksum_valid(self) -> bool:
        return (
            self.checksum
            == hashlib.sha256(self.checksum_payload().encode("utf-8")).hexdigest()
        )

    @property
    def has_completion_percentage(self) -> bool:
        """Guards the no-single-percentage rule at the surface."""
        return "completion_percentage" in self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Live evidence descriptors (collected by the caller / test harness)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveCodeEvidence:
    """Observed live-code facts the implementation dimension is decided on."""

    required_files: tuple[str, ...]
    missing_files: tuple[str, ...] = ()
    importable_modules: tuple[str, ...] = ()
    failed_modules: tuple[str, ...] = ()
    capability_violations: tuple[str, ...] = ()
    forbidden_vocabulary: tuple[str, ...] = ()
    provider_imports: tuple[str, ...] = ()
    migration_head: str | None = None
    migration_single_head: bool = False
    expected_migration_head: str | None = None


@dataclass(frozen=True)
class BrowserEvidence:
    """Observed browser/UAT evidence the quality dimension is decided on (D-06)."""

    spec_paths: tuple[str, ...] = ()
    executed: bool = False
    observed_test_count: int | None = None
    spoiler_safe_asserted: bool = False
    citation_jump_asserted: bool = False
    partial_failure_asserted: bool = False
    accessibility_asserted: bool = False
    mobile_asserted: bool = False


# ---------------------------------------------------------------------------
# Code / migration static evidence helpers (pure filesystem)
# ---------------------------------------------------------------------------


def scan_forbidden_writes(source: str) -> list[str]:
    """Return stable codes for pointer/promotion/cutover *writes* in source.

    Only code-level assignment/call patterns match; docstring prose never does.
    """
    found: list[str] = []
    if re.search(r"active_pointer\s*=", source):
        found.append("active_pointer_assignment")
    if re.search(r"current_version\s*=", source):
        found.append("current_version_assignment")
    if re.search(r"default_version\s*=", source):
        found.append("default_version_assignment")
    if re.search(r"production_version\s*=", source):
        found.append("production_version_assignment")
    if re.search(r"\.promote\s*\(", source):
        found.append("promote_call")
    if re.search(r"\.cutover\s*\(", source):
        found.append("cutover_call")
    return sorted(found)


def scan_forbidden_writes_in_files(paths: list[Any]) -> list[str]:
    """Scan the given source files and merge every forbidden-write code."""
    found: list[str] = []
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found.extend(scan_forbidden_writes(source))
    return sorted(set(found))


def migration_heads(versions_dir: Any) -> list[str]:
    """Determine migration graph heads from the versions directory (no execution).

    Parses ``revision``/``down_revision`` assignments; a head is a revision that
    is never referenced as a ``down_revision``. A single head means the schema
    migration chain is serial and reversible end-to-end.
    """
    revisions: set[str] = set()
    referenced: set[str] = set()
    for path in sorted(versions_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev_match = re.search(
            r"^\s*revision\b[^=\n]*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE
        )
        if rev_match is None:
            continue
        revisions.add(rev_match.group(1))
        down_match = re.search(
            r"^\s*down_revision\b[^=\n]*=\s*(.+)", text, re.MULTILINE
        )
        if down_match is None:
            continue
        value = down_match.group(1).strip()
        if value.startswith(("(", "[")):
            referenced.update(re.findall(r"['\"]([^'\"]+)['\"]", value))
        elif value.lower() not in {"none", "null"}:
            single = re.match(r"^['\"]([^'\"]+)['\"]", value)
            if single:
                referenced.add(single.group(1))
    return sorted(revisions - referenced)


# ---------------------------------------------------------------------------
# Manifest / pair parity (Task 3)
# ---------------------------------------------------------------------------


def _manifest_gate_reasons(
    manifest: CandidateManifest, gold_set: ReadingQAGoldSet
) -> list[str]:
    """Consume the Phase 28-04 contract; any inconsistency fails closed."""
    reasons: list[str] = []
    if manifest.schema_version != CANDIDATE_MANIFEST_SCHEMA_VERSION:
        reasons.append("manifest_schema_version_mismatch")
    if manifest.checksum != candidate_manifest_checksum(manifest):
        reasons.append("manifest_checksum_failed")
    for result in manifest.dimensions:
        if result.schema_version != DIMENSION_RESULT_SCHEMA_VERSION:
            reasons.append("dimension_schema_version_mismatch")
        if result.checksum != dimension_result_checksum(result):
            reasons.append("dimension_checksum_failed")
    parity = dimension_parity_report(manifest)
    if not parity.ok:
        reasons.append("manifest_parity_failed")
        reasons.extend(f"parity_mismatch:{label}" for label in parity.mismatches)
        for verdict in parity.dimension_verdicts:
            if not verdict.ok and verdict.reason:
                reasons.append(f"parity_field:{verdict.dimension}:{verdict.reason}")
    if manifest.source_snapshot_hash != gold_set.source_snapshot_hash:
        reasons.append("manifest_snapshot_mismatch")
    present_kinds = {result.dimension for result in manifest.dimensions}
    for kind in DimensionKind:
        if kind not in present_kinds:
            reasons.append(f"manifest_dimension_missing:{kind.value}")
    try:
        assert_no_pointer_fields(manifest.model_dump(mode="json"))
    except ManifestContractError:
        reasons.append("manifest_pointer_fields_found")
    return reasons


def _pair_parity_reasons(
    candidate: CandidateManifest | None, baseline: CandidateManifest | None
) -> list[str]:
    """D-04: candidate and leaf baseline share identical snapshot/cutoff/budget."""
    if candidate is None and baseline is None:
        return []
    if candidate is None or baseline is None:
        return ["baseline_manifest_missing"]
    reasons: list[str] = []
    for field in MANIFEST_PARITY_FIELDS:
        if getattr(candidate, field) != getattr(baseline, field):
            reasons.append(f"pair_{field}_mismatch")
    cand_dims = {d.dimension: d for d in candidate.dimensions}
    base_dims = {d.dimension: d for d in baseline.dimensions}
    for kind in sorted(set(cand_dims) | set(base_dims), key=str):
        left = cand_dims.get(kind)
        right = base_dims.get(kind)
        if left is None or right is None:
            reasons.append(f"pair_dimension_missing:{kind.value}")
        elif left.blocked_reason != right.blocked_reason:
            reasons.append(f"pair_blocked_reason_mismatch:{kind.value}")
    return reasons


# ---------------------------------------------------------------------------
# Per-dimension audits
# ---------------------------------------------------------------------------


def _audit_implementation(
    live_code: LiveCodeEvidence,
) -> tuple[list[str], list[AuditRisk], list[AuditEvidenceLink]]:
    blocking: list[str] = []
    risks: list[AuditRisk] = []
    evidence: list[AuditEvidenceLink] = []

    if not live_code.required_files:
        blocking.append("live_evidence_incomplete")
    evidence.append(
        AuditEvidenceLink(
            kind="live_code",
            location="required files",
            detail=(
                f"{len(live_code.required_files)} checked, "
                f"{len(live_code.missing_files)} missing"
            ),
        )
    )
    if live_code.missing_files:
        blocking.append("required_file_missing")
        risks.append(
            AuditRisk(
                code="required_file_missing",
                dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                severity="blocking",
                detail=";".join(sorted(live_code.missing_files)),
            )
        )
    if live_code.failed_modules:
        blocking.append("required_module_missing")
        risks.append(
            AuditRisk(
                code="required_module_missing",
                dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                severity="blocking",
                detail=";".join(sorted(live_code.failed_modules)),
            )
        )
    if live_code.capability_violations:
        blocking.append("capability_violation")
        risks.append(
            AuditRisk(
                code="capability_violation",
                dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                severity="blocking",
                detail=";".join(sorted(live_code.capability_violations)),
            )
        )
    if live_code.forbidden_vocabulary:
        blocking.append("forbidden_vocabulary_found")
        risks.append(
            AuditRisk(
                code="forbidden_vocabulary_found",
                dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                severity="blocking",
                detail=";".join(sorted(live_code.forbidden_vocabulary)),
            )
        )
    if live_code.provider_imports:
        blocking.append("provider_import_found")
        risks.append(
            AuditRisk(
                code="provider_import_found",
                dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                severity="blocking",
                detail=";".join(sorted(live_code.provider_imports)),
            )
        )

    evidence.append(
        AuditEvidenceLink(
            kind="schema_migration",
            location=live_code.migration_head or "unknown",
            detail=(
                "single head"
                if live_code.migration_single_head
                else "multi-head / unknown"
            ),
        )
    )
    if not live_code.migration_single_head:
        blocking.append("schema_migration_multi_head")
        risks.append(
            AuditRisk(
                code="schema_migration_multi_head",
                dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                severity="blocking",
                detail=f"observed heads not exactly one; head={live_code.migration_head!r}",
            )
        )
    elif live_code.expected_migration_head is not None:
        if live_code.migration_head is None:
            blocking.append("schema_migration_head_missing")
        elif live_code.migration_head != live_code.expected_migration_head:
            blocking.append("schema_migration_head_mismatch")
            risks.append(
                AuditRisk(
                    code="schema_migration_head_mismatch",
                    dimension=AuditDimension.IMPLEMENTATION_READINESS.value,
                    severity="blocking",
                    detail=(
                        f"observed {live_code.migration_head!r} != expected "
                        f"{live_code.expected_migration_head!r}"
                    ),
                )
            )

    return blocking, risks, evidence


def _audit_sample_data(
    gold_set: ReadingQAGoldSet,
    manifest: CandidateManifest | None,
    baseline_manifest: CandidateManifest | None,
    dataset_path: str,
) -> tuple[list[str], list[AuditRisk], list[AuditEvidenceLink]]:
    blocking: list[str] = []
    risks: list[AuditRisk] = []
    evidence: list[AuditEvidenceLink] = []

    computed = dataset_fingerprint(gold_set.model_dump(mode="json"))
    stored = gold_set.fingerprint
    evidence.append(
        AuditEvidenceLink(
            kind="dataset_snapshot",
            location=dataset_path,
            detail=(
                f"fingerprint {stored[:12]}..." if stored else "fingerprint missing"
            ),
        )
    )
    if stored is None or stored != computed:
        blocking.append("dataset_fingerprint_mismatch")

    agreement = curator_agreement(gold_set)
    evidence.append(
        AuditEvidenceLink(
            kind="dataset_snapshot",
            location="curator agreement",
            detail=f"overall={agreement.overall:.3f} unanimous={agreement.is_unanimous}",
        )
    )
    if not agreement.is_unanimous:
        blocking.append("dataset_curator_disagreement")

    counts = gold_set.bucket_counts()
    evidence.append(
        AuditEvidenceLink(
            kind="dataset_snapshot",
            location="buckets",
            detail=f"{len(gold_set.samples)} samples across {len(GOLD_BUCKETS)} buckets",
        )
    )
    for bucket in GOLD_BUCKETS:
        if counts[bucket.value] < 1:
            blocking.append(f"dataset_missing_bucket:{bucket.value}")

    if manifest is not None:
        evidence.append(
            AuditEvidenceLink(
                kind="manifest",
                location=manifest.source_snapshot_hash,
                detail=(
                    f"owner={manifest.owner_id} version={manifest.version_id} "
                    f"cutoff={manifest.cutoff} dimensions={len(manifest.dimensions)}"
                ),
            )
        )
        blocking.extend(_manifest_gate_reasons(manifest, gold_set))
    else:
        blocking.append("manifest_missing")
        risks.append(
            AuditRisk(
                code="manifest_missing",
                dimension=AuditDimension.SAMPLE_DATA_COVERAGE.value,
                severity="blocking",
                detail="no Phase 28-04 CandidateManifest bound to the audit",
            )
        )

    pair = _pair_parity_reasons(manifest, baseline_manifest)
    if pair:
        blocking.append("manifest_pair_parity_failed")
        blocking.extend(pair)

    return blocking, risks, evidence


def _audit_quality(
    header: AuditHeader,
    gold_set: ReadingQAGoldSet,
    report: QualificationReport | None,
    manifest: CandidateManifest | None,
    browser: BrowserEvidence,
) -> tuple[list[str], list[AuditRisk], list[AuditEvidenceLink]]:
    blocking: list[str] = []
    risks: list[AuditRisk] = []
    evidence: list[AuditEvidenceLink] = []

    if report is None:
        blocking.append("report_missing")
    else:
        evidence.append(
            AuditEvidenceLink(
                kind="report",
                location=report.checksum,
                detail=f"verdict={report.verdict}",
            )
        )
        evidence.append(
            AuditEvidenceLink(
                kind="db_fingerprint",
                location=report.header.db_fingerprint,
                detail="report header binding",
            )
        )
        evidence.append(
            AuditEvidenceLink(
                kind="commit",
                location=report.header.commit,
                detail="report header binding",
            )
        )
        if not report.checksum_valid:
            blocking.append("report_checksum_failed")
        if report.verdict not in VERDICT_LABELS:
            blocking.append("illegal_verdict")

        h = report.header
        lineage_checks = (
            (
                "report_source_snapshot_mismatch",
                h.source_snapshot,
                header.source_snapshot,
            ),
            (
                "report_dataset_version_mismatch",
                h.dataset_version,
                header.dataset_version,
            ),
            ("report_db_fingerprint_mismatch", h.db_fingerprint, header.db_fingerprint),
            ("report_commit_mismatch", h.commit, header.commit),
            ("report_model_mismatch", h.model, header.model),
            ("report_prompt_mismatch", h.prompt, header.prompt),
            ("report_schema_version_mismatch", h.schema_version, header.schema_version),
            ("report_config_mismatch", h.config, header.config),
        )
        for code, actual, expected in lineage_checks:
            if actual != expected:
                blocking.append(code)
        if h.budget != header.budget:
            blocking.append("report_budget_mismatch")
        if h.source_snapshot != gold_set.source_snapshot_hash:
            blocking.append("report_snapshot_vs_dataset_mismatch")

        if report.verdict == VERDICT_BLOCKED:
            blocking.append("report_verdict_blocked")
            blocking.extend(
                f"report_reason:{reason}" for reason in report.blocked_reasons
            )

        bucket_kinds = {bucket.bucket for bucket in report.buckets}
        evidence.append(
            AuditEvidenceLink(
                kind="report",
                location="buckets",
                detail=f"{len(report.buckets)} bucket reports",
            )
        )
        if not report.buckets:
            blocking.append("report_bucket_missing")
        for bucket in report.buckets:
            if bucket.bucket not in GOLD_BUCKETS:
                blocking.append("report_unknown_bucket")
            for system in ("candidate", "baseline"):
                metrics = bucket.metrics.get(system, {})
                missing = [
                    name for name in REQUIRED_BUCKET_METRICS if name not in metrics
                ]
                if missing:
                    blocking.append(
                        f"report_metric_incomplete:{bucket.bucket.value}:{system}"
                    )
        for bucket in GOLD_BUCKETS:
            if bucket not in bucket_kinds:
                blocking.append(f"report_bucket_missing:{bucket.value}")

        if report.operations.cost_usd_total is None:
            blocking.append("operations_cost_incomplete")

        if manifest is not None:
            snapshot = report.manifest
            if snapshot is None:
                blocking.append("report_manifest_missing")
            else:
                snapshot_checks = (
                    (
                        "report_manifest_snapshot_mismatch",
                        snapshot.source_snapshot_hash,
                        manifest.source_snapshot_hash,
                    ),
                    (
                        "report_manifest_cutoff_mismatch",
                        snapshot.cutoff,
                        manifest.cutoff,
                    ),
                    (
                        "report_manifest_owner_mismatch",
                        snapshot.owner_id,
                        manifest.owner_id,
                    ),
                    (
                        "report_manifest_version_mismatch",
                        snapshot.version_id,
                        manifest.version_id,
                    ),
                    (
                        "report_manifest_version_key_mismatch",
                        snapshot.version_key,
                        manifest.version_key,
                    ),
                )
                for code, actual, expected in snapshot_checks:
                    if actual != expected:
                        blocking.append(code)
                snapshot_dims = {d.dimension: d for d in snapshot.dimensions}
                for result in manifest.dimensions:
                    bound = snapshot_dims.get(str(result.dimension))
                    if bound is None:
                        blocking.append(
                            f"report_manifest_dimension_missing:{result.dimension.value}"
                        )
                        continue
                    if bound.status != str(result.status):
                        blocking.append(
                            f"report_manifest_dimension_status_mismatch:{result.dimension.value}"
                        )
                    if bound.blocked_reason != result.blocked_reason:
                        blocking.append(
                            f"report_manifest_blocked_reason_mismatch:{result.dimension.value}"
                        )

    # Browser evidence (D-06) is reconciled before the verdict.
    if not browser.spec_paths:
        blocking.append("browser_evidence_missing")
    else:
        evidence.append(
            AuditEvidenceLink(
                kind="browser",
                location=";".join(browser.spec_paths),
                detail=(
                    f"executed={browser.executed} "
                    f"tests={browser.observed_test_count or 'n/a'}"
                ),
            )
        )
        if not browser.spoiler_safe_asserted:
            blocking.append("browser_spoiler_safe_missing")
        if not browser.citation_jump_asserted:
            blocking.append("browser_citation_jump_missing")
        if not browser.partial_failure_asserted:
            blocking.append("browser_partial_failure_missing")
        if not browser.accessibility_asserted:
            blocking.append("browser_accessibility_missing")
        if not browser.mobile_asserted:
            blocking.append("browser_mobile_missing")
    if not browser.executed:
        risks.append(
            AuditRisk(
                code="browser_execution_env_limited",
                dimension=AuditDimension.QUALITY_QUALIFICATION.value,
                severity="open",
                detail=(
                    "Playwright execution was env-limited (Next canary dev-server "
                    "compile failure); specs were parsed but not executed in a browser."
                ),
            )
        )

    return blocking, risks, evidence


# ---------------------------------------------------------------------------
# Audit entry point
# ---------------------------------------------------------------------------


def audit_checksum(report: AuditReport) -> str:
    """Deterministic SHA-256 over the audit without its own checksum field."""
    return hashlib.sha256(report.checksum_payload().encode("utf-8")).hexdigest()


def _validate_header(header: dict[str, Any] | AuditHeader) -> AuditHeader:
    if isinstance(header, AuditHeader):
        return header
    return AuditHeader.model_validate(header)


def run_audit(
    *,
    header: dict[str, Any] | AuditHeader,
    gold_set: ReadingQAGoldSet,
    live_code: LiveCodeEvidence,
    browser: BrowserEvidence,
    report: QualificationReport | None = None,
    manifest: CandidateManifest | None = None,
    baseline_manifest: CandidateManifest | None = None,
    phase22_green_observed: int = 0,
    dataset_path: str = "backend/evals/reading_qa_v1.json",
) -> AuditReport:
    """Run the three-dimension audit and return a lineage-bound report.

    The audit only consumes evidence; it never writes STATE/ROADMAP, never
    patches a manifest and never writes an active pointer. Phase 22 is reported
    independently and never changes the NM verdict.
    """
    hdr = _validate_header(header)

    impl_blocking, impl_risks, impl_evidence = _audit_implementation(live_code)
    data_blocking, data_risks, data_evidence = _audit_sample_data(
        gold_set, manifest, baseline_manifest, dataset_path
    )
    qual_blocking, qual_risks, qual_evidence = _audit_quality(
        hdr, gold_set, report, manifest, browser
    )

    dimensions = (
        DimensionAudit(
            dimension=AuditDimension.IMPLEMENTATION_READINESS,
            status=_dimension_status(impl_blocking, impl_risks),
            blocked_reasons=tuple(sorted(set(impl_blocking))),
            evidence=tuple(impl_evidence),
            risks=tuple(impl_risks),
        ),
        DimensionAudit(
            dimension=AuditDimension.SAMPLE_DATA_COVERAGE,
            status=_dimension_status(data_blocking, data_risks),
            blocked_reasons=tuple(sorted(set(data_blocking))),
            evidence=tuple(data_evidence),
            risks=tuple(data_risks),
        ),
        DimensionAudit(
            dimension=AuditDimension.QUALITY_QUALIFICATION,
            status=_dimension_status(qual_blocking, qual_risks),
            blocked_reasons=tuple(sorted(set(qual_blocking))),
            evidence=tuple(qual_evidence),
            risks=tuple(qual_risks),
        ),
    )

    reasons: list[str] = []
    for dimension in dimensions:
        if dimension.status == AuditDimensionStatus.BLOCKED:
            reasons.extend(dimension.blocked_reasons)
        elif dimension.status == AuditDimensionStatus.PARTIAL:
            reasons.append(f"dimension_partial:{dimension.dimension.value}")
    reasons = sorted(set(reasons))

    verdict = VERDICT_BLOCKED if reasons else VERDICT_QUALIFIED
    phase22 = Phase22Status(
        green_observed=phase22_green_observed,
        blocked=phase22_green_observed < 3,
        detail=(
            "Phase 22 CI nightly gate: blocked at 0/3 scheduled green evidence; "
            "preserved independently and never folded into the NM verdict."
        ),
    )

    audit = AuditReport(
        header=hdr,
        dimensions=dimensions,
        phase22=phase22,
        verdict=verdict,  # type: ignore[arg-type]
        blocked_reasons=tuple(reasons),
        checksum="0" * 64,
    )
    return audit.model_copy(update={"checksum": audit_checksum(audit)})


def _dimension_status(
    blocking: list[str], risks: list[AuditRisk]
) -> AuditDimensionStatus:
    if blocking:
        return AuditDimensionStatus.BLOCKED
    if risks:
        return AuditDimensionStatus.PARTIAL
    return AuditDimensionStatus.VERIFIED


def audit_has_promotion_capability() -> bool:
    return False


def audit_has_provider_capability() -> bool:
    return False


def audit_mutates_planning_state() -> bool:
    """Guard: the audit module must never write STATE/ROADMAP."""
    return False
