"""Character/world world-projection parity through the QueryPlan adapter.

Phase 27-04 / REQ-WM-04 (D-01, D-02, D-05, D-06, D-08).

Proves the unavailable -> available parity for both character and world
projections through one shared QueryPlan dimension (``WORLD_PROJECTION``):

- no projection at all            -> explicit ``unavailable``, never empty-success;
- projection appears             -> ``available`` with allowlisted leaf EvidenceRefs
  that freeze into a FrozenManifest with durable lineage;
- hidden/future facts            -> never visible (abstained), never in the manifest;
- candidate-only claims          -> ``partial``, no promotion, no evidence;
- user interpretation            -> isolated into ``overrides``, never merged;
- unauthorized authority relabel and non-leaf refs fail closed;
- cross-owner claims             -> unavailable (fail closed).

The manifest / EvidenceRef lineage is verified end-to-end: fused dimension
results -> ``materialize_evidence_ref`` (leaf-only, frozen-snapshot bound) ->
``freeze_manifest`` -> ``verify_manifest`` -> the serialized consumer view.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services.queryplan.adapters import (
    READER_WORLD_PROJECTION,
    AvailabilityStatus,
    ChapterRecord,
    SourceSnapshot,
    chapter_content_hash,
    run_plan_adapters,
)
from app.services.queryplan.contracts import (
    AUTHORITY_CANON_FACT,
    AUTHORITY_PROBABLE_INFERENCE,
    AUTHORITY_USER_INTERPRETATION,
    WorldProjectionItem,
)
from app.services.queryplan.evidence import (
    EvidenceError,
    materialize_evidence_ref,
    verify_manifest,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import QueryDimension, QueryPlan
from app.services.queryplan.service import QueryPlanService
from app.services.world_model.contracts import (
    Authority,
    EvidenceRef,
    GateStatus,
)
from app.services.world_model.knowledge import (
    EpistemicAspect,
    EpistemicClaim,
    EpistemicStatus,
    PovKind,
    SourceKind,
)
from app.services.world_model.queries import world_projection_reader

pytestmark = pytest.mark.integration

HEX_SNAPSHOT = "c" * 64
CHAPTER_TEXT = "林安走进竹林，剑客随后现身。她低声问：你是谁？"


def _sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _excerpt(start: int = 0, end: int = 12) -> str:
    return CHAPTER_TEXT[start:end]


def make_source(*, version_id: int = 1) -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=version_id,
        snapshot_hash=HEX_SNAPSHOT,
        chapters=(
            ChapterRecord(
                chapter_id=1,
                chapter_number=1,
                content=CHAPTER_TEXT,
                content_hash=chapter_content_hash(CHAPTER_TEXT),
            ),
        ),
    )


def leaf_ref(
    *,
    start: int = 0,
    end: int = 12,
    chapter: int = 1,
    evidence_id: str = "ev-1",
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        chapter_id=chapter,
        chapter_number=chapter,
        source_start=start,
        source_end=end,
        content_hash=_sha256(_excerpt(start, end)),
        source_snapshot_hash=HEX_SNAPSHOT,
    )


def make_claim(
    *,
    key: str,
    authority: Authority = Authority.PROBABLE_INFERENCE,
    known_at: int = 1,
    disclosure_cutoff: int = 1,
    gate_status: GateStatus = GateStatus.PASSED,
    source_kind: SourceKind = SourceKind.CANON_SOURCE,
    subject: str = "lin-an",
    version_id: int = 1,
    owner_id: int = 1,
    ref: EvidenceRef | None = None,
) -> EpistemicClaim:
    return EpistemicClaim(
        claim_kind="character_knowledge",
        knowledge_key=key,
        subject=subject,
        aspect=EpistemicAspect.KNOWLEDGE,
        proposition=f"{subject} knows {key}",
        known_at=known_at,
        disclosure_cutoff=disclosure_cutoff,
        pov=subject,
        pov_kind=PovKind.CHARACTER,
        source_kind=source_kind,
        authority=authority,
        confidence=0.9,
        epistemic_status=EpistemicStatus.ASSERTED,
        transition_from=None,
        lineage=[key],
        source_refs=(ref if ref is not None else leaf_ref(evidence_id=key),),
        gate_status=gate_status,
        gate_reason=None,
        owner_id=owner_id,
        novel_id=1,
        version_id=version_id,
    )


def make_plan(*, through_chapter: int = 1, version_id: int = 1) -> QueryPlan:
    result = parse_query_plan(
        {
            "intent": "analysis",
            "owner_id": 1,
            "novel_id": 1,
            "version_id": version_id,
            "question_text": "林安知道什么？",
            "reading_progress": {
                "through_chapter": through_chapter,
                "snapshot_hash": HEX_SNAPSHOT,
                "full_book_authorized": False,
            },
            "chapter_range": {"chapter_start": 1, "chapter_end": through_chapter},
            "dimensions": ["world_projection"],
            "source": "analysis_chat",
        }
    )
    assert isinstance(result, QueryPlan), result
    return result


async def make_resolver(claims, *, kind: str = "character"):
    """Resolver returning a world projection reader over ``claims``."""

    async def resolver(reader_id: str):
        if reader_id != READER_WORLD_PROJECTION:
            return None

        async def reader(context):
            return await world_projection_reader(claims, context=context, kind=kind)

        return reader

    return resolver


def _first_world_result(results):
    return next(r for r in results if r.dimension == QueryDimension.WORLD_PROJECTION)


# ---------------------------------------------------------------------------
# Unavailable -> available parity for character and world (REQ-WM-04)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["character", "world"])
async def test_projection_parity_unavailable_then_available(kind):
    plan = make_plan()
    source = make_source()

    # 1. No projection at all: explicit unavailable, never empty-success.
    empty = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver((), kind=kind),
    )
    empty_result = _first_world_result(empty)
    assert empty_result.status == AvailabilityStatus.UNAVAILABLE
    assert empty_result.refs == ()
    assert empty_result.evidence_eligible is False
    fused_empty, manifest_empty = await QueryPlanService().build_manifest(
        plan, source, dimension_results=empty
    )
    assert manifest_empty.allowed_evidence_ids() == set()
    assert fused_empty.status == AvailabilityStatus.UNAVAILABLE

    # 2. A projection appears (character or world): both become available.
    claims = (
        make_claim(
            key="k-canon" if kind == "character" else "w-rule-1",
            authority=Authority.CANON_FACT,
        ),
        make_claim(
            key="k-infer" if kind == "character" else "w-rule-2",
            authority=Authority.PROBABLE_INFERENCE,
            ref=leaf_ref(start=2, end=16, evidence_id="ev-2"),
        ),
    )
    filled = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver(claims, kind=kind),
    )
    filled_result = _first_world_result(filled)
    assert filled_result.status == AvailabilityStatus.AVAILABLE
    assert filled_result.refs
    assert all(r.source_snapshot_hash == HEX_SNAPSHOT for r in filled_result.refs)
    assert filled_result.evidence_eligible is True

    # 3. Fuse + freeze: every ref materializes as an allowlisted leaf entry.
    service = QueryPlanService()
    fused, manifest = await service.build_manifest(
        plan, source, dimension_results=filled
    )
    assert manifest.allowed_evidence_ids()
    verify_manifest(manifest)
    leaf_keys = {
        f"qp:{r.chapter_id}:{r.source_start}:{r.source_end}:{r.content_hash}"
        for r in filled_result.refs
    }
    assert manifest.allowed_evidence_ids() == leaf_keys
    for entry in manifest.evidence:
        assert set(entry.canonical_dict()) == {
            "evidence_key",
            "chapter_id",
            "chapter_number",
            "source_start",
            "source_end",
            "content_hash",
            "source_snapshot_hash",
            "excerpt",
        }

    # 4. Consumer view preserves authority labels and binds to the manifest.
    view = service.consumer_view(plan, manifest, dimension_results=filled)
    assert view.world_projection is not None
    world = view.world_projection
    assert world["available"] is True
    assert world["status"] == "available"
    assert world["manifest_checksum"] == manifest.manifest_checksum
    assert world["snapshot_hash"] == HEX_SNAPSHOT
    assert AUTHORITY_CANON_FACT in world["authorities"]
    assert AUTHORITY_PROBABLE_INFERENCE in world["authorities"]
    item_authorities = {item["authority"] for item in world["items"]}
    assert item_authorities == {
        AUTHORITY_CANON_FACT,
        AUTHORITY_PROBABLE_INFERENCE,
    }
    assert all(item["kind"] == kind for item in world["items"])


# ---------------------------------------------------------------------------
# Disclosure timing: future / hidden facts never surface (D-05)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["character", "world"])
async def test_future_fact_is_explicitly_abstained_not_empty_success(kind):
    plan = make_plan(through_chapter=1)
    source = make_source()
    # The claim is known at chapter 1 but only disclosed at chapter 3.
    claims = (
        make_claim(
            key="k-future" if kind == "character" else "w-future",
            known_at=1,
            disclosure_cutoff=3,
            ref=leaf_ref(evidence_id="ev-future"),
        ),
    )
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver(claims, kind=kind),
    )
    result = _first_world_result(results)
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert result.reason == "world_projection_abstained"
    assert result.refs == ()

    fused, manifest = await QueryPlanService().build_manifest(
        plan, source, dimension_results=results
    )
    assert manifest.allowed_evidence_ids() == set()
    assert any(
        entry.kind == "dimension"
        and entry.status == AvailabilityStatus.UNAVAILABLE.value
        for entry in manifest.omitted
    )


def test_future_evidence_ref_beyond_cutoff_fails_closed_in_materialization():
    chapter_2_text = "第二章：叛军夜袭，城火映天。"
    source = SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash=HEX_SNAPSHOT,
        chapters=(
            ChapterRecord(
                chapter_id=1,
                chapter_number=1,
                content=CHAPTER_TEXT,
                content_hash=chapter_content_hash(CHAPTER_TEXT),
            ),
            ChapterRecord(
                chapter_id=2,
                chapter_number=2,
                content=chapter_2_text,
                content_hash=chapter_content_hash(chapter_2_text),
            ),
        ),
    )
    # A hand-forged leaf ref at chapter 2 with matching content hashes is still
    # rejected by the leaf materializer when the reader cutoff is chapter 1.
    forged = EvidenceRef(
        evidence_id="ev-forged",
        chapter_id=2,
        chapter_number=2,
        source_start=0,
        source_end=8,
        content_hash=_sha256(chapter_2_text[:8]),
        source_snapshot_hash=HEX_SNAPSHOT,
    )
    with pytest.raises(EvidenceError) as excinfo:
        materialize_evidence_ref(forged, source=source, through_chapter=1)
    assert excinfo.value.code == "beyond_cutoff"


# ---------------------------------------------------------------------------
# Candidate-only: never promoted, never evidence (D-02)
# ---------------------------------------------------------------------------


async def test_candidate_only_claims_report_partial_with_no_evidence():
    plan = make_plan()
    source = make_source()
    claims = (
        make_claim(
            key="k-candidate",
            gate_status=GateStatus.PENDING,
        ),
    )
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver(claims, kind="character"),
    )
    result = _first_world_result(results)
    assert result.status == AvailabilityStatus.PARTIAL
    assert result.reason == "world_projection_candidate_only"
    assert result.refs == ()
    assert result.evidence_eligible is False

    fused, manifest = await QueryPlanService().build_manifest(
        plan, source, dimension_results=results
    )
    assert manifest.allowed_evidence_ids() == set()
    # The candidate-only dimension is recorded as omitted — never evidence.
    assert any(
        entry.kind == "dimension" and entry.status == "partial"
        for entry in manifest.omitted
    )

    view = QueryPlanService().consumer_view(plan, manifest, dimension_results=results)
    assert view.world_projection is not None
    assert view.world_projection["available"] is False
    assert view.world_projection["status"] == "candidate_only"


# ---------------------------------------------------------------------------
# User interpretation isolation (D-06)
# ---------------------------------------------------------------------------


async def test_user_interpretation_is_isolated_from_candidate_projection():
    plan = make_plan()
    source = make_source()
    claims = (
        make_claim(key="k-canon", authority=Authority.CANON_FACT),
        make_claim(
            key="k-user-read",
            authority=Authority.USER_INTERPRETATION,
            source_kind=SourceKind.HUMAN_OVERRIDE,
            ref=leaf_ref(start=4, end=20, evidence_id="ev-user"),
        ),
    )
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver(claims, kind="character"),
    )
    result = _first_world_result(results)
    # Approved canon evidence makes the projection available; the override is
    # carried separately and never merged into the candidate items.
    assert result.status == AvailabilityStatus.AVAILABLE
    assert result.world_items
    assert result.world_overrides
    assert all(item.is_override is False for item in result.world_items)
    assert all(
        item.is_override and item.authority == AUTHORITY_USER_INTERPRETATION
        for item in result.world_overrides
    )

    fused, manifest = await QueryPlanService().build_manifest(
        plan, source, dimension_results=results
    )
    # The override's evidence ref never joins the frozen manifest allowlist.
    assert manifest.allowed_evidence_ids() == {f"qp:1:0:12:{_sha256(_excerpt(0, 12))}"}

    view = QueryPlanService().consumer_view(plan, manifest, dimension_results=results)
    assert view.world_projection is not None
    assert [item["claim_key"] for item in view.world_projection["items"]] == ["k-canon"]
    assert [item["claim_key"] for item in view.world_projection["overrides"]] == [
        "k-user-read"
    ]
    assert AUTHORITY_USER_INTERPRETATION not in view.world_projection["authorities"]


async def test_freeze_world_projection_manifest_records_candidate_and_override():
    """The evidence wiring helper records candidate-only and isolated overrides."""
    plan = make_plan()
    source = make_source()
    from app.services.queryplan.evidence import freeze_world_projection_manifest
    from app.services.world_model.queries import claim_to_world_projection_item

    candidate = claim_to_world_projection_item(
        make_claim(key="k-candidate", gate_status=GateStatus.PENDING),
        kind="character",
    )
    override = claim_to_world_projection_item(
        make_claim(
            key="k-override",
            authority=Authority.USER_INTERPRETATION,
            source_kind=SourceKind.HUMAN_OVERRIDE,
            ref=leaf_ref(start=4, end=20, evidence_id="ev-user"),
        ),
        kind="character",
    )
    passed = claim_to_world_projection_item(
        make_claim(key="k-canon", authority=Authority.CANON_FACT),
        kind="character",
    )
    manifest = freeze_world_projection_manifest(
        plan=plan,
        source=source,
        refs=(leaf_ref(evidence_id="ev-1"),),
        items=(candidate, passed),
        overrides=(override,),
    )
    verify_manifest(manifest)
    assert manifest.allowed_evidence_ids() == {f"qp:1:0:12:{_sha256(_excerpt(0, 12))}"}
    kinds = {entry.kind for entry in manifest.omitted}
    assert kinds == {"world_projection_candidate", "world_projection_override"}


# ---------------------------------------------------------------------------
# Fail-closed: unauthorized conversions and non-leaf refs (D-01/D-08)
# ---------------------------------------------------------------------------


def test_unauthorized_authority_upgrade_never_serializes():
    with pytest.raises(ValueError):
        WorldProjectionItem.model_validate(
            {
                "claim_key": "k-x",
                "kind": "character",
                "subject": "lin-an",
                "aspect": "knowledge",
                "proposition": "relabeled fact",
                "authority": "canon_fact_injected",
                "known_at": 1,
                "disclosure_cutoff": 1,
                "pov": "lin-an",
                "gate_status": "passed",
                "approved": True,
                "evidence_key": f"qp:1:0:12:{_sha256(_excerpt(0, 12))}",
                "chapter_id": 1,
                "chapter_number": 1,
                "source_start": 0,
                "source_end": 12,
                "content_hash": _sha256(_excerpt(0, 12)),
                "source_snapshot_hash": HEX_SNAPSHOT,
                "lineage": ["k-x"],
            }
        )


def test_non_leaf_evidence_key_fails_closed():
    with pytest.raises(ValueError):
        WorldProjectionItem.model_validate(
            {
                "claim_key": "k-x",
                "kind": "character",
                "subject": "lin-an",
                "aspect": "knowledge",
                "proposition": "chat summary",
                "authority": AUTHORITY_PROBABLE_INFERENCE,
                "known_at": 1,
                "disclosure_cutoff": 1,
                "pov": "lin-an",
                "gate_status": "passed",
                "approved": True,
                "evidence_key": "summary:chapter1",
                "chapter_id": 1,
                "chapter_number": 1,
                "source_start": 0,
                "source_end": 12,
                "content_hash": _sha256(_excerpt(0, 12)),
                "source_snapshot_hash": HEX_SNAPSHOT,
                "lineage": ["k-x"],
            }
        )


# ---------------------------------------------------------------------------
# Owner isolation and lineage (V2/V3, D-14)
# ---------------------------------------------------------------------------


async def test_cross_owner_claims_fail_closed():
    plan = make_plan()
    source = make_source()
    foreign = (
        make_claim(
            key="k-foreign",
            owner_id=2,
            ref=leaf_ref(evidence_id="ev-foreign"),
        ),
    )
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver(foreign, kind="character"),
    )
    result = _first_world_result(results)
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert result.reason == "no_world_projection"
    assert result.refs == ()


async def test_stale_snapshot_claims_fail_closed():
    plan = make_plan()
    source = make_source()
    stale = make_claim(
        key="k-stale",
        ref=EvidenceRef(
            evidence_id="ev-stale",
            chapter_id=1,
            chapter_number=1,
            source_start=0,
            source_end=12,
            content_hash=_sha256(_excerpt(0, 12)),
            source_snapshot_hash="0" * 64,
        ),
    )
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await make_resolver((stale,), kind="character"),
    )
    result = _first_world_result(results)
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert result.refs == ()


# ---------------------------------------------------------------------------
# No active-pointer / cutover (D-02)
# ---------------------------------------------------------------------------


def test_world_projection_contract_has_no_promotion_fields():
    plan = make_plan()
    source = make_source()
    claims = (make_claim(key="k-canon", authority=Authority.CANON_FACT),)
    import asyncio

    async def _run():
        results = await run_plan_adapters(
            plan,
            source=source,
            resolver=await make_resolver(claims, kind="character"),
        )
        fused, manifest = await QueryPlanService().build_manifest(
            plan, source, dimension_results=results
        )
        view = QueryPlanService().consumer_view(
            plan, manifest, dimension_results=results
        )
        return view.world_projection

    payload = asyncio.run(_run())
    assert payload is not None
    for forbidden in (
        "active_pointer",
        "promotion",
        "current_revision",
        "cutover",
    ):
        assert forbidden not in payload
        for item in payload["items"]:
            assert forbidden not in item
