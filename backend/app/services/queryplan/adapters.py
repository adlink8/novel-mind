"""QueryPlan dimension adapters with explicit availability and the
exact-reader -> heuristic-candidate -> stable-reason fallback chain.

Phase 26-02 / REQ-QP-02 + REQ-QP-05 (D-04, D-05, D-06, D-12, D-14, D-15).

Every requested dimension always reports ``available`` / ``partial`` /
``unavailable`` with a stable reason code and provenance (D-05). A missing
reader or missing coverage is never an empty success.

Single fallback chain (D-15), replayable for identical inputs:

1. ``exact_reader`` — the registered exact/domain reader runs first. Only its
   *verified* refs may mark a dimension ``available``.
2. ``deterministic_heuristic`` — only when the reader is absent, unsupported or
   explicitly returns unavailable. Runs deterministic keyword candidate recall
   inside the same owner/version/cutoff/snapshot scope. Candidates are
   recall-only (``evidence_eligible=False``) and never become facts,
   ``EvidenceRef`` or citations.
3. ``stable_unavailable`` — no candidates or insufficient coverage ends as a
   stable ``partial`` / ``unavailable`` reason.

This module is pure: it operates on a frozen ``SourceSnapshot`` (chapter text +
content hashes) and never touches the database. Production readers are injected
through a ``ReaderResolver`` so the boundary stays unit-testable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.services.queryplan.contracts import WorldProjectionItem
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    CutoffMode,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
    QueryPlan,
)


class ReasonCode(StrEnum):
    """Stable, replayable reason codes for dimension availability (D-05/D-15)."""

    READER_OK = "reader_ok"
    READER_ZERO_HITS = "reader_zero_hits_in_scope"
    HEURISTIC_CANDIDATE_ONLY = "heuristic_candidate_only"
    READER_UNAVAILABLE = "reader_unavailable"
    DIMENSION_UNAVAILABLE = "dimension_unavailable"
    NO_WORLD_PROJECTION = "no_world_projection"
    WORLD_PROJECTION_ABSTAINED = "world_projection_abstained"
    WORLD_PROJECTION_CANDIDATE_ONLY = "world_projection_candidate_only"


PROVENANCE_EXACT_READER = "exact_reader_v1"
PROVENANCE_HEURISTIC = "deterministic_heuristic_v1"
PROVENANCE_CONTRACT = "deterministic_contract_v1"
PROVENANCE_WORLD_PROJECTION = "world_projection_reader_v1"

# Production reader ids wired through the ReaderResolver. ``None`` means no
# production reader exists yet (character_state / world_rules → Phase 27), which
# is exactly the stage-2 fallback trigger required by D-15.
READER_KNOWLEDGE_UNITS_CHUNKS = "knowledge_units_chunks"
READER_TIMELINE_EVENTS = "timeline_events"
READER_RELATIONSHIPS_GRAPH = "relationships_graph"
READER_CLUES_QUERY = "clues_query"
READER_KNOWLEDGE_UNITS_UNITS = "knowledge_units_units"
READER_WORLD_PROJECTION = "world_projection"


# ---------------------------------------------------------------------------
# Frozen source snapshot (D-07: leaf chapter + Unicode offsets + content hash)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChapterRecord:
    chapter_id: int
    chapter_number: int
    content: str
    content_hash: str


@dataclass(frozen=True)
class SourceSnapshot:
    owner_id: int
    novel_id: int
    version_id: int
    snapshot_hash: str
    chapters: tuple[ChapterRecord, ...]
    full_book_authorized: bool = False


def chapter_content_hash(content: str) -> str:
    """Deterministic 64-hex content hash for one frozen chapter."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Adapter input/output contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReaderContext:
    owner_id: int
    novel_id: int
    version_id: int
    through_chapter: int
    snapshot_hash: str
    dimension: QueryDimension
    question: str | None = None


class ReaderCallable(Protocol):
    async def __call__(self, context: ReaderContext) -> ReaderOutcome | None: ...


class ReaderResolver(Protocol):
    async def __call__(self, reader_id: str) -> ReaderCallable | None: ...


class ReaderUnavailableError(RuntimeError):
    """Exact/domain reader explicitly declines service for the current scope
    (D-15 stage 2 trigger)."""


@dataclass(frozen=True)
class ReaderOutcome:
    """Verified output of an exact/domain reader.

    Only ``refs`` from a successfully run reader may mark a dimension available.
    An empty ``refs`` is an honest zero-hit reader result (partial), never a
    claim that the dimension is missing (D-05).
    """

    refs: tuple[EvidenceRef, ...]
    provenance: str = PROVENANCE_EXACT_READER


