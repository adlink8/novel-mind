"""Phase 35-03 unit tests: isolated retrieval and citation revalidation.

Covers D-35-01..D-35-03 within the deterministic retrieval/citation layer:
- scope predicates drop future leaves and stale snapshot hashes before ranking;
- the retrieval trace proves filters ran before ranking and never leaks future
  metadata;
- an empty namespace is ``absent`` and a namespace whose rows are all
  inadmissible is ``blocked`` — neither is a fake successful empty array;
- citation revalidation fails closed on owner, fork/version, cutoff and
  offset/hash drift, and only ever resolves authorized leaf evidence.

These tests are DB-free: the services are exercised with fake adapters and
fake leaf providers.
"""

from __future__ import annotations

import pytest

from app.services.canon_fork.citations import (
    CanonCitationRef,
    CanonCitationService,
    CitationBlockedReason,
    ResolvedLeaf,
    authorized_citation_namespaces,
)
from app.services.canon_fork.contracts import (
    CanonAuthority,
    CanonCitationPolicy,
    CanonSpace,
    content_sha256,
)
from app.services.canon_fork.retrieval import (
    CanonRetrievalService,
    CanonRetrievalTrace,
    RetrievalBlockReason,
    RetrievalStatus,
    filter_and_rank,
    index_adapter_for,
)
from tests.unit.canon_fork.helpers import _record, _scope

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64


class FakeAdapter:
    """Fake namespace/index adapter; ignores the session."""

    def __init__(self, records, *, space=CanonSpace.ORIGINAL_CANON):
        self._records = list(records)
        self.space = space

    async def load_scoped_candidates(self, session, *, scope):
        return list(self._records)


class FakeProvider:
    """Fake cited-space leaf provider; returns one resolved leaf."""

    def __init__(self, resolved):
        self._resolved = resolved

    async def resolve_leaf(self, session, *, ref, scope):
        return self._resolved


class NoneProvider:
    async def resolve_leaf(self, session, *, ref, scope):
        return None


# ---------------------------------------------------------------------------
# Scope predicates: future leaves and stale hashes never pass
# ---------------------------------------------------------------------------


def test_filter_and_rank_drops_future_before_ranking():
    scope = _scope(through_chapter=2)
    records = [
        _record("original:chapter:1", chapter=1),
        _record("original:chapter:3", chapter=3),
    ]
    ranked, beyond, stale = filter_and_rank(scope, records)
    assert beyond == 1
    assert stale == 0
    assert [r.candidate_key for r in ranked] == ["original:chapter:1"]


def test_filter_and_rank_drops_stale_snapshot_before_ranking():
    scope = _scope(source_snapshot_hash=HEX64)
    records = [
        _record("original:chapter:1", chapter=1, snapshot=HEX64),
        _record("original:chapter:2", chapter=2, snapshot=HEX64_B),
    ]
    ranked, beyond, stale = filter_and_rank(scope, records)
    assert stale == 1
    assert [r.candidate_key for r in ranked] == ["original:chapter:1"]


def test_filter_and_rank_keeps_scope_visible_only():
    scope = _scope(through_chapter=3, source_snapshot_hash=HEX64)
    records = [
        _record("original:chapter:1", chapter=1, snapshot=HEX64),
        _record("original:chapter:2", chapter=2, snapshot=HEX64),
        _record("original:chapter:3", chapter=3, snapshot=HEX64),
    ]
    ranked, beyond, stale = filter_and_rank(scope, records)
    assert (beyond, stale) == (0, 0)
    assert [r.chapter_number for r in ranked] == [1, 2, 3]


def test_rank_order_is_deterministic_and_scope_first():
    scope = _scope(source_snapshot_hash=HEX64)
    records = [
        _record("b:2", chapter=2, snapshot=HEX64),
        _record("a:1", chapter=1, snapshot=HEX64),
    ]
    ranked_a, _, _ = filter_and_rank(scope, records)
    ranked_b, _, _ = filter_and_rank(scope, list(reversed(records)))
    assert [r.candidate_key for r in ranked_a] == ["a:1", "b:2"]
    assert [r.candidate_key for r in ranked_b] == ["a:1", "b:2"]


