"""Phase 35-01 triple knowledge-space contract tests (REQ-FORK-01/CRE-01).

Covers D-35-01..D-35-03 within the contract layer:
- three-space closed enum vocabulary with one authority and one citation policy
  per space;
- an immutable scope must contain owner, novel, space, namespace, version,
  source snapshot and cutoff — any missing piece fails closed;
- citations never cross into a different authority namespace;
- the Original Canon space is read-only: no write intent can exist for it;
- content hash replays from content (immutable lineage) and scope hashes are
  byte-replayable (checksum-preserving).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.canon_fork.contracts import (
    CANON_ARTIFACT_STATUSES,
    CITATION_SOURCE_RULES,
    ORIGINAL_PIPELINES,
    SPACE_RULES,
    CanonArtifactStatus,
    CanonAuthority,
    CanonCitation,
    CanonCitationPolicy,
    CanonForkContractError,
    CanonScope,
    CanonSpace,
    CanonWriteIntent,
    assert_citation_authority,
    assert_original_pipeline_input,
    assert_original_readonly,
    build_scope,
    canonical_scope_hash,
    content_sha256,
    expected_authority,
    expected_citation_policy,
    validate_scope,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64

CANON_SPACE_VALUES = {
    CanonSpace.ORIGINAL_CANON,
    CanonSpace.USER_INTERPRETATION,
    CanonSpace.FANFICTION_CANON,
}


def _scope(
    space: str = "user_interpretation",
    *,
    version_key: str = "v1",
    namespace: str = "user:1",
    owner_id: int = 1,
    novel_id: int = 2,
    through_chapter: int = 3,
    source_snapshot_hash: str = HEX64,
    cutoff_snapshot_hash: str = HEX64_B,
) -> CanonScope:
    return build_scope(
        owner_id=owner_id,
        novel_id=novel_id,
        space=space,
        namespace=namespace,
        version_key=version_key,
        source_snapshot_hash=source_snapshot_hash,
        through_chapter=through_chapter,
        cutoff_snapshot_hash=cutoff_snapshot_hash,
    )


# ---------------------------------------------------------------------------
# Three-space vocabulary and stable per-space rules (D-35-01)
# ---------------------------------------------------------------------------


def test_three_spaces_have_complete_closed_enum():
    assert {s.value for s in CanonSpace} == {
        "original_canon",
        "user_interpretation",
        "fanfiction_canon",
    }
    assert {s.value for s in CanonAuthority} == {
        "source_text",
        "user_assertion",
        "creative_draft",
    }
    assert {s.value for s in CanonCitationPolicy} == {
        "original_leaf",
        "interpretation_with_original_refs",
        "fanfiction_only",
    }
    assert set(SPACE_RULES) == CANON_SPACE_VALUES
    assert CanonArtifactStatus.DRAFT.value == "draft"
    assert set(CANON_ARTIFACT_STATUSES) == {s.value for s in CanonArtifactStatus}


def test_each_space_has_exactly_one_authority_and_citation_policy():
    assert SPACE_RULES[CanonSpace.ORIGINAL_CANON] == (
        CanonAuthority.SOURCE_TEXT,
        CanonCitationPolicy.ORIGINAL_LEAF,
    )
    assert SPACE_RULES[CanonSpace.USER_INTERPRETATION] == (
        CanonAuthority.USER_ASSERTION,
        CanonCitationPolicy.INTERPRETATION_WITH_ORIGINAL_REFS,
    )
    assert SPACE_RULES[CanonSpace.FANFICTION_CANON] == (
        CanonAuthority.CREATIVE_DRAFT,
        CanonCitationPolicy.FANFICTION_ONLY,
    )
    for space in CANON_SPACE_VALUES:
        assert expected_authority(space) == SPACE_RULES[space][0]
        assert expected_citation_policy(space) == SPACE_RULES[space][1]


def test_unknown_space_fails_closed():
    with pytest.raises(ValueError):
        build_scope(
            owner_id=1,
            novel_id=2,
            space="narrative_memory",
            namespace="n",
            version_key="v1",
            source_snapshot_hash=HEX64,
            through_chapter=1,
            cutoff_snapshot_hash=HEX64_B,
        )


# ---------------------------------------------------------------------------
# Immutable scope: owner/novel/space/namespace/version/cutoff all required
# ---------------------------------------------------------------------------


def test_scope_without_cutoff_fails_closed():
    with pytest.raises(ValidationError):
        CanonScope(
            owner_id=1,
            novel_id=2,
            space=CanonSpace.USER_INTERPRETATION,
            namespace="user:1",
            version_key="v1",
            authority=CanonAuthority.USER_ASSERTION,
            citation_policy=CanonCitationPolicy.INTERPRETATION_WITH_ORIGINAL_REFS,
            source_snapshot_hash=HEX64,
        )


def test_scope_without_owner_or_novel_fails_closed():
    base = {
        "space": CanonSpace.USER_INTERPRETATION,
        "namespace": "user:1",
        "version_key": "v1",
        "authority": CanonAuthority.USER_ASSERTION,
        "citation_policy": CanonCitationPolicy.INTERPRETATION_WITH_ORIGINAL_REFS,
        "source_snapshot_hash": HEX64,
    }
    with pytest.raises(ValidationError):
        CanonScope(owner_id=0, novel_id=2, **base)
    with pytest.raises(ValidationError):
        CanonScope(owner_id=1, novel_id=0, **base)


def test_scope_without_namespace_or_version_fails_closed():
    scope_kwargs = dict(
        owner_id=1,
        novel_id=2,
        space=CanonSpace.USER_INTERPRETATION,
        authority=CanonAuthority.USER_ASSERTION,
        citation_policy=CanonCitationPolicy.INTERPRETATION_WITH_ORIGINAL_REFS,
        source_snapshot_hash=HEX64,
    )
    with pytest.raises(ValidationError):
        CanonScope(namespace="  ", version_key="v1", **scope_kwargs)
    with pytest.raises(ValidationError):
        CanonScope(namespace="user:1", version_key="  ", **scope_kwargs)


def test_scope_authority_mismatch_fails_closed():
    scope = _scope("original_canon")
    assert scope.authority is CanonAuthority.SOURCE_TEXT
    with pytest.raises(ValidationError, match="authority_mismatch"):
        CanonScope(
            **{
                **scope.model_dump(),
                "authority": CanonAuthority.USER_ASSERTION,
            }
        )


def test_scope_citation_policy_mismatch_fails_closed():
    scope = _scope("fanfiction_canon")
    with pytest.raises(ValidationError, match="citation_policy_mismatch"):
        CanonScope(
            **{
                **scope.model_dump(),
                "citation_policy": CanonCitationPolicy.ORIGINAL_LEAF,
            }
        )


def test_validate_scope_accepts_complete_scope():
    scope = _scope()
    assert validate_scope(scope) is scope


def test_zero_cutoff_is_rejected_at_dto_boundary():
    scope = _scope()
    with pytest.raises(ValidationError, match="greater_than"):
        type(scope.cutoff)(
            through_chapter=0,
            full_book_authorized=False,
            snapshot_hash=HEX64_B,
        )


# ---------------------------------------------------------------------------
# Citation authority: no cross-space resolution (D-35-01)
# ---------------------------------------------------------------------------


def test_original_citations_cannot_resolve_to_derivative_spaces():
    scope = _scope("original_canon")
    with pytest.raises(ValidationError, match="citation_scope"):
        CanonCitation(
            scope=scope,
            cited_space=CanonSpace.FANFICTION_CANON,
            cited_namespace="ff:1",
            leaf_key="leaf",
            content_hash=HEX64,
            source_snapshot_hash=HEX64,
        )
    with pytest.raises(ValidationError, match="citation_scope"):
        CanonCitation(
            scope=scope,
            cited_space=CanonSpace.USER_INTERPRETATION,
            cited_namespace="user:1",
            leaf_key="leaf",
            content_hash=HEX64,
            source_snapshot_hash=HEX64,
        )


def test_fanfiction_citations_stay_in_fanfiction_space():
    scope = _scope("fanfiction_canon")
    ok = CanonCitation(
        scope=scope,
        cited_space=CanonSpace.FANFICTION_CANON,
        cited_namespace="ff:1",
        leaf_key="leaf",
        content_hash=HEX64,
        source_snapshot_hash=HEX64,
    )
    assert ok.cited_space is CanonSpace.FANFICTION_CANON
    with pytest.raises(ValidationError, match="citation_scope"):
        CanonCitation(
            scope=scope,
            cited_space=CanonSpace.ORIGINAL_CANON,
            cited_namespace="orig:1",
            leaf_key="leaf",
            content_hash=HEX64,
            source_snapshot_hash=HEX64,
        )


def test_user_interpretation_cites_original_refs_but_not_fanfiction():
    scope = _scope("user_interpretation")
    ok = CanonCitation(
        scope=scope,
        cited_space=CanonSpace.ORIGINAL_CANON,
        cited_namespace="orig:1",
        leaf_key="leaf",
        content_hash=HEX64,
        source_snapshot_hash=HEX64,
    )
    assert ok.cited_space is CanonSpace.ORIGINAL_CANON
    with pytest.raises(ValidationError, match="citation_scope"):
        CanonCitation(
            scope=scope,
            cited_space=CanonSpace.FANFICTION_CANON,
            cited_namespace="ff:1",
            leaf_key="leaf",
            content_hash=HEX64,
            source_snapshot_hash=HEX64,
        )


def test_citation_source_rules_match_assert_citation_authority():
    for citing in CANON_SPACE_VALUES:
        for cited in CANON_SPACE_VALUES:
            allowed = cited in CITATION_SOURCE_RULES[citing]
            if allowed:
                assert_citation_authority(citing, cited)
            else:
                with pytest.raises(CanonForkContractError, match="citation_scope"):
                    assert_citation_authority(citing, cited)


# ---------------------------------------------------------------------------
# Original Canon is read-only (D-35-02)
# ---------------------------------------------------------------------------


def test_original_canon_write_intent_is_rejected():
    original = _scope("original_canon")
    with pytest.raises(ValidationError, match="original_readonly"):
        CanonWriteIntent(
            scope=original,
            content="tampered original text",
            content_hash=content_sha256("tampered original text"),
        )


def test_original_readonly_gate():
    # Read queries on the Original space are fine.
    assert_original_readonly(CanonSpace.ORIGINAL_CANON, mutation=False)
    # Any mutation is forbidden.
    with pytest.raises(CanonForkContractError, match="original_readonly"):
        assert_original_readonly(CanonSpace.ORIGINAL_CANON, mutation=True)
    # Derivative spaces may carry mutation intents (still versioned).
    assert_original_readonly(CanonSpace.FANFICTION_CANON, mutation=True)


def test_write_intent_requires_replayable_content_hash():
    scope = _scope("user_interpretation")
    ok = CanonWriteIntent(
        scope=scope,
        content="a derivative interpretation",
        content_hash=content_sha256("a derivative interpretation"),
    )
    assert ok.status is CanonArtifactStatus.DRAFT
    assert ok.read_only is False
    with pytest.raises(ValidationError, match="content_hash_mismatch"):
        CanonWriteIntent(
            scope=scope,
            content="a derivative interpretation",
            content_hash=HEX64_B,
        )


def test_fanfiction_write_intent_is_candidate_only():
    scope = _scope("fanfiction_canon")
    intent = CanonWriteIntent(
        scope=scope,
        content="fanfiction draft",
        content_hash=content_sha256("fanfiction draft"),
    )
    assert intent.scope.space is CanonSpace.FANFICTION_CANON
    assert intent.scope.authority is CanonAuthority.CREATIVE_DRAFT
    assert intent.scope.citation_policy is CanonCitationPolicy.FANFICTION_ONLY


# ---------------------------------------------------------------------------
# Immutable lineage and deterministic hashes (D-35-03)
# ---------------------------------------------------------------------------


def test_scope_hash_is_deterministic_and_scope_sensitive():
    scope_a = _scope()
    scope_a_copy = _scope()
    assert scope_a.scope_hash() == scope_a_copy.scope_hash()
    assert len(scope_a.scope_hash()) == 64
    assert canonical_scope_hash({"owner_id": 1}) == canonical_scope_hash(
        {"owner_id": 1}
    )
    # Any scope dimension change must change the hash.
    for different in (
        _scope(version_key="v2"),
        _scope(space="fanfiction_canon"),
        _scope(owner_id=7),
        _scope(novel_id=9),
        _scope(namespace="user:7"),
        _scope(through_chapter=9),
        _scope(source_snapshot_hash=HEX64_B),
    ):
        assert different.scope_hash() != scope_a.scope_hash()


def test_scope_hash_binds_version_and_space_together():
    same_version_other_space = _scope(
        space="fanfiction_canon", version_key="v1", namespace="ff:1"
    )
    same_space_other_version = _scope(version_key="v2")
    assert (
        same_version_other_space.scope_hash() != same_space_other_version.scope_hash()
    )


def test_content_hash_is_stable_sha256():
    assert content_sha256("hello") == content_sha256("hello")
    assert len(content_sha256("hello")) == 64
    assert content_sha256("hello") != content_sha256("Hello")


# ---------------------------------------------------------------------------
# Original pipelines never accept derivative spaces (REQ-CRE-02)
# ---------------------------------------------------------------------------


def test_derivative_spaces_rejected_from_all_original_pipelines():
    for space in (CanonSpace.USER_INTERPRETATION, CanonSpace.FANFICTION_CANON):
        for pipeline in sorted(ORIGINAL_PIPELINES):
            with pytest.raises(CanonForkContractError, match="space_excluded"):
                assert_original_pipeline_input(space, pipeline)


def test_original_canon_is_the_only_original_pipeline_input():
    for pipeline in sorted(ORIGINAL_PIPELINES):
        assert_original_pipeline_input(CanonSpace.ORIGINAL_CANON, pipeline)