class WorldProjectionUnavailableError(RuntimeError):
    """The world projection reader explicitly declines service (no projection)."""


@dataclass(frozen=True)
class WorldProjectionOutcome:
    """Verified output of the world projection reader (Phase 27-04 / REQ-WM-04).

    ``status`` is one of ``available`` / ``candidate_only`` / ``abstained``:

    - ``available`` — at least one approved candidate claim is visible at the
      cutoff/POV; its leaf evidence refs may mark the dimension available.
    - ``candidate_only`` — claims exist but none are approved yet; reported as
      partial, never evidence (D-02 candidate-only, no promotion).
    - ``abstained`` — the projection exists but nothing is visible at the
      cutoff/POV (D-05 disclosure); reported as explicit unavailable, never
      empty-success.

    ``refs`` are the allowlisted leaf EvidenceRefs of the approved candidates.
    ``items`` carry the authority / disclosure / evidence / lineage contract for
    the browser; ``overrides`` are the isolated user-interpretation claims
    (D-06) — they never join the candidate items or the manifest evidence.
    """

    status: str
    cutoff: int
    refs: tuple[EvidenceRef, ...]
    items: tuple[WorldProjectionItem, ...] = ()
    overrides: tuple[WorldProjectionItem, ...] = ()
    provenance: str = PROVENANCE_WORLD_PROJECTION


class WorldProjectionReaderCallable(Protocol):
    """A world projection reader; ``None`` means no projection exists at all."""

    async def __call__(
        self, context: ReaderContext
    ) -> WorldProjectionOutcome | None: ...


@dataclass(frozen=True)
class HeuristicCandidate:
    """Deterministic recall candidate; never a fact, EvidenceRef or citation.

    ``evidence_eligible`` is structurally False: candidates may improve recall
    for downstream stages but can never be cited as evidence (D-15, D-08).
    """

    dimension: QueryDimension
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    source_snapshot_hash: str
    snippet: str
    evidence_eligible: bool = False


@dataclass(frozen=True)
class HeuristicOutcome:
    candidates: tuple[HeuristicCandidate, ...]
    provenance: str = PROVENANCE_HEURISTIC


@dataclass(frozen=True)
class DimensionResult:
    """Terminal, replayable availability for one dimension."""

    dimension: QueryDimension
    status: AvailabilityStatus
    reason: str
    provenance: str
    stage: FallbackStage
    refs: tuple[EvidenceRef, ...] = ()
    candidates: tuple[HeuristicCandidate, ...] = ()
    world_items: tuple[WorldProjectionItem, ...] = ()
    world_overrides: tuple[WorldProjectionItem, ...] = ()

    @property
    def evidence_eligible(self) -> bool:
        """Verified evidence may feed citations; candidates never may."""
        return self.status == AvailabilityStatus.AVAILABLE and bool(self.refs)

    @property
    def hit_count(self) -> int:
        return len(self.refs) + len(self.candidates)


# ---------------------------------------------------------------------------
# Deterministic keyword heuristics (candidate recall only)
# ---------------------------------------------------------------------------

CHARACTER_STATE_KEYWORDS: tuple[str, ...] = (
    "目的",
    "目标",
    "动机",
    "渴望",
    "愿望",
    "想要",
    "打算",
    "计划",
    "决心",
    "知道",
    "记得",
    "想起",
    "认为",
    "怀疑",
    "猜测",
    "意识到",
    "明白",
    "领悟",
    "goal",
    "intend",
    "want",
    "wish",
    "desire",
    "plan",
    "resolve",
    "know",
    "remember",
    "believe",
    "suspect",
    "realize",
    "understand",
)

WORLD_RULES_KEYWORDS: tuple[str, ...] = (
    "规则",
    "法则",
    "规矩",
    "规定",
    "禁忌",
    "禁令",
    "王国",
    "帝国",
    "王朝",
    "宗门",
    "门派",
    "世家",
    "家族",
    "势力",
    "组织",
    "阵营",
    "神器",
    "法宝",
    "道具",
    "装备",
    "结界",
    "封印",
    "阵法",
    "传承",
    "秘术",
    "rule",
    "law",
    "taboo",
    "forbidden",
    "kingdom",
    "empire",
    "faction",
    "sect",
    "clan",
    "artifact",
    "relic",
    "item",
    "seal",
    "barrier",
)