# ---------------------------------------------------------------------------
# Retrieval service: absent / blocked / completed are distinct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_namespace_is_absent_not_fake_success():
    scope = _scope("original_canon", namespace="original:chapters")
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter([])
    )
    assert result.trace.status is RetrievalStatus.ABSENT
    assert result.trace.block_reason is None
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_all_future_rows_are_blocked_not_fake_success():
    scope = _scope("original_canon", namespace="original:chapters", through_chapter=2)
    records = [_record("original:chapter:5", chapter=5)]
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter(records)
    )
    assert result.trace.status is RetrievalStatus.BLOCKED
    assert result.trace.block_reason is RetrievalBlockReason.BEYOND_CUTOFF
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_all_stale_rows_are_blocked_not_fake_success():
    scope = _scope(
        "user_interpretation", namespace="user:1", source_snapshot_hash=HEX64
    )
    records = [_record("interpretation:artifact:1", chapter=1, snapshot=HEX64_B)]
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter(records)
    )
    assert result.trace.status is RetrievalStatus.BLOCKED
    assert result.trace.block_reason is RetrievalBlockReason.STALE_SNAPSHOT
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_completed_result_carries_authority_lineage_and_evidence():
    scope = _scope(
        "original_canon",
        namespace="original:chapters",
        version_key="original:v1",
        source_snapshot_hash=HEX64,
    )
    records = [
        _record(
            "original:chapter:1",
            chapter=1,
            snapshot=HEX64,
            namespace="original:chapters",
        ),
        _record(
            "original:chapter:2",
            chapter=2,
            snapshot=HEX64,
            namespace="original:chapters",
        ),
    ]
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter(records)
    )
    assert result.trace.status is RetrievalStatus.COMPLETED
    assert result.trace.block_reason is None
    assert [c.chapter_number for c in result.candidates] == [1, 2]
    for candidate in result.candidates:
        assert candidate.space is CanonSpace.ORIGINAL_CANON
        assert candidate.authority is CanonAuthority.SOURCE_TEXT
        assert candidate.citation_policy is CanonCitationPolicy.ORIGINAL_LEAF
        assert candidate.source_snapshot_hash == HEX64
        assert candidate.evidence_ref["chapter_number"] == candidate.chapter_number
        assert candidate.evidence_ref["content_hash"] == candidate.content_hash


@pytest.mark.asyncio
async def test_trace_counts_prove_scope_filters_before_ranking():
    scope = _scope(through_chapter=2, source_snapshot_hash=HEX64)
    records = [
        _record("original:chapter:1", chapter=1, snapshot=HEX64),
        _record("original:chapter:2", chapter=2, snapshot=HEX64),
        _record("original:chapter:3", chapter=3, snapshot=HEX64_B),  # future
        _record("original:chapter:1b", chapter=1, snapshot=HEX64_B),  # stale
    ]
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter(records)
    )
    trace = result.trace
    assert trace.loaded_scoped_count == 4
    assert trace.beyond_cutoff_count == 1
    assert trace.stale_snapshot_count == 1
    assert trace.ranked_count == 2
    assert trace.status is RetrievalStatus.COMPLETED
    assert trace.scope_hash == scope.scope_hash()
    assert trace.through_chapter == 2


def test_trace_exposes_only_counts_never_candidate_keys_or_hashes():
    scope = _scope(through_chapter=2, source_snapshot_hash=HEX64)
    ranked, beyond, stale = filter_and_rank(
        scope,
        [_record("original:chapter:9", chapter=9, hash_="9" * 64, snapshot=HEX64)],
    )
    trace = CanonRetrievalTrace(
        scope_hash=scope.scope_hash(),
        space=scope.space,
        namespace=scope.namespace,
        version_key=scope.version_key,
        through_chapter=scope.through_chapter,
        loaded_scoped_count=1,
        beyond_cutoff_count=beyond,
        stale_snapshot_count=stale,
        ranked_count=len(ranked),
        status=RetrievalStatus.BLOCKED,
        block_reason=RetrievalBlockReason.BEYOND_CUTOFF,
    )
    text = trace.__dict__.__repr__()
    assert "chapter:9" not in text
    assert ("9" * 64) not in text
    assert "content_hash" not in text


