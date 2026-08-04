"""Deterministic contradiction and consistency gates (Phase 37-03).

D-37-03 / REQ-FORK-03 / REQ-CRE-06 / D-37-05: after the strict-schema candidate
is produced, deterministic server code evaluates character behavior, canon
facts, timeline order/causality, unresolved clue payoff, world rules, evidence
references and scope against the sealed context package — any conflict fails
closed (``blocked``) and is never silently repaired into canon. An explicit
derivative override (``CanonDelta``) is ``needs_override`` — never
auto-accepted. The gate verdict carries disabled-by-default
``BranchSuggestion[]`` that only describe candidate branches: they never
auto-fork, never write Canon, and never grant or reuse any approval.

Principles (following ``services/creative_consistency.py``):

- **Consume structured claims only.** The checker never infers facts from
  free-form prose; character/fact/timeline/clue contradictions are declared as
  structured ``ContinuityClaim`` items with evidence keys, a chapter number and
  a disposition.
- **Every violation is locatable** to a package/candidate field
  (``ConsistencyViolation.field``), so red-team tests can prove which frozen
  package field a forged claim contradicts (T-37-03-01).
- **Stable reason codes.** Each violation carries a deterministic ``code``;
  a blocked verdict is never silently repaired and the candidate row keeps its
  reason.
- **Fail-closed dimensions.** A factual dimension that is ``unavailable`` or
  ``blocked`` blocks qualification — a continuation cannot be qualified against
  facts the package honestly does not contain.
- **Spoiler guard.** A claim whose chapter number is beyond the frozen cutoff
  is ``cutoff_exceeded`` (T-37-03-02).
- **Branch suggestions are inert.** ``evaluate_consistency`` only validates and
  re-emits the candidate's suggestions; no fork/Canon/approval side effect
  exists in this module (REQ-FORK-06).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.derivative_generation.candidate import (
    CandidateDraft,
    GateVerdict,
)
from app.services.derivative_generation.context_package import (
    ContextPackageError,
    DimensionStatus,
    verify_package_hash,
)

# Stable violation codes (failure policy, 37-VALIDATION).
CODE_PACKAGE_HASH_MISMATCH = "package_hash_mismatch"
CODE_SCOPE_DENIED = "scope_denied"
CODE_CROSS_FORK = "cross_fork"
CODE_INCOMPLETE_LINEAGE = "incomplete_lineage"
CODE_INTENT_MISMATCH = "intent_mismatch"
CODE_BUDGET_EXHAUSTED = "budget_exhausted"
CODE_EVIDENCE_OUTSIDE_PACKAGE = "evidence_outside_package"
CODE_BRANCH_EVIDENCE_OUTSIDE_PACKAGE = "branch_evidence_outside_package"
CODE_DIVERGENCE_EVIDENCE_OUTSIDE_PACKAGE = "divergence_evidence_outside_package"
CODE_MISSING_EVIDENCE = "missing_evidence"
CODE_CUTOFF_EXCEEDED = "cutoff_exceeded"
CODE_CHARACTER_CONTRADICTION = "character_contradiction"
CODE_FACT_CONTRADICTION = "fact_contradiction"
CODE_TIMELINE_CONTRADICTION = "timeline_contradiction"
CODE_CLUE_CONTRADICTION = "clue_contradiction"
CODE_CHARACTER_OUTSIDE_PACKAGE = "character_outside_package"
CODE_CLUE_OUTSIDE_PACKAGE = "clue_outside_package"
CODE_TIMELINE_CYCLE = "timeline_cycle"
CODE_TIMELINE_MISSING_EVENT = "timeline_missing_event"
CODE_CLUE_WITHOUT_EVIDENCE = "clue_without_evidence"
CODE_UNCERTAIN = "uncertain"

# Divergence covered by the verdict driver; a clean run has no reason.
CODE_DIVERGENCE_REQUIRES_OVERRIDE = "divergence_requires_override"

# D-37-03 fail-closed dimension gate: these factual dimensions are required for
# a qualified continuation/rewrite (user_intent is structural, always present).
QUALIFICATION_DIMENSIONS = (
    "world_state",
    "timeline",
    "unresolved_clues",
    "world_rules",
    "evidence",
)

# Claim category -> CanonDelta divergence_type that would *declare* the
# contradiction (D-37-03: explicit override covers only its own class).
CLAIM_CATEGORY_TO_DIVERGENCE_TYPE = {
    "character_behavior": "character",
    "established_fact": "other",
    "timeline": "timeline",
    "clue": "clue",
}

# Clue lifecycle states that still mean "open at the cutoff".
OPEN_CLUE_STATES = frozenset({"candidate", "active", "reinforced"})


class GateViolationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ContinuityClaimCategory(StrEnum):
    CHARACTER_BEHAVIOR = "character_behavior"
    ESTABLISHED_FACT = "established_fact"
    TIMELINE = "timeline"
    CLUE = "clue"


class ClaimDisposition(StrEnum):
    CONSISTENT = "consistent"
    CONTRADICTION = "contradiction"
    UNKNOWN = "unknown"


class StrictGateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContinuityClaim(StrictGateModel):
    """Structured, deterministic claim surface of a derivative candidate.

    The model may not assert prose; every claim is located to a package field
    via ``entity_key``/``clue_id`` and is grounded by evidence keys.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_key: str = Field(min_length=1, max_length=160)
    category: ContinuityClaimCategory
    # Locates a character-behavior claim to a world_state entity.
    entity_key: str | None = Field(default=None, min_length=1, max_length=160)
    # Locates a clue claim to an unresolved_clues entry.
    clue_id: str | None = Field(default=None, min_length=1, max_length=160)
    evidence_keys: list[str] = Field(default_factory=list, max_length=8)
    chapter_number: int | None = Field(default=None, ge=1)
    disposition: ClaimDisposition = ClaimDisposition.UNKNOWN

    @model_validator(mode="after")
    def _category_lookup(self) -> "ContinuityClaim":
        if (
            self.category is ContinuityClaimCategory.CHARACTER_BEHAVIOR
            and not self.entity_key
        ):
            raise ValueError(
                "character_behavior claims must carry an entity_key"
            )
        if self.category is ContinuityClaimCategory.CLUE and not self.clue_id:
            raise ValueError("clue claims must carry a clue_id")
        return self