GENERIC_HEURISTIC_KEYWORDS: tuple[str, ...] = (
    "事件",
    "发生",
    "线索",
    "伏笔",
    "关系",
    "时间",
    "地点",
    "转折",
    "冲突",
    "战斗",
    "谈话",
    "决定",
    "开始",
    "结束",
    "出现",
    "离开",
    "进入",
    "event",
    "clue",
    "foreshadow",
    "relation",
    "timeline",
    "turn",
    "battle",
    "decide",
    "appear",
    "leave",
    "enter",
)


def extract_heuristic_candidates(
    *,
    dimension: QueryDimension,
    chapters: Sequence[ChapterRecord],
    keywords: Sequence[str],
    snapshot_hash: str,
    through_chapter: int,
    window_before: int = 40,
    window_after: int = 80,
) -> tuple[HeuristicCandidate, ...]:
    """Deterministic keyword candidate recall inside the frozen snapshot scope.

    Runs only over chapters at or before ``through_chapter``, dedupes by span,
    sorts by (chapter_number, source_start, source_end, content_hash) and always
    yields candidates with ``evidence_eligible=False``.
    """
    if not keywords:
        return ()
    seen: set[tuple[int, int, int]] = set()
    candidates: list[HeuristicCandidate] = []
    for chapter in chapters:
        if int(chapter.chapter_number) > through_chapter:
            continue
        content = chapter.content
        positions: list[int] = []
        for keyword in keywords:
            if not keyword:
                continue
            start = 0
            while True:
                idx = content.find(keyword, start)
                if idx < 0:
                    break
                positions.append(idx)
                start = idx + 1
        positions.sort()
        for pos in positions:
            lo = max(0, pos - window_before)
            hi = min(len(content), pos + len(keyword) + window_after)
            if hi <= lo:
                continue
            key = (chapter.chapter_id, lo, hi)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                HeuristicCandidate(
                    dimension=dimension,
                    chapter_id=chapter.chapter_id,
                    chapter_number=chapter.chapter_number,
                    source_start=lo,
                    source_end=hi,
                    content_hash=chapter.content_hash,
                    source_snapshot_hash=snapshot_hash,
                    snippet=content[lo:hi],
                    evidence_eligible=False,
                )
            )
    candidates.sort(
        key=lambda c: (c.chapter_number, c.source_start, c.source_end, c.content_hash)
    )
    return tuple(candidates)


# ---------------------------------------------------------------------------
# Pure three-stage chain evaluation (D-15)
# ---------------------------------------------------------------------------


def evaluate_dimension_chain(
    dimension: QueryDimension,
    *,
    reader_outcome: ReaderOutcome | None,
    heuristic_outcome: HeuristicOutcome | None,
) -> DimensionResult:
    """Reduce one dimension's fallback chain to a stable terminal result.

    Stage 1 wins whenever the exact reader ran, even with zero hits (honest
    partial, never empty-success). Stage 2 is only reachable when the reader is
    absent/declined. Stage 3 is the terminal stable reason when no candidates or
    no heuristic exist.
    """
    if reader_outcome is not None and reader_outcome.refs:
        return DimensionResult(
            dimension=dimension,
            status=AvailabilityStatus.AVAILABLE,
            reason=ReasonCode.READER_OK.value,
            provenance=reader_outcome.provenance,
            stage=FallbackStage.EXACT_READER,
            refs=reader_outcome.refs,
        )
    if reader_outcome is not None:
        return DimensionResult(
            dimension=dimension,
            status=AvailabilityStatus.PARTIAL,
            reason=ReasonCode.READER_ZERO_HITS.value,
            provenance=reader_outcome.provenance,
            stage=FallbackStage.EXACT_READER,
        )
    if heuristic_outcome is not None and heuristic_outcome.candidates:
        return DimensionResult(
            dimension=dimension,
            status=AvailabilityStatus.PARTIAL,
            reason=ReasonCode.HEURISTIC_CANDIDATE_ONLY.value,
            provenance=heuristic_outcome.provenance,
            stage=FallbackStage.DETERMINISTIC_HEURISTIC,
            candidates=heuristic_outcome.candidates,
        )
    if heuristic_outcome is not None:
        return DimensionResult(
            dimension=dimension,
            status=AvailabilityStatus.UNAVAILABLE,
            reason=ReasonCode.DIMENSION_UNAVAILABLE.value,
            provenance=heuristic_outcome.provenance,
            stage=FallbackStage.STABLE_UNAVAILABLE,
        )
    return DimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.UNAVAILABLE,
        reason=ReasonCode.READER_UNAVAILABLE.value,
        provenance=PROVENANCE_CONTRACT,
        stage=FallbackStage.STABLE_UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionAdapter:
    dimension: QueryDimension
    reader_id: str | None
    heuristic_keywords: tuple[str, ...] = ()

    @property
    def has_heuristic(self) -> bool:
        return bool(self.heuristic_keywords)


