"""Strict continuation/rewrite candidate contract and deterministic gates (Phase 37-02).

D-37-02 / REQ-CRE-06 / REQ-FORK-03: the LLM/agent can only ever produce a
strict-schema candidate; deterministic server code owns evidence, scope,
schema, budget and publication authority. This module is the DB-free contract
layer (the ``reader_chat`` answer-envelope analog):

- ``CandidateDraft`` is the strict, closed-vocabulary provider output:
  ``derivative-candidate.v1`` with intent, draft text, cited evidence keys, an
  explicit optional ``CanonDelta`` (D-37-03) and disabled-by-default
  ``BranchSuggestion[]`` (D-37-05). Unknown fields fail closed.
- ``parse_candidate`` validates a provider payload strictly; a schema violation
  is never silently repaired or published (RESEARCH pitfall #1).
- ``validate_candidate_against_package`` replays the package hash and enforces
  the evidence allowlist: a citation key outside the package fails closed
  (T-37-02-01 / pitfall #1).
- ``apply_deterministic_gates`` returns the stable verdict
  ``candidate | blocked | needs_override`` with a stable reason code.

Stable reason codes (failure policy, 37-VALIDATION):
  ``schema_invalid``, ``package_hash_mismatch``, ``evidence_outside_package``,
  ``divergence_requires_override``, ``empty_draft``, ``intent_mismatch``.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

from app.models.derivative_context import DERIVATIVE_CONTEXT_INTENTS
from app.services.derivative_generation.context_package import (
    verify_package_hash,
)

# Closed divergence classes (RESEARCH: no implicit plot/character/world/timeline
# class is accepted; divergence is always an explicit CanonDelta).
DIVERGENCE_TYPES = ("character", "timeline", "world_rule", "clue", "other")
CANDIDATE_SCHEMA_VERSION = "derivative-candidate.v1"
CANDIDATE_SCHEMA_HASH_PREFIX = "derivative-candidate.v1:schema"
CANDIDATE_HASH_PREFIX = "derivative-candidate.v1:candidate"

MAX_DRAFT_CHARS = 40_000
MAX_SUMMARY_CHARS = 1_000
MAX_CITATION_KEYS = 128
MAX_BRANCH_SUGGESTIONS = 16

# Canonical hash helpers reused by the job/attempt lineage.
HEX64 = r"^[0-9a-f]{64}$"

# Pydantic needs a stable pattern object.
_Hex64 = Annotated[str, StringConstraints(pattern=HEX64)]


class CandidateIntent(StrEnum):
    CONTINUATION = "continuation"
    REWRITE = "rewrite"


class GateVerdict(StrEnum):
    CANDIDATE = "candidate"
    BLOCKED = "blocked"
    NEEDS_OVERRIDE = "needs_override"


class StrictCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonDelta(StrictCandidateModel):
    """Explicit derivative override (D-37-03): never an implicit divergence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    divergence_type: str = Field(
        min_length=1, max_length=32, pattern="|".join(DIVERGENCE_TYPES)
    )
    reason: str = Field(min_length=1, max_length=2000)
    affected_evidence: list[str] = Field(default_factory=list, max_length=128)
    scope: str = Field(default="derivative", pattern="^derivative$")


class BranchSuggestion(StrictCandidateModel):
    """Disabled-by-default candidate branch output (D-37-05, REQ-FORK-06).

    Describes a user-selectable branch option only; it never auto-forks, never
    changes Canon/branch state and cannot reuse ``allow_divergence`` approval.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    choice_text: str = Field(min_length=1, max_length=2000)
    branch_summary: str = Field(min_length=1, max_length=2000)
    triggering_conflict: str = Field(min_length=1, max_length=2000)
    canon_delta_hash: _Hex64
    evidence_refs: list[str] = Field(default_factory=list, max_length=128)
    # D-37-05: suggestions are disabled by default; an enabled default fails.
    enabled_by_default: bool = False

    @model_validator(mode="after")
    def must_be_disabled_by_default(self) -> "BranchSuggestion":
        if self.enabled_by_default is not False:
            raise ValueError(
                "BranchSuggestion must be disabled by default "
                "(enabled_by_default=False); an enabled default is forbidden"
            )
        return self


class CandidateDraft(StrictCandidateModel):
    """Strict provider output; unknown fields and empty drafts fail closed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=CANDIDATE_SCHEMA_VERSION)
    intent: CandidateIntent
    draft_text: str = Field(min_length=1, max_length=MAX_DRAFT_CHARS)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY_CHARS)
    # Evidence keys cited by the model; must be a subset of the package allowlist.
    citation_keys: list[str] = Field(default_factory=list, max_length=MAX_CITATION_KEYS)
    # D-37-03: an explicit derivative override, stored never promoted.
    divergence: CanonDelta | None = None
    # D-37-05: disabled-by-default candidate branch suggestions.
    branch_suggestions: list[BranchSuggestion] = Field(
        default_factory=list, max_length=MAX_BRANCH_SUGGESTIONS
    )

    @property
    def has_divergence(self) -> bool:
        return self.divergence is not None


