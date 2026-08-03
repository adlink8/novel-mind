"""QueryPlan service: freeze a leaf-only manifest and gate the cited-answer gateway.

Phase 26-03 / REQ-QP-03 (D-07, D-08, D-09, D-12, D-14).

Proven call chain (asserted by ``tests/adversarial/test_queryplan_evidence.py``)::

    QueryPlanService.execute
      -> run_plan_adapters              (Phase 26-02: dimension chains)
      -> fuse_dimension_results         (Phase 26-02: deterministic fusion)
      -> materialize_evidence_ref       (this package: leaf/raw EvidenceRef)
      -> freeze_manifest                (this package: immutable Frozen Manifest)
      -> answer_producer                (injected: generates ReaderAnswerEnvelope)
      -> business_validate_answer       (reader_chat.gateway)
          -> validate_answer_against_manifest (reader_chat.schemas)
             only allowlisted leaf/raw EvidenceRef keys are accepted (D-08)

Fail-closed guarantees:

- Every fused evidence ref is re-sliced against the frozen snapshot; a stale
  hash, offset, owner, spoiler or version rejects before any answer exists.
- The manifest freezes before generation and is content-addressed: replay is by
  checksum and any mutation fails closed (``verify_manifest``).
- Non-leaf citations (summary / score / routing / chat) are rejected by the
  local cited-answer gate before a ``QueryPlanAnswer`` artifact is created.
- With no evidence the answer must abstain (D-09); a factual answer with no
  evidence is rejected.
- No NM promotion / active-pointer / consumer cutover write exists (D-14).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas.reader_chat import ReaderAnswerEnvelope
from app.services.queryplan.adapters import (
    DimensionResult,
    SourceSnapshot,
    run_plan_adapters,
)
from app.services.queryplan.contracts import (
    WorldProjectionItem,
    WorldProjectionView,
)
from app.services.queryplan.evidence import (
    FrozenManifest,
    build_omitted_records,
    effective_through_chapter,
    freeze_manifest,
    materialize_evidence_ref,
    verify_manifest,
)
from app.services.queryplan.fusion import (
    FusionResult,
    fuse_dimension_results,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    BlockedResult,
    ChapterRangeAnchor,
    QueryDimension,
    QueryPlan,
    QueryPlanIntent,
    QueryPlanRequest,
    ReadingProgress,
    SelectionAnchor,
    QUERYPLAN_DATASET_VERSION,
)
from app.services.reader_chat.gateway import business_validate_answer


class QueryPlanServiceError(ValueError):
    """Configuration or pipeline error inside the QueryPlan service."""


class ConsumerPlanBlocked(ValueError):
    """Stable fail-closed plan block carrying the machine-readable BlockedResult.

    D-02/D-03: an unknown/ambiguous/escaping plan is never guessed; the consumer
    receives the stable ``reason_code`` and a clarification, and nothing is
    executed or persisted.
    """

    def __init__(self, result: BlockedResult) -> None:
        super().__init__(result.message)
        self.result = result
        self.reason_code = result.reason_code.value


class AnswerProducer(Protocol):
    """Produces a candidate cited-answer envelope from the frozen manifest."""

    async def __call__(self, manifest: FrozenManifest) -> ReaderAnswerEnvelope: ...


@dataclass(frozen=True)
class QueryPlanAnswer:
    """The gated answer artifact: only created after the leaf-only gate passes."""

    plan: QueryPlan
    manifest: FrozenManifest
    envelope: ReaderAnswerEnvelope
    fused: FusionResult

    @property
    def abstained(self) -> bool:
        """D-09: an evidence-less answer must abstain (no factual blocks)."""
        return not self.envelope.answer_blocks


@dataclass(frozen=True)
class ConsumerManifestResult:
    """Manifest-only consumer result (context build) — no answer envelope yet.

    The model call happens later in the chat worker; this boundary freezes the
    retrieval/evidence graph and exposes the consumer view without generating.
    """

    plan: QueryPlan
    manifest: FrozenManifest
    fused: FusionResult

    @property
    def abstained(self) -> bool:
        """D-09: with no materialized evidence the answer must abstain."""
        return not self.manifest.allowed_evidence_ids()


@dataclass(frozen=True)
class CitationJumpTarget:
    """One leaf citation jump target (chapter + Unicode offsets + excerpt).

    Only leaf/raw manifest evidence may become a jump target (D-08); summaries,
    scores, routing metadata and chat text never appear here.
    """

    evidence_key: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    excerpt: str

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "evidence_key": self.evidence_key,
            "chapter_id": self.chapter_id,
            "chapter_number": self.chapter_number,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class ConsumerQueryPlanView:
    """Serializable consumer view: trace + availability + fallback + citation jump.

    Exposed by the shared seam to both Reader and Analysis Chat (D-01/D-05/D-08).
    """

    trace_id: str
    plan_hash: str
    intent: str
    anchor_kind: str | None
    cutoff_mode: str
    through_chapter: int
    full_book_authorized: bool
    availability: tuple[dict[str, str], ...]
    fallback: dict[str, Any]
    manifest_checksum: str
    allowed_evidence_ids: tuple[str, ...]
    citation_jump: tuple[CitationJumpTarget, ...]
    abstained: bool
    world_projection: dict[str, Any] | None = None

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "plan_hash": self.plan_hash,
            "intent": self.intent,
            "anchor_kind": self.anchor_kind,
            "cutoff_mode": self.cutoff_mode,
            "through_chapter": self.through_chapter,
            "full_book_authorized": self.full_book_authorized,
            "availability": [dict(entry) for entry in self.availability],
            "fallback": self.fallback,
            "manifest_checksum": self.manifest_checksum,
            "allowed_evidence_ids": list(self.allowed_evidence_ids),
            "citation_jump": [jump.canonical_dict() for jump in self.citation_jump],
            "abstained": self.abstained,
            "world_projection": self.world_projection,
        }


ConsumerExecution = QueryPlanAnswer | ConsumerManifestResult


def build_world_projection_view(
    manifest: FrozenManifest,
    dimension_results: Sequence[DimensionResult] | None,
) -> WorldProjectionView | None:
    """Serialize the world projection dimension into the consumer view.

    Returns ``None`` when the plan did not request the world projection
    dimension. ``available`` is True only for approved candidate evidence; a
    missing or fully hidden projection stays explicit ``unavailable`` — never an
    empty success (D-05). The view binds to the frozen manifest checksum so
    evidence lineage stays durable and replayable.
    """
    if not dimension_results:
        return None
    world_result = next(
        (
            result
            for result in dimension_results
            if result.dimension == QueryDimension.WORLD_PROJECTION
        ),
        None,
    )
    if world_result is None:
        return None
    if world_result.status == AvailabilityStatus.AVAILABLE:
        status = "available"
        available = True
    elif world_result.status == AvailabilityStatus.PARTIAL:
        status = "candidate_only"
        available = False
    else:
        status = "unavailable"
        available = False
    items: tuple[WorldProjectionItem, ...] = world_result.world_items
    overrides: tuple[WorldProjectionItem, ...] = world_result.world_overrides
    authorities = tuple(dict.fromkeys(item.authority for item in items))
    return WorldProjectionView(
        available=available,
        status=status,
        cutoff=manifest.through_chapter,
        items=items,
        overrides=overrides,
        authorities=authorities,
        manifest_checksum=manifest.manifest_checksum,
        snapshot_hash=manifest.snapshot_hash,
    )


class QueryPlanService:
    """Owns the queryplan -> cited-answer gateway validation boundary.

    Phase 26-04 (REQ-QP-04 / D-10): ``build_consumer_request`` +
    ``execute_consumer`` / ``execute_consumer_manifest`` form the shared seam
    consumed by both Reader Chat (selection anchor) and Analysis Chat
    (chapter_range anchor).
    """

    def __init__(
        self,
        *,
        resolver=None,
        adapters=None,
        answer_producer: AnswerProducer | None = None,
    ) -> None:
        self._resolver = resolver
        self._adapters = adapters
        self._answer_producer = answer_producer

    # ------------------------------------------------------------------ seam

    @staticmethod
    def build_consumer_request(
        *,
        intent: QueryPlanIntent,
        owner_id: int,
        novel_id: int,
        version_id: int,
        question_text: str,
        through_chapter: int,
        snapshot_hash: str,
        full_book_authorized: bool = False,
        whole_book: bool = False,
        selection: SelectionAnchor | None = None,
        chapter_range: ChapterRangeAnchor | None = None,
        source: str = "reader_chat",
        dataset_lineage: str = QUERYPLAN_DATASET_VERSION,
    ) -> dict[str, Any]:
        """Build the parser payload both consumers share (distinct anchors only).

        Reader Chat passes a ``selection`` anchor; Analysis Chat passes a
        ``chapter_range`` anchor. Default cutoff is reading-progress; whole-book
        requires the explicit per-novel switch (D-12), enforced by the parser.
        """
        request = QueryPlanRequest(
            intent=intent,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            question_text=question_text,
            reading_progress=ReadingProgress(
                through_chapter=through_chapter,
                snapshot_hash=snapshot_hash,
                full_book_authorized=full_book_authorized,
            ),
            whole_book=whole_book,
            selection=selection,
            chapter_range=chapter_range,
            source=source,
            dataset_lineage=dataset_lineage,
        )
        return request.model_dump(mode="json")

    @staticmethod
    def parse_consumer_request(payload: dict[str, Any]) -> QueryPlan:
        """Deterministic fail-closed parse; raises ``ConsumerPlanBlocked``."""
        plan = parse_query_plan(payload)
        if isinstance(plan, BlockedResult):
            raise ConsumerPlanBlocked(plan)
        return plan

    def consumer_view(
        self,
        plan: QueryPlan,
        manifest: FrozenManifest,
        *,
        dimension_results: Sequence[DimensionResult] | None = None,
    ) -> ConsumerQueryPlanView:
        """Build the consumer-facing trace/availability/fallback/citation view."""
        availability = tuple(
            {
                "dimension": entry.dimension.value,
                "status": entry.status.value,
                "reason": entry.reason,
                "provenance": entry.provenance,
            }
            for entry in plan.availability
        )
        citation_jump = tuple(
            CitationJumpTarget(
                evidence_key=entry.evidence_key,
                chapter_id=entry.chapter_id,
                chapter_number=entry.chapter_number,
                source_start=entry.source_start,
                source_end=entry.source_end,
                excerpt=entry.excerpt,
            )
            for entry in manifest.evidence
        )
        world_projection = build_world_projection_view(manifest, dimension_results)
        return ConsumerQueryPlanView(
            trace_id=plan.trace.trace_id,
            plan_hash=plan.trace.canonical_payload_hash,
            intent=plan.intent.value,
            anchor_kind=plan.anchor.kind if plan.anchor is not None else None,
            cutoff_mode=plan.spoiler_cutoff.mode.value,
            through_chapter=plan.spoiler_cutoff.through_chapter,
            full_book_authorized=bool(plan.spoiler_cutoff.full_book_authorized),
            availability=availability,
            fallback=plan.fallback.model_dump(mode="json"),
            manifest_checksum=manifest.manifest_checksum,
            allowed_evidence_ids=tuple(sorted(manifest.allowed_evidence_ids())),
            citation_jump=citation_jump,
            abstained=not manifest.allowed_evidence_ids(),
            world_projection=(
                world_projection.model_dump(mode="json")
                if world_projection is not None
                else None
            ),
        )

    async def execute_consumer_manifest(
        self,
        payload: dict[str, Any],
        *,
        source: SourceSnapshot,
        dimension_results: Sequence[DimensionResult] | None = None,
        budget: int | None = None,
    ) -> tuple[ConsumerManifestResult, ConsumerQueryPlanView]:
        """Context-build path: parse -> adapters -> fusion -> freeze, no model.

        Used by both chat consumers at send time to freeze the retrieval/
        evidence graph and surface trace/availability/fallback/citation jump.
        """
        plan = self.parse_consumer_request(payload)
        fused, manifest = await self.build_manifest(
            plan,
            source,
            dimension_results=dimension_results,
            budget=budget,
        )
        result = ConsumerManifestResult(plan=plan, manifest=manifest, fused=fused)
        return result, self.consumer_view(
            plan, manifest, dimension_results=fused.dimension_results
        )

    async def execute_consumer(
        self,
        payload: dict[str, Any],
        *,
        source: SourceSnapshot,
        dimension_results: Sequence[DimensionResult] | None = None,
        budget: int | None = None,
        answer_producer: AnswerProducer,
    ) -> tuple[QueryPlanAnswer, ConsumerQueryPlanView]:
        """Full path: parse -> pipeline -> leaf-only cited-answer gate.

        The answer envelope is validated against the frozen manifest allowlist
        (``business_validate_answer``) before a ``QueryPlanAnswer`` exists.
        """
        plan = self.parse_consumer_request(payload)
        answer = await self.execute(
            plan,
            source,
            dimension_results=dimension_results,
            budget=budget,
            answer_producer=answer_producer,
        )
        return answer, self.consumer_view(
            plan, answer.manifest, dimension_results=answer.fused.dimension_results
        )

    # ------------------------------------------------------------------ core

    def gate_answer(
        self, manifest: FrozenManifest, envelope: ReaderAnswerEnvelope
    ) -> None:
        """Local cited-answer gate; runs before any answer artifact is created.

        ``manifest.allowed_evidence_ids()`` is the gateway input: only leaf/raw
        EvidenceRef keys that were materialized against the frozen snapshot may
        be cited. Raises ``ValueError`` on any non-leaf / unallowlisted ref.
        """
        verify_manifest(manifest)
        business_validate_answer(
            envelope, allowed_evidence_ids=manifest.allowed_evidence_ids()
        )

    async def build_manifest(
        self,
        plan: QueryPlan,
        source: SourceSnapshot,
        *,
        dimension_results: Sequence[DimensionResult] | None = None,
        budget: int | None = None,
    ) -> tuple[FusionResult, FrozenManifest]:
        """Adapters -> fusion -> leaf materialization -> immutable freeze."""
        results = (
            tuple(dimension_results)
            if dimension_results is not None
            else await run_plan_adapters(
                plan,
                source=source,
                resolver=self._resolver,
                adapters=self._adapters,
            )
        )
        if budget is None:
            budget = plan.answer_constraints.max_evidence_refs
        through_chapter = effective_through_chapter(plan, source)
        fused = fuse_dimension_results(
            results,
            source=source,
            through_chapter=through_chapter,
            cutoff_mode=plan.spoiler_cutoff.mode.value,
            budget=budget,
        )
        evidence = tuple(
            materialize_evidence_ref(
                fused_entry.ref,
                source=source,
                through_chapter=through_chapter,
                cutoff_mode=plan.spoiler_cutoff.mode,
            )
            for fused_entry in fused.fused_evidence
        )
        manifest = freeze_manifest(
            plan=plan,
            source=source,
            evidence=evidence,
            omitted=build_omitted_records(fused),
        )
        return fused, manifest

    async def execute(
        self,
        plan: QueryPlan,
        source: SourceSnapshot,
        *,
        dimension_results: Sequence[DimensionResult] | None = None,
        budget: int | None = None,
        answer_producer: AnswerProducer | None = None,
    ) -> QueryPlanAnswer:
        """Run the full pipeline and return a gated cited-answer artifact.

        Raises before returning on any evidence mutation, non-leaf citation or
        uncited factual block, so no answer / Artifact is produced.
        """
        fused, manifest = await self.build_manifest(
            plan,
            source,
            dimension_results=dimension_results,
            budget=budget,
        )
        producer = answer_producer or self._answer_producer
        if producer is None:
            raise QueryPlanServiceError(
                "no answer producer configured; cannot create a cited answer"
            )
        envelope = await producer(manifest)
        self.gate_answer(manifest, envelope)
        return QueryPlanAnswer(
            plan=plan, manifest=manifest, envelope=envelope, fused=fused
        )
