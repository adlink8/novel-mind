"""Adversarial isolation gates for the three knowledge spaces (Phase 35-01).

REQ-FORK-01: the same owner/novel can never cross space, namespace or version
silently. D-35-02: the Original Canon space is read-only by default — the
contract layer must expose no Original write path, and any attempt to mutate or
cite across authority namespaces fails closed.

These gates are deterministic (contract + AST source checks), so they run
without PostgreSQL.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.canon_fork.contracts import (
    CanonCitation,
    CanonForkContractError,
    CanonScope,
    CanonSpace,
    CanonWriteIntent,
    assert_original_readonly,
    build_scope,
    content_sha256,
)
from app.services.canon_space_policy import (
    CanonSpacePolicyError,
    CanonSpaceRef,
    assert_scope,
    expected_rule,
    validate_ref,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX64 = "a" * 64
HEX64_B = "b" * 64

CANON_FORK_DIR = Path(__file__).resolve().parents[2] / "app" / "services" / "canon_fork"
CONTRACTS_SOURCE = (CANON_FORK_DIR / "contracts.py").read_text(encoding="utf-8")


def _scope(
    space: str = "user_interpretation",
    *,
    owner_id: int = 1,
    novel_id: int = 2,
    namespace: str = "user:1",
    version_key: str = "v1",
) -> CanonScope:
    return build_scope(
        owner_id=owner_id,
        novel_id=novel_id,
        space=space,
        namespace=namespace,
        version_key=version_key,
        source_snapshot_hash=HEX64,
        through_chapter=3,
        cutoff_snapshot_hash=HEX64_B,
    )


# ---------------------------------------------------------------------------
# Original Canon is read-only: no write path exists (D-35-02)
# ---------------------------------------------------------------------------


def test_original_canon_has_no_write_path():
    """The contract layer must not accept any write for the Original space."""
    original = _scope("original_canon")
    with pytest.raises(ValidationError, match="original_readonly"):
        CanonWriteIntent(
            scope=original,
            content="adversarial original mutation",
            content_hash=content_sha256("adversarial original mutation"),
        )
    with pytest.raises(CanonForkContractError, match="original_readonly"):
        assert_original_readonly(CanonSpace.ORIGINAL_CANON, mutation=True)


def test_contracts_source_has_no_database_write():
    """Pure contract layer: no ORM/session/DML imports or calls."""
    tree = ast.parse(CONTRACTS_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("app.models"), (
                f"contracts.py must not import ORM models: {node.module}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.models"), (
                    f"contracts.py must not import ORM models: {alias.name}"
                )
    for token in (
        "session.add",
        "session.commit",
        "insert(",
        ".update(",
        ".delete(",
        "create_engine",
    ):
        assert token not in CONTRACTS_SOURCE, f"contracts.py must not write DB: {token}"


def test_original_citation_cannot_cross_into_derivative_spaces():
    original = _scope("original_canon")
    for cited in (CanonSpace.USER_INTERPRETATION, CanonSpace.FANFICTION_CANON):
        with pytest.raises(ValidationError, match="citation_scope"):
            CanonCitation(
                scope=original,
                cited_space=cited,
                cited_namespace=f"{cited.value}:1",
                leaf_key="leaf",
                content_hash=HEX64,
                source_snapshot_hash=HEX64,
            )


# ---------------------------------------------------------------------------
# Scope can never silently cross space / namespace / version (REQ-FORK-01)
# ---------------------------------------------------------------------------


def test_scope_hash_changes_when_space_changes_for_same_owner_novel():
    ui = _scope("user_interpretation", version_key="v1")
    ff = _scope("fanfiction_canon", version_key="v1", namespace="ff:1")
    assert ui.owner_id == ff.owner_id
    assert ui.novel_id == ff.novel_id
    assert ui.scope_hash() != ff.scope_hash()


def test_scope_hash_changes_when_version_changes_for_same_space():
    v1 = _scope("user_interpretation", version_key="v1")
    v2 = _scope("user_interpretation", version_key="v2")
    assert v1.scope_hash() != v2.scope_hash()


def test_scope_hash_changes_when_namespace_changes():
    ns1 = _scope(namespace="user:1")
    ns2 = _scope(namespace="user:2")
    assert ns1.scope_hash() != ns2.scope_hash()


def test_owner_and_novel_are_bound_into_the_scope():
    owner_7 = _scope(owner_id=7)
    novel_9 = _scope(novel_id=9)
    base = _scope()
    assert base.scope_hash() != owner_7.scope_hash()
    assert base.scope_hash() != novel_9.scope_hash()


def test_existing_policy_scope_gate_still_enforced():
    authority, citation = expected_rule("user_interpretation")
    ref = CanonSpaceRef(
        7, 9, "user_interpretation", "user:7", "v2", authority, citation
    )
    validate_ref(ref)
    with pytest.raises(CanonSpacePolicyError):
        assert_scope(ref, owner_id=8, novel_id=9)
    with pytest.raises(CanonSpacePolicyError):
        assert_scope(ref, owner_id=7, novel_id=10)


def test_blank_or_oversized_namespace_fails_closed():
    with pytest.raises((ValidationError, ValueError)):
        _scope(namespace="   ")
    with pytest.raises((ValidationError, ValueError)):
        _scope(namespace="x" * 200)


# ---------------------------------------------------------------------------
# Shared derivative-write guard adapter (Phase 35-04, REQ-CRE-02)
# ---------------------------------------------------------------------------


def test_guard_rejects_derivative_spaces_from_every_original_pipeline():
    """The shared guard rejects both derivative spaces for index/eval/facet."""
    from app.services.canon_fork.contamination import (
        DerivativeWriteGuard,
    )

    for pipeline in ("original_retrieval", "evaluation", "facet"):
        guard = DerivativeWriteGuard(pipeline)
        for space in ("user_interpretation", "fanfiction_canon"):
            with pytest.raises(Exception) as excinfo:
                guard.assert_write_allowed(space=space, owner_id=1, novel_id=2)
            # Machine-readable blocked reason is preserved on the error.
            assert getattr(excinfo.value, "blocked_reason", None) == "space_excluded"
            assert getattr(excinfo.value, "pipeline", None) == pipeline


def test_guard_allows_original_canon_for_every_original_pipeline():
    from app.services.canon_fork.contamination import (
        DerivativeWriteGuard,
    )

    for pipeline in ("original_retrieval", "evaluation", "facet"):
        guard = DerivativeWriteGuard(pipeline)
        guard.assert_write_allowed(space="original_canon", owner_id=1, novel_id=2)


def test_guard_binds_owner_and_novel_from_the_frozen_scope():
    from app.services.canon_fork.contamination import DerivativeWriteGuard

    guard = DerivativeWriteGuard("evaluation")
    scope = _scope("original_canon", owner_id=7, novel_id=9)
    guard.assert_write_allowed(scope=scope, owner_id=7, novel_id=9)
    with pytest.raises(Exception) as excinfo:
        guard.assert_write_allowed(scope=scope, owner_id=8, novel_id=9)
    assert getattr(excinfo.value, "blocked_reason", None) == "owner_scope"
    with pytest.raises(Exception) as excinfo:
        guard.assert_write_allowed(scope=scope, owner_id=7, novel_id=10)
    assert getattr(excinfo.value, "blocked_reason", None) == "novel_scope"


def test_guard_never_returns_fake_success_on_derivative_input():
    """A derivative write must raise; an empty-success path is forbidden."""
    from app.services.canon_fork.contamination import DerivativeWriteGuard

    guard = DerivativeWriteGuard("facet")
    try:
        guard.assert_write_allowed(space="user_interpretation")
    except Exception as exc:
        assert getattr(exc, "blocked_reason", None) == "space_excluded"
    else:
        pytest.fail("derivative space must never pass the facet guard")


# ---------------------------------------------------------------------------
# Contamination phase gate verdicts (Phase 35-04, only candidate/blocked)
# ---------------------------------------------------------------------------


def test_phase_gate_verdict_vocabulary_is_closed():
    from app.services.canon_fork.contamination import (
        PhaseGateEvidence,
        PhaseGateVerdict,
        resolve_gate_verdict,
    )

    assert {v.value for v in PhaseGateVerdict} == {"candidate", "blocked"}
    green = resolve_gate_verdict(PhaseGateEvidence(preflight_ok=True))
    assert green.verdict is PhaseGateVerdict.CANDIDATE


def test_phase_gate_blocks_without_preflight():
    """Without an executed upstream contract-availability preflight -> blocked."""
    from app.services.canon_fork.contamination import (
        ContaminationBlockedReason,
        PhaseGateVerdict,
        resolve_gate_verdict,
    )
    from app.services.canon_fork.contamination import PhaseGateEvidence

    result = resolve_gate_verdict(PhaseGateEvidence())
    assert result.verdict is PhaseGateVerdict.BLOCKED
    assert result.blocked_reason is ContaminationBlockedReason.MISSING_PREFLIGHT


def test_phase_gate_blocks_on_each_fail_closed_condition():
    from app.services.canon_fork.contamination import (
        ContaminationBlockedReason,
        PhaseGateEvidence,
        PhaseGateVerdict,
        resolve_gate_verdict,
    )

    cases = (
        (
            PhaseGateEvidence(preflight_ok=True, active_pointer=True),
            ContaminationBlockedReason.ACTIVE_POINTER,
        ),
        (
            PhaseGateEvidence(preflight_ok=True, original_mutated=True),
            ContaminationBlockedReason.ORIGINAL_MUTATION,
        ),
        (
            PhaseGateEvidence(preflight_ok=True, cross_owner_leakage=True),
            ContaminationBlockedReason.CROSS_OWNER_LEAKAGE,
        ),
        (
            PhaseGateEvidence(
                preflight_ok=True, publish_requested=True, approved=False
            ),
            ContaminationBlockedReason.APPROVAL_REQUIRED,
        ),
    )
    for evidence, reason in cases:
        result = resolve_gate_verdict(evidence)
        assert result.verdict is PhaseGateVerdict.BLOCKED
        assert result.blocked_reason is reason


def test_phase_gate_candidate_requires_preflight_approval_and_clean_state():
    from app.services.canon_fork.contamination import (
        PhaseGateEvidence,
        PhaseGateVerdict,
        resolve_gate_verdict,
    )

    result = resolve_gate_verdict(
        PhaseGateEvidence(
            preflight_ok=True,
            active_pointer=False,
            original_mutated=False,
            cross_owner_leakage=False,
            publish_requested=True,
            approved=True,
        )
    )
    assert result.verdict is PhaseGateVerdict.CANDIDATE
    assert result.blocked_reason is None
