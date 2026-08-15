"""Adversarial citation and retrieval boundary gates (Phase 35-03).

REQ-FORK-01 / REQ-CRE-01 / REQ-CRE-02 / D-35-01..D-35-03: retrieval is
isolated by space/owner/version/cutoff and citations resolve only to authorized
leaf evidence. These red-team gates prove, with deterministic fakes and AST
source checks (no PostgreSQL):

- every cross-fork, wrong-owner, future-version, spoiler/cutoff, stale-hash,
  or Interpretation-masquerading-as-Canon citation fails closed with an
  auditable blocked reason;
- the retrieval source applies the cutoff and snapshot filters *before* any
  ranking (scope-before-ranking) and never leaks future metadata;
- an empty dimension is reported ``absent``/``blocked`` — never a fake
  successful empty array.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.services.canon_fork.citations import (
    CanonCitationRef,
    CanonCitationService,
    CitationBlockedReason,
    ResolvedLeaf,
)
from app.services.canon_fork.contracts import CanonSpace, content_sha256
from app.services.canon_fork.retrieval import (
    CanonIndexRecord,
    CanonRetrievalService,
    CanonRetrievalTrace,
    RetrievalBlockReason,
    RetrievalStatus,
    filter_and_rank,
)
from tests.unit.canon_fork.helpers import _record, _scope

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX64 = "a" * 64
HEX64_B = "b" * 64

CANON_FORK_DIR = Path(__file__).resolve().parents[2] / "app" / "services" / "canon_fork"
RETRIEVAL_SOURCE = (CANON_FORK_DIR / "retrieval.py").read_text(encoding="utf-8")
CITATIONS_SOURCE = (CANON_FORK_DIR / "citations.py").read_text(encoding="utf-8")


class FakeAdapter:
    def __init__(self, records, *, space=CanonSpace.ORIGINAL_CANON):
        self._records = list(records)
        self.space = space

    async def load_scoped_candidates(self, session, *, scope):
        return list(self._records)


class FakeProvider:
    def __init__(self, resolved):
        self._resolved = resolved

    async def resolve_leaf(self, session, *, ref, scope):
        return self._resolved


def _ref(
    *,
    cited_space=CanonSpace.ORIGINAL_CANON,
    cited_namespace="original:chapters",
    leaf_key="chapter:1",
    content_hash=HEX64,
    source_snapshot_hash=HEX64,
    start=None,
    end=None,
) -> CanonCitationRef:
    return CanonCitationRef(
        cited_space=cited_space,
        cited_namespace=cited_namespace,
        leaf_key=leaf_key,
        content_hash=content_hash,
        source_snapshot_hash=source_snapshot_hash,
        source_start=start,
        source_end=end,
    )


def _resolved(**overrides) -> ResolvedLeaf:
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


# ---------------------------------------------------------------------------
# Scope-before-ranking: source order and leak-free trace
# ---------------------------------------------------------------------------


def test_retrieval_source_filters_before_ranking():
    """The source must run cutoff filter, then snapshot filter, then sort."""
    cutoff_pos = RETRIEVAL_SOURCE.index("[r for r in records if within_cutoff(scope")
    stale_pos = RETRIEVAL_SOURCE.index("[r for r in within if snapshot_replays(scope")
    sort_pos = RETRIEVAL_SOURCE.index("sorted(replayed, key=_rank_key)")
    assert cutoff_pos < stale_pos < sort_pos


def test_retrieval_service_validates_scope_before_loading():
    """The frozen scope is validated before any adapter load."""
    validate_pos = RETRIEVAL_SOURCE.index("validate_scope(scope)")
    load_pos = RETRIEVAL_SOURCE.index(
        "load_scoped_candidates(self._session, scope=scope)"
    )
    assert validate_pos < load_pos


def test_retrieval_source_has_no_write_or_active_pointer():
    tree = ast.parse(RETRIEVAL_SOURCE)
    for token in ("active=True", "active = True", "session.add", "session.commit"):
        assert token not in RETRIEVAL_SOURCE, f"retrieval.py must not write: {token}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "active":
                    assert False, "retrieval.py must never assign active"


def test_blocked_trace_does_not_leak_future_metadata():
    scope = _scope("original_canon", namespace="original:chapters", through_chapter=2)
    records = [
        CanonIndexRecord(
            candidate_key="original:chapter:9",
            chapter_number=9,
            content_hash="9" * 64,
            source_snapshot_hash=HEX64,
        )
    ]
    ranked, beyond, stale = filter_and_rank(scope, records)
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
    text = json.dumps(trace.__dict__, default=str)
    # Future chapter id, its key and its hash must never leak into the trace.
    assert "chapter:9" not in text
    assert ("9" * 64) not in text
    assert "content_hash" not in text
    assert "candidate_key" not in text


# ---------------------------------------------------------------------------
# Citation boundary matrix: every non-compliant citation fails closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_fork_citation_blocked():
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
            _resolved(namespace="fork:ff-other", version_key="ff-other")
        ),
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.FORK_VERSION_MISMATCH
    assert verdict.detail  # auditable reason text is present


@pytest.mark.asyncio
async def test_future_version_citation_blocked():
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
                version_key="v999",
                chapter_number=1,
            )
        ),
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.FORK_VERSION_MISMATCH


@pytest.mark.asyncio
async def test_spoiler_beyond_cutoff_citation_blocked():
    scope = _scope("original_canon", through_chapter=2, source_snapshot_hash=HEX64)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(chapter_number=4))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.BEYOND_CUTOFF


@pytest.mark.asyncio
async def test_stale_source_hash_citation_blocked():
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
async def test_interpretation_masquerading_as_canon_blocked():
    """An original scope can never cite interpretation evidence as canon."""
    scope = _scope("original_canon")
    ref = _ref(
        cited_space=CanonSpace.USER_INTERPRETATION,
        cited_namespace="user:1",
        leaf_key="artifact:1",
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved())
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.CITATION_SCOPE


@pytest.mark.asyncio
async def test_interpretation_citation_rejects_unauthorized_namespace():
    """An interpretation scope cites original or its own namespace only."""
    scope = _scope(
        "user_interpretation",
        namespace="user:1",
        version_key="v1",
        source_snapshot_hash=HEX64,
    )
    body = "derivative text"
    ref = _ref(
        cited_space=CanonSpace.USER_INTERPRETATION,
        cited_namespace="user:1",
        leaf_key="artifact:1",
        content_hash=content_sha256(body),
        start=0,
        end=len(body),
    )
    ok = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(
            _resolved(
                namespace="user:1",
                version_key="v1",
                chapter_number=1,
                content=body,
            )
        ),
    )
    assert ok.allowed is True
    # A fanfiction leaf (or any unauthorized namespace) is never admissible.
    forged = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(
            _resolved(
                namespace="fork:ff-other",
                version_key="ff-other",
                chapter_number=1,
                content=body,
            )
        ),
    )
    assert forged.allowed is False
    assert forged.blocked_reason is CitationBlockedReason.FORK_VERSION_MISMATCH


@pytest.mark.asyncio
async def test_original_citation_requires_replayable_leaf_lineage():
    """An original citation is admitted only when the leaf replays the snapshot."""
    scope = _scope("original_canon", source_snapshot_hash=HEX64)
    body = "chapter one body"
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON,
        cited_namespace="original:chapters",
        content_hash=content_sha256(body),
        start=0,
        end=len(body),
    )
    ok = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(_resolved(content=body, source_snapshot_hash=HEX64)),
    )
    assert ok.allowed is True
    # The same content under a tampered snapshot lineage fails closed.
    tampered = await CanonCitationService(session=object()).revalidate(
        ref,
        scope=scope,
        provider=FakeProvider(_resolved(content=body, source_snapshot_hash=HEX64_B)),
    )
    assert tampered.allowed is False
    assert tampered.blocked_reason is CitationBlockedReason.STALE_HASH


def test_original_provider_resolves_chapter_leaves_only():
    """Original leaves come from the owned novel chapters, never derivative storage."""
    start = CITATIONS_SOURCE.index("class OriginalLeafProvider")
    end = CITATIONS_SOURCE.index("class InterpretationLeafProvider")
    block = CITATIONS_SOURCE[start:end]
    assert "CanonSpaceArtifact" not in block
    assert "select(Chapter)" in block
    assert "select(CanonFork)" not in block


# ---------------------------------------------------------------------------
# Empty-dimension fallback is never a fake successful empty array
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_namespace_reported_absent():
    scope = _scope("original_canon", namespace="original:chapters")
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter([])
    )
    assert result.trace.status is RetrievalStatus.ABSENT
    assert result.trace.status.value != RetrievalStatus.COMPLETED.value
    assert result.trace.block_reason is None
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_blocked_never_masks_as_empty_success():
    scope = _scope("user_interpretation", namespace="user:1", through_chapter=2)
    records = [
        _record("interpretation:artifact:1", chapter=3, snapshot=HEX64),  # future
        _record("interpretation:artifact:2", chapter=1, snapshot=HEX64_B),  # stale
    ]
    result = await CanonRetrievalService(session=object()).retrieve(
        scope, adapter=FakeAdapter(records)
    )
    assert result.trace.status is RetrievalStatus.BLOCKED
    assert result.trace.status.value != RetrievalStatus.COMPLETED.value
    assert result.trace.block_reason is not None
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_blocked_citation_never_returns_fake_empty_leaf():
    scope = _scope("original_canon", through_chapter=2, source_snapshot_hash=HEX64)
    ref = _ref(
        cited_space=CanonSpace.ORIGINAL_CANON, cited_namespace="original:chapters"
    )
    verdict = await CanonCitationService(session=object()).revalidate(
        ref, scope=scope, provider=FakeProvider(_resolved(chapter_number=3))
    )
    assert verdict.allowed is False
    assert verdict.blocked_reason is CitationBlockedReason.BEYOND_CUTOFF
    assert verdict.leaf is None
    assert verdict.detail  # the blocked reason is auditable


# ---------------------------------------------------------------------------
# Citation source never leaks raw free-text or cross-space bridges
# ---------------------------------------------------------------------------


def test_citation_source_bans_raw_rationale_fields():
    for token in ("rationale", "cache_key", "raw_question", "similarity"):
        assert token not in CITATIONS_SOURCE, (
            f"citations.py must not carry leaky field {token!r}"
        )


def test_citation_source_bans_active_pointer_and_write_path():
    for token in ("active=True", "active = True", "session.add", "session.commit"):
        assert token not in CITATIONS_SOURCE, f"citations.py must not write: {token}"
