"""Phase31-02 negative boundary tests."""

import pytest

from app.services.canon_space_policy import (
    CanonSpacePolicyError,
    CanonSpaceRef,
    assert_citation_source,
    assert_pipeline_input,
    assert_scope,
    expected_rule,
    validate_ref,
)
from app.services.knowledge_units.search import NarrativeSearchService
from app.services.reader_chat.retrieval import retrieve_visible_evidence

pytestmark = pytest.mark.unit


def test_fanfiction_is_not_an_original_candidate_builder_input():
    with pytest.raises(CanonSpacePolicyError, match="space_excluded"):
        assert_pipeline_input("fanfiction_canon", "candidate_builder")


def test_scope_is_owner_and_novel_bound():
    authority, citation = expected_rule("user_interpretation")
    ref = CanonSpaceRef(
        7, 9, "user_interpretation", "user:7", "v2", authority, citation
    )
    validate_ref(ref)
    with pytest.raises(CanonSpacePolicyError, match="owner_scope"):
        assert_scope(ref, owner_id=8, novel_id=9)
    with pytest.raises(CanonSpacePolicyError, match="novel_scope"):
        assert_scope(ref, owner_id=7, novel_id=10)


def test_fanfiction_citations_stay_in_fanfiction_space():
    with pytest.raises(CanonSpacePolicyError, match="citation_scope"):
        assert_citation_source("fanfiction_canon", "original_canon")


@pytest.mark.asyncio
async def test_retrieval_entry_points_reject_non_original_space_before_io():
    # Phase 35-01 contract: original retrieval entry points carry NO `space`
    # parameter — a derivative space can never be smuggled into the raw IO
    # path. Space is bound exclusively through the CanonScope contract layer.
    import inspect as _inspect

    search_sig = _inspect.signature(NarrativeSearchService.search_units)
    assert "space" not in search_sig.parameters
    evidence_sig = _inspect.signature(retrieve_visible_evidence)
    assert "space" not in evidence_sig.parameters

    # The deterministic policy gate rejects every derivative space before any
    # original-fact consumer runs.
    with pytest.raises(CanonSpacePolicyError, match="space_excluded"):
        assert_pipeline_input("fanfiction_canon", "original_retrieval")
    with pytest.raises(CanonSpacePolicyError, match="space_excluded"):
        assert_pipeline_input("user_interpretation", "original_retrieval")