def test_adapter_dispatch_covers_all_three_spaces():
    assert (
        index_adapter_for(CanonSpace.ORIGINAL_CANON).space is CanonSpace.ORIGINAL_CANON
    )
    assert (
        index_adapter_for(CanonSpace.USER_INTERPRETATION).space
        is CanonSpace.USER_INTERPRETATION
    )
    assert (
        index_adapter_for(CanonSpace.FANFICTION_CANON).space
        is CanonSpace.FANFICTION_CANON
    )


def test_unknown_space_enum_fails_closed():
    # The closed enum itself rejects an unknown space at the boundary; the
    # adapter dispatch unknown branch is defensive and unreachable via the enum.
    with pytest.raises(ValueError):
        CanonSpace("narrative_memory")


# ---------------------------------------------------------------------------
# Citation revalidation: owner / fork-version / cutoff / offset-hash gates
# ---------------------------------------------------------------------------


def _ref(
    *,
    cited_space,
    cited_namespace,
    leaf_key="chapter:1",
    content_hash=HEX64,
    source_snapshot_hash=HEX64,
    start=None,
    end=None,
):
    return CanonCitationRef(
        cited_space=cited_space,
        cited_namespace=cited_namespace,
        leaf_key=leaf_key,
        content_hash=content_hash,
        source_snapshot_hash=source_snapshot_hash,
        source_start=start,
        source_end=end,
    )


def _resolved(**overrides):
    payload = dict(
        owner_id=1,
        novel_id=2,
        namespace="original:chapters",
        version_key=None,
        chapter_number=1,
        content="chapter one body",
        source_snapshot_hash=HEX64,
    )
    payload.update(overrides)
    return ResolvedLeaf(**payload)


@pytest.mark.asyncio
async def test_citation_policy_gate_blocks_cross_space():
    scope = _scope("original_canon")
    ref = _ref(
        cited_space=CanonSpace.FANFICTION_CANON,
        cited_namespace="ff:1",
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved())
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.CITATION_SCOPE


@pytest.mark.asyncio
async def test_wrong_owner_citation_blocked():
    scope = _scope("original_canon", owner_id=1)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(owner_id=7))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.OWNER_SCOPE


@pytest.mark.asyncio
async def test_wrong_novel_citation_blocked():
    scope = _scope("original_canon", novel_id=2)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(novel_id=9))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.NOVEL_SCOPE


@pytest.mark.asyncio
async def test_fork_version_mismatch_blocked():
    scope = _scope(
        "fanfiction_canon",
        namespace="fork:ff-main",
        version_key="ff-main",
        source_snapshot_hash=HEX64,
    )
    ref = _ref(
        cited_space=CanonSpace.FANFICTION_CANON,
        cited_namespace="fork:ff-other",
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(
            _resolved(
                namespace="fork:ff-other",
                version_key="ff-other",
            )
        ),
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.FORK_VERSION_MISMATCH


@pytest.mark.asyncio
async def test_version_drift_within_same_namespace_blocked():
    scope = _scope(
        "user_interpretation",
        namespace="user:1",
        version_key="v1",
        source_snapshot_hash=HEX64,
    )
    ref = _ref(
        cited_space=CanonSpace.USER_INTERPRETATION,
        cited_namespace="user:1",
        leaf_key="artifact:1",
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(
            _resolved(
                namespace="user:1",
                version_key="v2",  # future version of the same namespace
                chapter_number=1,
            )
        ),
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.FORK_VERSION_MISMATCH


@pytest.mark.asyncio
async def test_beyond_cutoff_citation_blocked():
    scope = _scope("original_canon", through_chapter=2)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(chapter_number=3))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.BEYOND_CUTOFF


@pytest.mark.asyncio
async def test_stale_snapshot_citation_blocked():
    scope = _scope("original_canon", source_snapshot_hash=HEX64)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(source_snapshot_hash=HEX64_B))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.STALE_HASH