DEFAULT_ADAPTERS: dict[QueryDimension, DimensionAdapter] = {
    QueryDimension.RAW_TEXT: DimensionAdapter(
        QueryDimension.RAW_TEXT,
        READER_KNOWLEDGE_UNITS_CHUNKS,
        GENERIC_HEURISTIC_KEYWORDS,
    ),
    QueryDimension.EVENTS_CAUSALITY: DimensionAdapter(
        QueryDimension.EVENTS_CAUSALITY,
        READER_TIMELINE_EVENTS,
        GENERIC_HEURISTIC_KEYWORDS,
    ),
    QueryDimension.CHARACTER_STATE: DimensionAdapter(
        QueryDimension.CHARACTER_STATE, None, CHARACTER_STATE_KEYWORDS
    ),
    QueryDimension.RELATIONS: DimensionAdapter(
        QueryDimension.RELATIONS, READER_RELATIONSHIPS_GRAPH, GENERIC_HEURISTIC_KEYWORDS
    ),
    QueryDimension.TIMELINE: DimensionAdapter(
        QueryDimension.TIMELINE, READER_TIMELINE_EVENTS, GENERIC_HEURISTIC_KEYWORDS
    ),
    QueryDimension.CLUES_FORESHAOWING: DimensionAdapter(
        QueryDimension.CLUES_FORESHAOWING,
        READER_CLUES_QUERY,
        GENERIC_HEURISTIC_KEYWORDS,
    ),
    QueryDimension.WORLD_RULES: DimensionAdapter(
        QueryDimension.WORLD_RULES, None, WORLD_RULES_KEYWORDS
    ),
    QueryDimension.NARRATIVE_UNITS: DimensionAdapter(
        QueryDimension.NARRATIVE_UNITS,
        READER_KNOWLEDGE_UNITS_UNITS,
        GENERIC_HEURISTIC_KEYWORDS,
    ),
    QueryDimension.WORLD_PROJECTION: DimensionAdapter(
        QueryDimension.WORLD_PROJECTION, READER_WORLD_PROJECTION, ()
    ),
}


async def run_world_projection_adapter(
    adapter: DimensionAdapter,
    *,
    source: SourceSnapshot,
    through_chapter: int,
    resolver: ReaderResolver | None = None,
    question: str | None = None,
) -> DimensionResult:
    """Execute the world projection dimension against the injected reader.

    Fail-closed availability (REQ-WM-04 / D-05):
    - no world projection at all           -> ``unavailable`` (never empty-success)
    - projection exists but nothing visible-> ``unavailable`` (abstained)
    - projection exists, none approved     -> ``partial``  (candidate-only)
    - projection exists with approved refs -> ``available`` (evidence-eligible)
    """
    context = ReaderContext(
        owner_id=source.owner_id,
        novel_id=source.novel_id,
        version_id=source.version_id,
        through_chapter=through_chapter,
        snapshot_hash=source.snapshot_hash,
        dimension=adapter.dimension,
        question=question,
    )
    if resolver is None:
        return DimensionResult(
            dimension=adapter.dimension,
            status=AvailabilityStatus.UNAVAILABLE,
            reason=ReasonCode.NO_WORLD_PROJECTION.value,
            provenance=PROVENANCE_CONTRACT,
            stage=FallbackStage.STABLE_UNAVAILABLE,
        )
    try:
        reader = await resolver(adapter.reader_id)
    except Exception:
        reader = None
    if reader is None:
        return DimensionResult(
            dimension=adapter.dimension,
            status=AvailabilityStatus.UNAVAILABLE,
            reason=ReasonCode.NO_WORLD_PROJECTION.value,
            provenance=PROVENANCE_CONTRACT,
            stage=FallbackStage.STABLE_UNAVAILABLE,
        )
    try:
        outcome = await reader(context)
    except WorldProjectionUnavailableError:
        outcome = None
    except Exception:
        outcome = None
    if outcome is None:
        return DimensionResult(
            dimension=adapter.dimension,
            status=AvailabilityStatus.UNAVAILABLE,
            reason=ReasonCode.NO_WORLD_PROJECTION.value,
            provenance=PROVENANCE_CONTRACT,
            stage=FallbackStage.STABLE_UNAVAILABLE,
        )

    refs = tuple(
        ref for ref in outcome.refs if int(ref.chapter_number) <= through_chapter
    )
    if outcome.status == "available" and refs:
        return DimensionResult(
            dimension=adapter.dimension,
            status=AvailabilityStatus.AVAILABLE,
            reason=ReasonCode.READER_OK.value,
            provenance=outcome.provenance,
            stage=FallbackStage.EXACT_READER,
            refs=refs,
            world_items=outcome.items,
            world_overrides=outcome.overrides,
        )
    if outcome.status == "candidate_only":
        return DimensionResult(
            dimension=adapter.dimension,
            status=AvailabilityStatus.PARTIAL,
            reason=ReasonCode.WORLD_PROJECTION_CANDIDATE_ONLY.value,
            provenance=outcome.provenance,
            stage=FallbackStage.EXACT_READER,
            world_items=outcome.items,
            world_overrides=outcome.overrides,
        )
    return DimensionResult(
        dimension=adapter.dimension,
        status=AvailabilityStatus.UNAVAILABLE,
        reason=ReasonCode.WORLD_PROJECTION_ABSTAINED.value,
        provenance=outcome.provenance,
        stage=FallbackStage.STABLE_UNAVAILABLE,
        world_items=outcome.items,
        world_overrides=outcome.overrides,
    )


