"""Phase31-01 three-space authority and lineage contract tests."""

import pytest

from app.models.canon_space import CANON_SPACES
from app.services.canon_space_policy import (
    CanonSpacePolicyError,
    CanonSpaceRef,
    assert_citation_source,
    assert_pipeline_input,
    expected_rule,
    validate_ref,
    validate_space,
)

pytestmark = pytest.mark.unit


def _ref(space: str) -> CanonSpaceRef:
    authority, citation = expected_rule(space)
    return CanonSpaceRef(1, 2, space, f"ns:{space}", "v1", authority, citation)


def test_all_three_spaces_have_stable_rules():
    assert set(CANON_SPACES) == {
        "original_canon",
        "user_interpretation",
        "fanfiction_canon",
    }
    for space in CANON_SPACES:
        assert validate_ref(_ref(space)).space == space


def test_unknown_space_fails_closed():
    with pytest.raises(CanonSpacePolicyError, match="unknown_space"):
        validate_space("narrative_memory")


def test_authority_and_citation_mismatch_fail_closed():
    ref = _ref("original_canon")
    with pytest.raises(CanonSpacePolicyError, match="authority_mismatch"):
        validate_ref(
            ref.__class__(
                1,
                2,
                ref.space,
                ref.namespace,
                ref.version_key,
                "user_assertion",
                ref.citation_policy,
            )
        )
    with pytest.raises(CanonSpacePolicyError, match="citation_policy_mismatch"):
        validate_ref(
            ref.__class__(
                1,
                2,
                ref.space,
                ref.namespace,
                ref.version_key,
                ref.authority,
                "fanfiction_only",
            )
        )


@pytest.mark.parametrize("space", ["user_interpretation", "fanfiction_canon"])
def test_non_original_spaces_rejected_from_original_pipelines(space):
    with pytest.raises(CanonSpacePolicyError, match="space_excluded"):
        assert_pipeline_input(space, "original_retrieval")


def test_original_citations_cannot_cross_space():
    with pytest.raises(CanonSpacePolicyError, match="citation_scope"):
        assert_citation_source("original_canon", "fanfiction_canon")
