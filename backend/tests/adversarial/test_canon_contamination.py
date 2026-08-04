"""Adversarial contamination gates for the three knowledge spaces (Phase 35-01).

REQ-CRE-02: derivative content must never enter the Original retrieval index,
evaluation corpus or facet production chain. These negative gates prove, on
every reachable write/query path defined in this phase, that a derivative space
fails closed *before* any IO — and that the contract layer exposes no Original
write or Original-into-derivative citation bridge.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.canon_fork.contracts import (
    ORIGINAL_PIPELINES,
    CanonCitation,
    CanonForkContractError,
    CanonSpace,
    CanonWriteIntent,
    assert_original_pipeline_input,
    build_scope,
    content_sha256,
)
from app.services.canon_space_policy import (
    CanonSpacePolicyError,
    assert_pipeline_input,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX64 = "a" * 64
HEX64_B = "b" * 64

CANON_FORK_DIR = Path(__file__).resolve().parents[2] / "app" / "services" / "canon_fork"
CONTRACTS_SOURCE = (CANON_FORK_DIR / "contracts.py").read_text(encoding="utf-8")

DERIVATIVE_SPACES = (CanonSpace.USER_INTERPRETATION, CanonSpace.FANFICTION_CANON)
# Every production chain that must stay Original-only (REQ-CRE-02).
CONTAMINATION_PIPELINES = (
    "original_analysis",
    "original_retrieval",
    "facet",
    "evaluation",
    "candidate_builder",
)


def _scope(space: str) -> CanonSpace:
    return build_scope(
        owner_id=1,
        novel_id=2,
        space=space,
        namespace=f"{space}:1",
        version_key="v1",
        source_snapshot_hash=HEX64,
        through_chapter=3,
        cutoff_snapshot_hash=HEX64_B,
    )


# ---------------------------------------------------------------------------
# Derivative text cannot enter any Original production pipeline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("space", [s.value for s in DERIVATIVE_SPACES])
@pytest.mark.parametrize("pipeline", CONTAMINATION_PIPELINES)
def test_derivative_space_rejected_from_original_pipeline(space, pipeline):
    with pytest.raises(CanonForkContractError, match="space_excluded"):
        assert_original_pipeline_input(CanonSpace(space), pipeline)
    # The legacy policy gate agrees: same fail-closed verdict.
    with pytest.raises(CanonSpacePolicyError, match="space_excluded"):
        assert_pipeline_input(space, pipeline)


def test_original_canon_is_the_only_pipeline_accepted_input():
    for pipeline in CONTAMINATION_PIPELINES:
        assert_original_pipeline_input(CanonSpace.ORIGINAL_CANON, pipeline)
        assert_pipeline_input("original_canon", pipeline)


@pytest.mark.asyncio
async def test_original_retrieval_entry_points_cannot_receive_a_space_arg():
    """No original retrieval entry point accepts an unscoped ``space``.

    Phase 35-01 owns the deterministic contract boundary: retrieval consumers
    must first build an immutable ``CanonScope`` through the contract layer,
    which rejects any derivative space. Calling the raw entry points with a
    derivative ``space`` argument is not even possible — the signature has no
    such parameter, so a derivative space can never be smuggled into the
    original retrieval IO path.
    """
    import inspect as _inspect

    from app.services.knowledge_units.search import NarrativeSearchService
    from app.services.reader_chat.retrieval import retrieve_visible_evidence

    search_sig = _inspect.signature(NarrativeSearchService.search_units)
    assert "space" not in search_sig.parameters
    evidence_sig = _inspect.signature(retrieve_visible_evidence)
    assert "space" not in evidence_sig.parameters
    # The contract gate is the only admission control and it rejects derivative
    # spaces for every Original pipeline.
    for pipeline in CONTAMINATION_PIPELINES:
        with pytest.raises(CanonForkContractError, match="space_excluded"):
            assert_original_pipeline_input(CanonSpace.FANFICTION_CANON, pipeline)


# ---------------------------------------------------------------------------
# Derivative evidence can never masquerade as an Original citation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cited", ["user_interpretation", "fanfiction_canon"])
def test_original_citation_rejects_derivative_leaf(cited):
    original = _scope("original_canon")
    with pytest.raises(ValidationError, match="citation_scope"):
        CanonCitation(
            scope=original,
            cited_space=CanonSpace(cited),
            cited_namespace=f"{cited}:1",
            leaf_key="derivative_leaf",
            content_hash=HEX64,
            source_snapshot_hash=HEX64,
        )


def test_original_write_cannot_carry_derivative_text():
    original = _scope("original_canon")
    with pytest.raises(ValidationError, match="original_readonly"):
        CanonWriteIntent(
            scope=original,
            content="derivative text smuggled into original canon",
            content_hash=content_sha256(
                "derivative text smuggled into original canon"
            ),
        )


def test_contracts_module_is_a_pure_boundary():
    """The contract layer is pure: no app imports, no vector index, no DML."""
    tree = ast.parse(CONTRACTS_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            for alias in node.names:
                assert not (alias.name or "").startswith("app."), (
                    f"contracts.py must not import app modules: {alias.name}"
                )
    for token in (
        "chroma",
        "VectorStore",
        "insert(",
        "session.add",
        "session.commit",
        "create_engine",
        "eval_dataset",
    ):
        assert token not in CONTRACTS_SOURCE, (
            f"contracts.py must not write original index/eval/facet: {token}"
        )


def test_original_pipelines_are_a_closed_frozen_set():
    assert ORIGINAL_PIPELINES == frozenset(CONTAMINATION_PIPELINES)
    # The pipeline vocabulary must be declared, never computed from runtime input.
    assert "ORIGINAL_PIPELINES = frozenset(" in CONTRACTS_SOURCE


def test_derivative_space_never_accepted_by_pipeline_guard_at_source_level():
    # A derivative space literal must never appear next to a pipeline gate in a
    # permissive branch (no `space in ORIGINAL_PIPELINES` bypass).
    assert "not in ORIGINAL_PIPELINES" not in CONTRACTS_SOURCE
