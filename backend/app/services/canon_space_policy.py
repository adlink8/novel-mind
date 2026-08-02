"""Fail-closed authority and input boundaries for the three knowledge spaces."""

from dataclasses import dataclass
from typing import Any

from app.models.canon_space import (
    CANON_AUTHORITIES,
    CANON_CITATION_POLICIES,
    CANON_SPACES,
)

ORIGINAL_CANON = "original_canon"
USER_INTERPRETATION = "user_interpretation"
FANFICTION_CANON = "fanfiction_canon"

ORIGINAL_PIPELINES = frozenset(
    {"original_analysis", "original_retrieval", "facet", "evaluation", "candidate_builder"}
)

_SPACE_RULES: dict[str, tuple[str, str]] = {
    ORIGINAL_CANON: ("source_text", "original_leaf"),
    USER_INTERPRETATION: ("user_assertion", "interpretation_with_original_refs"),
    FANFICTION_CANON: ("creative_draft", "fanfiction_only"),
}


class CanonSpacePolicyError(ValueError):
    """Machine-readable rejection from a knowledge-space boundary."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class CanonSpaceRef:
    owner_id: int
    novel_id: int
    space: str
    namespace: str
    version_key: str
    authority: str
    citation_policy: str


def validate_space(space: str) -> str:
    if space not in CANON_SPACES:
        raise CanonSpacePolicyError("unknown_space", f"unsupported knowledge space: {space}")
    return space


def expected_rule(space: str) -> tuple[str, str]:
    validate_space(space)
    return _SPACE_RULES[space]


def validate_ref(ref: CanonSpaceRef) -> CanonSpaceRef:
    expected_authority, expected_citation = expected_rule(ref.space)
    if ref.owner_id < 1 or ref.novel_id < 1:
        raise CanonSpacePolicyError("invalid_scope", "owner_id and novel_id must be positive")
    if not ref.namespace or not ref.version_key:
        raise CanonSpacePolicyError("missing_lineage", "namespace and version_key are required")
    if ref.authority not in CANON_AUTHORITIES or ref.authority != expected_authority:
        raise CanonSpacePolicyError("authority_mismatch", "authority does not match the knowledge space")
    if ref.citation_policy not in CANON_CITATION_POLICIES or ref.citation_policy != expected_citation:
        raise CanonSpacePolicyError("citation_policy_mismatch", "citation policy does not match the knowledge space")
    return ref


def assert_scope(ref: Any, *, owner_id: int, novel_id: int) -> None:
    if getattr(ref, "owner_id", None) != owner_id:
        raise CanonSpacePolicyError("owner_scope", "artifact owner is outside the requested scope")
    if getattr(ref, "novel_id", None) != novel_id:
        raise CanonSpacePolicyError("novel_scope", "artifact novel is outside the requested scope")


def assert_pipeline_input(space: str, pipeline: str) -> None:
    """Reject non-original spaces from all original-fact consumers."""
    validate_space(space)
    if pipeline in ORIGINAL_PIPELINES and space != ORIGINAL_CANON:
        raise CanonSpacePolicyError(
            "space_excluded",
            f"{space} cannot enter {pipeline}; original_canon is required",
        )


def assert_citation_source(space: str, source_space: str) -> None:
    """Prevent citations from crossing into a different authority namespace."""
    validate_space(space)
    validate_space(source_space)
    if space == ORIGINAL_CANON and source_space != ORIGINAL_CANON:
        raise CanonSpacePolicyError(
            "citation_scope",
            "original canon citations must resolve to original canon evidence",
        )
    if space == FANFICTION_CANON and source_space != FANFICTION_CANON:
        raise CanonSpacePolicyError(
            "citation_scope",
            "fanfiction canon citations cannot be presented as original evidence",
        )