class CandidateGateResult:
    """Deterministic gate outcome with a stable reason code."""

    __slots__ = ("verdict", "reason", "detail")

    def __init__(
        self, verdict: GateVerdict, reason: str | None = None, detail: str | None = None
    ):
        self.verdict = verdict
        self.reason = reason
        self.detail = detail


def canonical_candidate_hash(payload: Any) -> str:
    """Byte-replayable canonical SHA-256 (same convention as context_package)."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(f"{CANDIDATE_HASH_PREFIX}\n".encode("utf-8") + encoded).hexdigest()


def candidate_hash(draft: CandidateDraft) -> str:
    """Canonical candidate hash over the strict payload (response lineage)."""
    return canonical_candidate_hash(draft.model_dump(mode="json"))


def schema_hash() -> str:
    """Deterministic hash of the candidate JSON schema (job schema lineage)."""
    encoded = json.dumps(
        CandidateDraft.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(
        f"{CANDIDATE_SCHEMA_HASH_PREFIX}\n".encode("utf-8") + encoded
    ).hexdigest()


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
        text = text.removesuffix("```").strip()
    return text


def parse_candidate(content: str) -> CandidateDraft:
    """Strictly parse provider output into a CandidateDraft.

    Raises ``ValueError`` (stable code ``schema_invalid``) on any schema
    violation; the caller records the failed attempt and never publishes.
    """
    from pydantic import ValidationError

    raw = _strip_code_fence(content or "")
    if not raw:
        raise ValueError("schema_invalid: empty provider content")
    try:
        return CandidateDraft.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        raise ValueError(f"schema_invalid: {exc.errors()[:3]}") from exc


def _package_evidence_allowlist(package: dict[str, Any]) -> set[str]:
    """Allowlisted citation keys from the sealed package evidence dimension."""
    dimension = (package.get("dimensions") or {}).get("evidence") or {}
    items = dimension.get("items") or []
    allowed: set[str] = set()
    for ref in items:
        if isinstance(ref, dict) and ref.get("candidate_key"):
            allowed.add(str(ref["candidate_key"]))
    return allowed


def validate_candidate_against_package(
    draft: CandidateDraft,
    package: dict[str, Any],
    *,
    expected_package_hash: str,
) -> None:
    """Replay the package hash and enforce the evidence allowlist (T-37-02-01).

    Raises ``ValueError`` with the stable reason code on any violation; a
    citation outside the package fails closed — refs are never silently dropped.
    """
    verify_package_hash(dict(package or {}), expected_package_hash)
    allowed = _package_evidence_allowlist(package)
    outside = sorted(set(draft.citation_keys) - allowed)
    if outside:
        raise ValueError(
            f"evidence_outside_package: {outside} not in the sealed package"
        )


def apply_deterministic_gates(
    draft: CandidateDraft,
    package: dict[str, Any],
    *,
    expected_package_hash: str,
    package_intent: str,
) -> CandidateGateResult:
    """Stable verdict for one strict candidate against its sealed package.

    Order: package hash replay -> intent match -> empty draft -> evidence
    allowlist -> explicit divergence (D-37-03). The first failing gate is the
    stable reason code; a clean candidate yields ``candidate``.
    """
    if package_intent not in DERIVATIVE_CONTEXT_INTENTS:
        return CandidateGateResult(
            GateVerdict.BLOCKED,
            "intent_mismatch",
            f"unsupported intent {package_intent!r}",
        )
    if draft.intent.value != package_intent:
        return CandidateGateResult(
            GateVerdict.BLOCKED,
            "intent_mismatch",
            f"candidate intent {draft.intent.value} != package intent {package_intent}",
        )
    try:
        validate_candidate_against_package(
            draft, package, expected_package_hash=expected_package_hash
        )
    except ValueError as exc:
        reason = str(exc).split(":", 1)[0]
        return CandidateGateResult(GateVerdict.BLOCKED, reason, str(exc))
    if draft.has_divergence:
        return CandidateGateResult(
            GateVerdict.NEEDS_OVERRIDE,
            "divergence_requires_override",
            "explicit derivative divergence requires an override approval",
        )
    return CandidateGateResult(GateVerdict.CANDIDATE)


__all__ = [
    "BranchSuggestion",
    "CANDIDATE_HASH_PREFIX",
    "CANDIDATE_SCHEMA_HASH_PREFIX",
    "CANDIDATE_SCHEMA_VERSION",
    "CanonDelta",
    "CandidateDraft",
    "CandidateGateResult",
    "CandidateIntent",
    "DIVERGENCE_TYPES",
    "GateVerdict",
    "apply_deterministic_gates",
    "candidate_hash",
    "canonical_candidate_hash",
    "parse_candidate",
    "schema_hash",
    "validate_candidate_against_package",
]