async def run_adapter(
    adapter: DimensionAdapter,
    *,
    source: SourceSnapshot,
    through_chapter: int,
    resolver: ReaderResolver | None = None,
    question: str | None = None,
) -> DimensionResult:
    """Execute one dimension's three-stage chain against a frozen snapshot."""
    if adapter.reader_id == READER_WORLD_PROJECTION:
        return await run_world_projection_adapter(
            adapter,
            source=source,
            through_chapter=through_chapter,
            resolver=resolver,
            question=question,
        )
    context = ReaderContext(
        owner_id=source.owner_id,
        novel_id=source.novel_id,
        version_id=source.version_id,
        through_chapter=through_chapter,
        snapshot_hash=source.snapshot_hash,
        dimension=adapter.dimension,
        question=question,
    )
    reader_outcome: ReaderOutcome | None = None
    if adapter.reader_id is not None and resolver is not None:
        try:
            reader = await resolver(adapter.reader_id)
        except Exception:
            reader = None
        if reader is not None:
            try:
                outcome = await reader(context)
            except ReaderUnavailableError:
                outcome = None
            except Exception:
                outcome = None
            if outcome is not None:
                reader_outcome = outcome

    heuristic_outcome: HeuristicOutcome | None = None
    if reader_outcome is None and adapter.has_heuristic:
        candidates = extract_heuristic_candidates(
            dimension=adapter.dimension,
            chapters=source.chapters,
            keywords=adapter.heuristic_keywords,
            snapshot_hash=source.snapshot_hash,
            through_chapter=through_chapter,
        )
        heuristic_outcome = HeuristicOutcome(candidates=candidates)

    return evaluate_dimension_chain(
        adapter.dimension,
        reader_outcome=reader_outcome,
        heuristic_outcome=heuristic_outcome,
    )


def _effective_through_chapter(plan: QueryPlan, source: SourceSnapshot) -> int:
    if plan.spoiler_cutoff.mode == CutoffMode.WHOLE_BOOK:
        return max((int(c.chapter_number) for c in source.chapters), default=10**9)
    return int(plan.spoiler_cutoff.through_chapter)


async def run_plan_adapters(
    plan: QueryPlan,
    *,
    source: SourceSnapshot,
    resolver: ReaderResolver | None = None,
    adapters: Mapping[QueryDimension, DimensionAdapter] | None = None,
    question: str | None = None,
) -> tuple[DimensionResult, ...]:
    """Run the chain for every dimension in plan order; never raises."""
    registry = DEFAULT_ADAPTERS if adapters is None else adapters
    through_chapter = _effective_through_chapter(plan, source)
    results: list[DimensionResult] = []
    for dimension in plan.dimensions:
        adapter = registry.get(dimension)
        if adapter is None:
            # Fail closed: an unregistered dimension reports unavailable.
            results.append(
                DimensionResult(
                    dimension=dimension,
                    status=AvailabilityStatus.UNAVAILABLE,
                    reason=ReasonCode.READER_UNAVAILABLE.value,
                    provenance=PROVENANCE_CONTRACT,
                    stage=FallbackStage.STABLE_UNAVAILABLE,
                )
            )
            continue
        result = await run_adapter(
            adapter,
            source=source,
            through_chapter=through_chapter,
            resolver=resolver,
            question=question,
        )
        results.append(result)
    return tuple(results)