class ConsistencyViolation:
    """One deterministic gate finding, locatable to a package/candidate field."""

    __slots__ = ("code", "severity", "field", "evidence_keys", "detail", "claim_key")

    def __init__(
        self,
        code: str,
        *,
        severity: GateViolationSeverity = GateViolationSeverity.ERROR,
        field: str = "",
        evidence_keys: list[str] | None = None,
        detail: str = "",
        claim_key: str | None = None,
    ) -> None:
        self.code = code
        self.severity = severity
        self.field = field
        self.evidence_keys = list(evidence_keys or [])
        self.detail = detail
        self.claim_key = claim_key

    @property
    def is_error(self) -> bool:
        return self.severity is GateViolationSeverity.ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "field": self.field,
            "evidence_keys": list(self.evidence_keys),
            "detail": self.detail,
            "claim_key": self.claim_key,
        }


class ConsistencyGateResult:
    """Deterministic gate outcome with violations and candidate branch output.

    ``branch_suggestions`` are always disabled-by-default candidate outputs
    (D-37-05); a blocked/override verdict still carries them so the UI can show
    disabled branches without any side effect.
    """

    __slots__ = ("verdict", "violations", "branch_suggestions", "detail")

    def __init__(
        self,
        verdict: GateVerdict,
        violations: list[ConsistencyViolation],
        branch_suggestions: list[dict[str, Any]],
        detail: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.violations = list(violations)
        self.branch_suggestions = [dict(s) for s in branch_suggestions]
        self.detail = detail

    @property
    def errors(self) -> list[ConsistencyViolation]:
        return [v for v in self.violations if v.is_error]

    @property
    def reason(self) -> str | None:
        """Stable primary reason code (first error, else override, else None)."""
        if self.errors:
            return self.errors[0].code
        if self.verdict is GateVerdict.NEEDS_OVERRIDE:
            return CODE_DIVERGENCE_REQUIRES_OVERRIDE
        return None

    def has_code(self, code: str) -> bool:
        return any(v.code == code for v in self.violations)


# ---------------------------------------------------------------------------
# Pure package view helpers (deterministic, DB-free)
# ---------------------------------------------------------------------------


def package_dimension(package: dict[str, Any], name: str) -> dict[str, Any]:
    return ((package.get("dimensions") or {}).get(name) or {}) or {}


def package_evidence_allowlist(package: dict[str, Any]) -> set[str]:
    """Allowlisted citation keys from the sealed package evidence dimension."""
    allowed: set[str] = set()
    for item in package_dimension(package, "evidence").get("items") or []:
        if isinstance(item, dict) and item.get("candidate_key"):
            allowed.add(str(item["candidate_key"]))
    return allowed


def package_cutoff(package: dict[str, Any]) -> int:
    version = package.get("version") or {}
    through = version.get("through_chapter")
    return int(through) if isinstance(through, int) and through > 0 else 0


def _world_state_entity_keys(package: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for item in package_dimension(package, "world_state").get("items") or []:
        if isinstance(item, dict) and item.get("entity_key"):
            keys.add(str(item["entity_key"]))
    return keys


def _unresolved_clue_ids(package: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in package_dimension(package, "unresolved_clues").get("items") or []:
        if isinstance(item, dict) and item.get("logical_clue_id"):
            ids.add(str(item["logical_clue_id"]))
    return ids


def _timeline_views(
    package: dict[str, Any],
) -> tuple[set[str], list[dict[str, Any]]]:
    """Return (event_keys, causal_edges) from the timeline dimension."""
    event_keys: set[str] = set()
    edges: list[dict[str, Any]] = []
    for item in package_dimension(package, "timeline").get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("event_key"):
            event_keys.add(str(item["event_key"]))
        if item.get("edge_key") and item.get("source_event_key"):
            edges.append(item)
    return event_keys, edges


# ---------------------------------------------------------------------------
# Deterministic gate checks (each returns a list of violations)
# ---------------------------------------------------------------------------


def check_package_scope(
    package: dict[str, Any],
    *,
    owner_id: int,
    novel_id: int,
    fork_id: int,
    expected_package_hash: str,
) -> list[ConsistencyViolation]:
    """Hash replay + scope/lineage fail-closed checks (REQ-FORK-03)."""
    violations: list[ConsistencyViolation] = []
    try:
        verify_package_hash(dict(package or {}), expected_package_hash)
    except ContextPackageError as exc:
        violations.append(
            ConsistencyViolation(
                CODE_PACKAGE_HASH_MISMATCH,
                field="package_hash",
                detail=exc.detail,
            )
        )
        # A forged package cannot be trusted for any further check.
        return violations
    if (package.get("space") or "") != "fanfiction_canon":
        violations.append(
            ConsistencyViolation(
                CODE_SCOPE_DENIED,
                field="space",
                detail=f"package space {package.get('space')!r} is not fanfiction_canon",
            )
        )
    mismatches = [
        label
        for label, actual, expected in (
            ("owner_id", package.get("owner_id"), owner_id),
            ("novel_id", package.get("novel_id"), novel_id),
            ("fork_id", package.get("fork_id"), fork_id),
        )
        if actual != expected
    ]
    if mismatches:
        violations.append(
            ConsistencyViolation(
                CODE_CROSS_FORK,
                field="owner_id/novel_id/fork_id",
                detail=(
                    "package is bound to a different fork scope: "
                    f"{','.join(mismatches)}"
                ),
            )
        )
    version = package.get("version") or {}
    missing_lineage = [
        field
        for field in (
            "source_version_key",
            "source_snapshot_hash",
            "through_chapter",
            "full_book_authorized",
            "cutoff_snapshot_hash",
            "scope_hash",
            "manifest_hash",
        )
        if field not in version
    ]
    if missing_lineage:
        violations.append(
            ConsistencyViolation(
                CODE_INCOMPLETE_LINEAGE,
                field="version",
                detail=f"missing lineage fields: {sorted(missing_lineage)}",
            )
        )
    return violations


def check_intent(draft: CandidateDraft, package_intent: str) -> list[ConsistencyViolation]:
    if draft.intent.value != package_intent:
        return [
            ConsistencyViolation(
                CODE_INTENT_MISMATCH,
                field="intent",
                detail=(
                    f"candidate intent {draft.intent.value} != "
                    f"package intent {package_intent}"
                ),
            )
        ]
    return []


def check_budget(
    package: dict[str, Any],
    *,
    budget_policy: Any | None = None,
) -> list[ConsistencyViolation]:
    """Re-check the sealed budget estimate; a block fails closed pre-call."""
    from app.services.derivative_generation.context_package import budget_verdict

    estimate = package.get("budget_estimate") or {}
    if estimate.get("blocked"):
        return [
            ConsistencyViolation(
                CODE_BUDGET_EXHAUSTED,
                field="budget_estimate.blocked",
                detail=(
                    f"sealed budget is blocked: "
                    f"{estimate.get('block_reason', 'budget_exhausted')}"
                ),
            )
        ]
    replay = budget_verdict(package, budget_policy)
    if replay.get("blocked"):
        return [
            ConsistencyViolation(
                CODE_BUDGET_EXHAUSTED,
                field="budget_estimate",
                detail=(
                    "package re-evaluation exceeds the budget policy "
                    f"({replay.get('estimated_input_tokens')} input tokens)"
                ),
            )
        ]
    return []


def check_dimension_availability(package: dict[str, Any]) -> list[ConsistencyViolation]:
    """A factual dimension that is unavailable/blocked fails closed (D-37-03)."""
    violations: list[ConsistencyViolation] = []
    for name in QUALIFICATION_DIMENSIONS:
        dimension = package_dimension(package, name)
        status = dimension.get("status") or DimensionStatus.UNAVAILABLE.value
        if status == DimensionStatus.UNAVAILABLE.value:
            violations.append(
                ConsistencyViolation(
                    f"dimension_unavailable:{name}",
                    field=f"dimensions.{name}.status",
                    detail=(
                        f"the {name} dimension is unavailable in the frozen "
                        "package; the continuation cannot be qualified against "
                        "facts the package honestly does not contain"
                    ),
                )
            )
        elif status == DimensionStatus.BLOCKED.value:
            block_reason = dimension.get("block_reason")
            violations.append(
                ConsistencyViolation(
                    f"dimension_blocked:{name}",
                    field=f"dimensions.{name}.status",
                    detail=(
                        f"the {name} dimension is blocked in the frozen package"
                        + (f" ({block_reason})" if block_reason else "")
                    ),
                )
            )
    return violations


def check_evidence_refs(
    draft: CandidateDraft, package: dict[str, Any]
) -> list[ConsistencyViolation]:
    """Citation allowlist for draft, branch suggestions and divergence (pitfall #1)."""
    allowed = package_evidence_allowlist(package)
    violations: list[ConsistencyViolation] = []

    outside = sorted(set(draft.citation_keys) - allowed)
    if outside:
        violations.append(
            ConsistencyViolation(
                CODE_EVIDENCE_OUTSIDE_PACKAGE,
                field="dimensions.evidence.items",
                evidence_keys=outside,
                detail=(
                    f"citation keys outside the sealed package allowlist: {outside}"
                ),
            )
        )
    for index, suggestion in enumerate(draft.branch_suggestions):
        s_outside = sorted(set(suggestion.evidence_refs) - allowed)
        if s_outside:
            violations.append(
                ConsistencyViolation(
                    CODE_BRANCH_EVIDENCE_OUTSIDE_PACKAGE,
                    field=f"branch_suggestions[{index}].evidence_refs",
                    evidence_keys=s_outside,
                    detail=(
                        f"branch suggestion {index} cites evidence outside the "
                        f"sealed package: {s_outside}"
                    ),
                )
            )
    if draft.divergence is not None:
        d_outside = sorted(set(draft.divergence.affected_evidence) - allowed)
        if d_outside:
            violations.append(
                ConsistencyViolation(
                    CODE_DIVERGENCE_EVIDENCE_OUTSIDE_PACKAGE,
                    field="divergence.affected_evidence",
                    evidence_keys=d_outside,
                    detail=(
                        f"divergence affects evidence outside the sealed "
                        f"package: {d_outside}"
                    ),
                )
            )
    return violations


def check_timeline_causality(package: dict[str, Any]) -> list[ConsistencyViolation]:
    """Deterministic causal-graph integrity: known events, no cycles."""
    event_keys, edges = _timeline_views(package)
    if not edges:
        return []
    violations: list[ConsistencyViolation] = []
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge["source_event_key"])
        target = str(edge["target_event_key"])
        for key, label in ((source, "source"), (target, "target")):
            if key not in event_keys:
                violations.append(
                    ConsistencyViolation(
                        CODE_TIMELINE_MISSING_EVENT,
                        field=(
                            f"dimensions.timeline.items[{edge.get('edge_key')}].{label}"
                        ),
                        detail=(
                            f"timeline edge {edge.get('edge_key')!r} references "
                            f"unknown {label} event {key!r}"
                        ),
                    )
                )
        adjacency.setdefault(source, []).append(target)

    # Cycle detection over causal edges (fail closed on any cycle).
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in adjacency.get(node, []):
            if _visit(child):
                return True
        visiting.discard(node)
        visited.add(node)
        return False

    cyclic = any(_visit(node) for node in list(adjacency))
    if cyclic:
        violations.append(
            ConsistencyViolation(
                CODE_TIMELINE_CYCLE,
                field="dimensions.timeline.items",
                detail="timeline causal edges form a cycle (impossible causal order)",
            )
        )
    return violations


def check_unresolved_clues(package: dict[str, Any]) -> list[ConsistencyViolation]:
    """Every published open clue must be evidence-grounded at the cutoff."""
    violations: list[ConsistencyViolation] = []
    for index, clue in enumerate(
        package_dimension(package, "unresolved_clues").get("items") or []
    ):
        if not isinstance(clue, dict):
            continue
        clue_id = clue.get("logical_clue_id")
        status = clue.get("status")
        if status not in OPEN_CLUE_STATES:
            violations.append(
                ConsistencyViolation(
                    CODE_CLUE_CONTRADICTION,
                    field=f"dimensions.unresolved_clues.items[{index}].status",
                    detail=(
                        f"clue {clue_id!r} is {status!r} but listed as "
                        "unresolved (must be candidate/active/reinforced)"
                    ),
                )
            )
        if not (clue.get("evidence_refs") or []):
            violations.append(
                ConsistencyViolation(
                    CODE_CLUE_WITHOUT_EVIDENCE,
                    field=f"dimensions.unresolved_clues.items[{index}].evidence_refs",
                    detail=(
                        f"unresolved clue {clue_id!r} has no evidence refs at the cutoff"
                    ),
                )
            )
    return violations


def check_continuity_claims(
    draft: CandidateDraft,
    package: dict[str, Any],
    claims: list[ContinuityClaim],
) -> list[ConsistencyViolation]:
    """Character/fact/timeline/clue claims against the frozen package (D-37-03).

    A contradiction is only tolerated when the candidate *declares* an explicit
    CanonDelta of the matching divergence type; otherwise it fails closed.
    """
    allowed = package_evidence_allowlist(package)
    cutoff = package_cutoff(package)
    entity_keys = _world_state_entity_keys(package)
    clue_ids = _unresolved_clue_ids(package)
    declared_types = {draft.divergence.divergence_type} if draft.has_divergence else set()

    violations: list[ConsistencyViolation] = []
    for claim in claims:
        prefix = f"claims.{claim.claim_key}"
        if not claim.evidence_keys:
            violations.append(
                ConsistencyViolation(
                    CODE_MISSING_EVIDENCE,
                    field=f"{prefix}.evidence_keys",
                    claim_key=claim.claim_key,
                    detail="claim has no evidence citation from the sealed package",
                )
            )
        else:
            outside = sorted(set(claim.evidence_keys) - allowed)
            if outside:
                violations.append(
                    ConsistencyViolation(
                        CODE_EVIDENCE_OUTSIDE_PACKAGE,
                        field=f"{prefix}.evidence_keys",
                        evidence_keys=outside,
                        claim_key=claim.claim_key,
                        detail=(
                            f"claim {claim.claim_key} cites evidence outside "
                            f"the sealed package: {outside}"
                        ),
                    )
                )
        if claim.chapter_number is not None and cutoff and claim.chapter_number > cutoff:
            violations.append(
                ConsistencyViolation(
                    CODE_CUTOFF_EXCEEDED,
                    field=f"{prefix}.chapter_number",
                    claim_key=claim.claim_key,
                    detail=(
                        f"claim {claim.claim_key} is beyond the frozen cutoff "
                        f"({claim.chapter_number} > {cutoff})"
                    ),
                )
            )
        if claim.category is ContinuityClaimCategory.CHARACTER_BEHAVIOR:
            if claim.entity_key not in entity_keys:
                violations.append(
                    ConsistencyViolation(
                        CODE_CHARACTER_OUTSIDE_PACKAGE,
                        field=f"{prefix}.entity_key",
                        claim_key=claim.claim_key,
                        detail=(
                            f"character behavior claim references entity "
                            f"{claim.entity_key!r} absent from the frozen "
                            "world_state"
                        ),
                    )
                )
        if claim.category is ContinuityClaimCategory.CLUE:
            if claim.clue_id not in clue_ids:
                violations.append(
                    ConsistencyViolation(
                        CODE_CLUE_OUTSIDE_PACKAGE,
                        field=f"{prefix}.clue_id",
                        claim_key=claim.claim_key,
                        detail=(
                            f"clue claim references {claim.clue_id!r} absent "
                            "from the frozen unresolved clues"
                        ),
                    )
                )

        if claim.disposition is ClaimDisposition.CONTRADICTION:
            divergence_type = CLAIM_CATEGORY_TO_DIVERGENCE_TYPE[claim.category.value]
            if divergence_type in declared_types:
                # Covered by an explicit CanonDelta (D-37-03) -> needs_override.
                continue
            code = {
                ContinuityClaimCategory.CHARACTER_BEHAVIOR: CODE_CHARACTER_CONTRADICTION,
                ContinuityClaimCategory.ESTABLISHED_FACT: CODE_FACT_CONTRADICTION,
                ContinuityClaimCategory.TIMELINE: CODE_TIMELINE_CONTRADICTION,
                ContinuityClaimCategory.CLUE: CODE_CLUE_CONTRADICTION,
            }[claim.category]
            field = prefix
            if (
                claim.category is ContinuityClaimCategory.CHARACTER_BEHAVIOR
                and claim.entity_key in entity_keys
            ):
                items = package_dimension(package, "world_state").get("items") or []
                for index, item in enumerate(items):
                    if isinstance(item, dict) and item.get("entity_key") == claim.entity_key:
                        field = f"dimensions.world_state.items[{index}].canonical_payload"
                        break
            if (
                claim.category is ContinuityClaimCategory.CLUE
                and claim.clue_id in clue_ids
            ):
                items = package_dimension(package, "unresolved_clues").get("items") or []
                for index, item in enumerate(items):
                    if isinstance(item, dict) and item.get("logical_clue_id") == claim.clue_id:
                        field = f"dimensions.unresolved_clues.items[{index}].status"
                        break
            violations.append(
                ConsistencyViolation(
                    code,
                    field=field,
                    claim_key=claim.claim_key,
                    evidence_keys=list(claim.evidence_keys),
                    detail=(
                        f"claim {claim.claim_key} ({claim.category.value}) "
                        "contradicts the frozen package without an explicit "
                        "derivative override"
                    ),
                )
            )
        elif claim.disposition is ClaimDisposition.UNKNOWN:
            violations.append(
                ConsistencyViolation(
                    CODE_UNCERTAIN,
                    severity=GateViolationSeverity.WARNING,
                    field=f"{prefix}.disposition",
                    claim_key=claim.claim_key,
                    detail=(
                        f"claim {claim.claim_key} is marked unknown; it requires "
                        "authorized evidence or an explicit override"
                    ),
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Verdict assembly
# ---------------------------------------------------------------------------


def build_branch_suggestions(draft: CandidateDraft) -> list[dict[str, Any]]:
    """Validate-and-re-emit the candidate's disabled-by-default suggestions.

    Suggestions are inert candidate output (D-37-05): this function performs no
    fork creation, no Canon write and no approval grant/reuse.
    """
    return [s.model_dump(mode="json") for s in draft.branch_suggestions]


def evaluate_consistency(
    draft: CandidateDraft,
    package: dict[str, Any],
    *,
    owner_id: int,
    novel_id: int,
    fork_id: int,
    expected_package_hash: str,
    package_intent: str,
    claims: list[ContinuityClaim] | None = None,
    budget_policy: Any | None = None,
) -> ConsistencyGateResult:
    """Deterministic consistency verdict for one candidate against its package.

    Gate order: package hash/scope -> intent -> budget -> dimension availability
    -> evidence allowlist -> timeline causality -> unresolved clues -> continuity
    claims -> explicit divergence. Any error fails closed (``blocked``); an
    explicit CanonDelta is ``needs_override``; a clean run is ``candidate``.
    """
    violations: list[ConsistencyViolation] = []
    violations.extend(
        check_package_scope(
            package,
            owner_id=owner_id,
            novel_id=novel_id,
            fork_id=fork_id,
            expected_package_hash=expected_package_hash,
        )
    )
    if not any(v.code == CODE_PACKAGE_HASH_MISMATCH for v in violations):
        # A forged package is not trustworthy for intent/dimension/claim checks.
        violations.extend(check_intent(draft, package_intent))
        violations.extend(check_budget(package, budget_policy=budget_policy))
        violations.extend(check_dimension_availability(package))
        violations.extend(check_evidence_refs(draft, package))
        violations.extend(check_timeline_causality(package))
        violations.extend(check_unresolved_clues(package))
        if claims:
            violations.extend(check_continuity_claims(draft, package, claims))

    branch_suggestions = build_branch_suggestions(draft)
    errors = [v for v in violations if v.is_error]
    if errors:
        return ConsistencyGateResult(
            GateVerdict.BLOCKED,
            violations,
            branch_suggestions,
            detail=f"{errors[0].code}: {errors[0].detail}",
        )
    if draft.has_divergence:
        return ConsistencyGateResult(
            GateVerdict.NEEDS_OVERRIDE,
            violations,
            branch_suggestions,
            detail=(
                "explicit derivative divergence requires an independent "
                "publish approval that this module never grants or reuses"
            ),
        )
    return ConsistencyGateResult(
        GateVerdict.CANDIDATE, violations, branch_suggestions, detail=None
    )


__all__ = [
    "CLAIM_CATEGORY_TO_DIVERGENCE_TYPE",
    "CODE_BRANCH_EVIDENCE_OUTSIDE_PACKAGE",
    "CODE_BUDGET_EXHAUSTED",
    "CODE_CHARACTER_CONTRADICTION",
    "CODE_CHARACTER_OUTSIDE_PACKAGE",
    "CODE_CLUE_CONTRADICTION",
    "CODE_CLUE_OUTSIDE_PACKAGE",
    "CODE_CLUE_WITHOUT_EVIDENCE",
    "CODE_CROSS_FORK",
    "CODE_CUTOFF_EXCEEDED",
    "CODE_DIVERGENCE_EVIDENCE_OUTSIDE_PACKAGE",
    "CODE_DIVERGENCE_REQUIRES_OVERRIDE",
    "CODE_EVIDENCE_OUTSIDE_PACKAGE",
    "CODE_FACT_CONTRADICTION",
    "CODE_INCOMPLETE_LINEAGE",
    "CODE_INTENT_MISMATCH",
    "CODE_MISSING_EVIDENCE",
    "CODE_PACKAGE_HASH_MISMATCH",
    "CODE_SCOPE_DENIED",
    "CODE_TIMELINE_CONTRADICTION",
    "CODE_TIMELINE_CYCLE",
    "CODE_TIMELINE_MISSING_EVENT",
    "CODE_UNCERTAIN",
    "ClaimDisposition",
    "ConsistencyGateResult",
    "ConsistencyViolation",
    "ContinuityClaim",
    "ContinuityClaimCategory",
    "GateViolationSeverity",
    "OPEN_CLUE_STATES",
    "QUALIFICATION_DIMENSIONS",
    "StrictGateModel",
    "build_branch_suggestions",
    "check_budget",
    "check_continuity_claims",
    "check_dimension_availability",
    "check_evidence_refs",
    "check_intent",
    "check_package_scope",
    "check_timeline_causality",
    "check_unresolved_clues",
    "evaluate_consistency",
    "package_cutoff",
    "package_dimension",
    "package_evidence_allowlist",
]
