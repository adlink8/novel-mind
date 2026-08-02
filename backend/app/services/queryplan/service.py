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
from typing import Protocol

from app.schemas.reader_chat import ReaderAnswerEnvelope
from app.services.queryplan.adapters import (
    DimensionResult,
    SourceSnapshot,
    run_plan_adapters,
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
from app.services.queryplan.schemas import QueryPlan
from app.services.reader_chat.gateway import business_validate_answer


class QueryPlanServiceError(ValueError):
    """Configuration or pipeline error inside the QueryPlan service."""


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


class QueryPlanService:
    """Owns the queryplan -> cited-answer gateway validation boundary."""

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