@pytest.mark.asyncio
async def test_claimed_snapshot_must_replay_from_resolved_leaf():
    """A citation whose claimed snapshot differs from the resolved leaf fails."""
    scope = _scope("original_canon", source_snapshot_hash=HEX64)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON,
        cited_namespace="original:chapters",
        source_snapshot_hash=HEX64_B,  # claimed hash does not replay the leaf
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(source_snapshot_hash=HEX64))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.STALE_HASH


@pytest.mark.asyncio
async def test_stale_content_hash_citation_blocked():
    scope = _scope("original_canon", source_snapshot_hash=HEX64)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON,
        cited_namespace="original:chapters",
        content_hash=HEX64_B,  # does not replay from the leaf slice
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(
            _resolved(content="chapter one body", source_snapshot_hash=HEX64)
        ),
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.STALE_HASH


def test_invalid_offset_citation_blocked():
    # end <= start is rejected by the strict DTO before any resolution.
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError, match="invalid_citation_offset"):
        _ref(
            cited_space=CanonSpace.ORIGINAL_CANON,
            cited_namespace="original:chapters",
            start=5,
            end=5,  # end <= start
        )


@pytest.mark.asyncio
async def test_out_of_bounds_offset_citation_blocked():
    scope = _scope("original_canon", source_snapshot_hash=HEX64)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON,
        cited_namespace="original:chapters",
        start=5,
        end=500,  # beyond the leaf length
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(content="chapter one body"))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.INVALID_OFFSET


@pytest.mark.asyncio
async def test_unknown_leaf_citation_blocked():
    scope = _scope("original_canon")
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=NoneProvider()
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.UNKNOWN_LEAF


@pytest.mark.asyncio
async def test_allowed_citation_returns_revalidated_leaf():
    scope = _scope("original_canon", source_snapshot_hash=HEX64)
    content = "chapter one body"
    excerpt = "chapter"
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON,
        cited_namespace="original:chapters",
        content_hash=content_sha256(excerpt),
        start=0,
        end=len(excerpt),
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(_resolved(content=content, source_snapshot_hash=HEX64)),
    )
    assert verdict.allowed is True
    assert verdict.blocked_reason is None
    assert verdict.leaf is not None
    assert verdict.leaf.excerpt == excerpt
    assert verdict.leaf.content_hash == content_sha256(excerpt)
    assert verdict.leaf.authority is CanonAuthority.SOURCE_TEXT
    assert verdict.leaf.citation_policy is CanonCitationPolicy.ORIGINAL_LEAF
    assert verdict.leaf.evidence_ref["source_start"] == 0
    assert verdict.leaf.evidence_ref["source_end"] == len(excerpt)


def test_authorized_namespaces_follow_citation_source_rules():
    assert authorized_citation_namespaces(
        _scope("original_canon", namespace="original:chapters")
    ) == frozenset({"original:chapters"})
    assert authorized_citation_namespaces(
        _scope("fanfiction_canon", namespace="fork:ff-main")
    ) == frozenset({"fork:ff-main"})
    assert authorized_citation_namespaces(
        _scope("user_interpretation", namespace="user:1")
    ) == frozenset({"original:chapters", "user:1"})


def test_citation_ref_rejects_orphan_offset():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="invalid_citation_offset"):
        CanonCitationRef(
            cited_space=CanonSpace.ORIGINAL_CANON,
            cited_namespace="original:chapters",
            leaf_key="chapter:1",
            content_hash=HEX64,
            source_snapshot_hash=HEX64,
            source_start=0,  # no source_end
        )
