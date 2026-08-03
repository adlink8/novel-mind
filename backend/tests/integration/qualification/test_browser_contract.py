"""Phase 29-03 / REQ-QA-03 browser contract and leaf citation smoke.

Runs *before* the Playwright phase gate and fails closed without a browser:
it proves the service-side contracts the real Reader/Analysis Chat UAT depends
on (D-04/D-06):

- the 26-00 execution gate stays fail-closed while Phase 22 has <3/3 green
  records (the 2026-08-03 Phase 29 override is the only execution authority);
- request scope: selection/chapter_range anchors, spoiler cutoff narrowing and
  ``chapter_beyond_cutoff`` rejection;
- CandidateManifest snapshot/cutoff/owner/version/budget/lineage parity is
  checksum-stable and tamper-sensitive (29-02 shared contract);
- the Frozen Manifest delivers a leaf-only allowlist to the cited-answer
  gateway; non-leaf/summary/score keys can never be cited (D-08);
- partial/failure/spoiler/cancel/retry contracts are assertable without a
  browser, so Playwright is a real-browser gate and never a substitute.

Pure tests: no database, no provider transport. ``pytestmark = integration``
satisfies the classification gate without PostgreSQL fixtures.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.reader_chat import (
    MessageCreate,
    ReaderAnswerEnvelope,
    SelectionCoordinate,
    validate_answer_against_manifest,
)
from app.services.narrative_memory.contracts import (
    BudgetTotals,
    CandidateManifest,
    DimensionKind,
    DimensionResult,
    DimensionStatus,
    candidate_manifest_checksum,
    dimension_result_checksum,
)
from app.services.queryplan.adapters import (
    ChapterRecord,
    DimensionResult as QueryDimensionResult,
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.contracts import is_leaf_evidence_key, leaf_evidence_key
from app.services.queryplan.evidence import (
    EvidenceError,
    FrozenManifest,
    freeze_manifest,
    materialize_evidence_ref,
    verify_manifest,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
    QueryPlan,
)
from app.services.queryplan.service import ConsumerQueryPlanView, QueryPlanService
from app.services.reader_chat.budget import BudgetPolicy, DualBudgetGate
from app.services.reader_chat.context import (
    SelectionValidationError,
    assert_retry_uses_original_checksum,
    freeze_manifest_from_stored,
    narrow_chapter_range,
)
from app.services.reader_chat.gateway import (
    ModelDeployment,
    ReaderChatGateway,
    StructuredOutputRejected,
    business_validate_answer,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = REPO_ROOT / "backend"

HEX_SNAPSHOT = "c" * 64
HEX_OTHER = "d" * 64

CHAPTER_1_TEXT = "林安走进竹林，剑客随后现身。🀄 她低声问：你是谁？"
CHAPTER_2_TEXT = "第二章：剑客没有回答。林安握紧剑柄。"
CHAPTER_3_TEXT = "第三章：两人并肩走出竹林。"


def make_chapter(
    chapter_id: int, chapter_number: int, content: str
) -> ChapterRecord:
    return ChapterRecord(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        content_hash=chapter_content_hash(content),
    )


def make_source(*, snapshot_hash: str = HEX_SNAPSHOT) -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash=snapshot_hash,
        chapters=(
            make_chapter(1, 1, CHAPTER_1_TEXT),
            make_chapter(2, 2, CHAPTER_2_TEXT),
            make_chapter(3, 3, CHAPTER_3_TEXT),
        ),
    )


def make_plan(
    *,
    through_chapter: int = 3,
    whole_book: bool = False,
    full_book_authorized: bool = False,
) -> QueryPlan:
    result = parse_query_plan(
        {
            "intent": "reader",
            "owner_id": 1,
            "novel_id": 1,
            "version_id": 1,
            "question_text": "林安走进哪里？",
            "reading_progress": {
                "through_chapter": through_chapter,
                "snapshot_hash": HEX_SNAPSHOT,
                "full_book_authorized": full_book_authorized,
            },
            "whole_book": whole_book,
            "source": "reader_chat",
        }
    )
    assert isinstance(result, QueryPlan), result
    return result


def leaf_ref(
    *,
    chapter_id: int = 1,
    chapter_number: int = 1,
    content: str = CHAPTER_1_TEXT,
    start: int = 0,
    end: int | None = None,
    snapshot_hash: str = HEX_SNAPSHOT,
) -> EvidenceRef:
    if end is None:
        end = len(content[:10])
    excerpt = content[start:end]
    return EvidenceRef(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=chapter_content_hash(excerpt),
        source_snapshot_hash=snapshot_hash,
    )


def make_available(refs: tuple[EvidenceRef, ...]) -> QueryDimensionResult:
    return QueryDimensionResult(
        dimension=QueryDimension.RAW_TEXT,
        status=AvailabilityStatus.AVAILABLE,
        reason="reader_ok",
        provenance="exact_reader_v1",
        stage=FallbackStage.EXACT_READER,
        refs=refs,
    )


def make_unavailable(
    dimension: QueryDimension = QueryDimension.WORLD_RULES,
) -> QueryDimensionResult:
    return QueryDimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.UNAVAILABLE,
        reason="dimension_unavailable",
        provenance="deterministic_contract_v1",
        stage=FallbackStage.STABLE_UNAVAILABLE,
    )


def make_partial(
    dimension: QueryDimension = QueryDimension.CHARACTER_STATE,
) -> QueryDimensionResult:
    return QueryDimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.PARTIAL,
        reason="partial_reader_coverage",
        provenance="exact_reader_v1",
        stage=FallbackStage.EXACT_READER,
    )


# ---------------------------------------------------------------------------
# 1. Phase 26-00 execution gate stays fail-closed (Task 3 entry)
# ---------------------------------------------------------------------------


def _count_phase22_green_rows() -> int:
    ledger = (
        REPO_ROOT
        / ".planning"
        / "phases"
        / "22-ci-nightly-gap-closure"
        / "22-VALIDATION.md"
    )
    if not ledger.is_file():
        return 0
    text = ledger.read_text(encoding="utf-8")
    section = text.split("## Consecutive Scheduled Green Runs", 1)
    if len(section) < 2:
        return 0
    rows = 0
    for line in section[1].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or cells[0].startswith("#"):
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c != ""):
            continue
        if len(cells) >= 5 and cells[4].strip().lower() in {
            "passed",
            "pass",
            "green",
            "success",
            "ok",
        }:
            rows += 1
    return rows


def test_phase26_execution_gate_fails_closed_while_phase22_not_green() -> None:
    """The gate must exit non-zero until Phase 22 has 3/3 real green runs."""
    green = _count_phase22_green_rows()
    assert green < 3, (
        "Phase 22 unexpectedly reached 3/3 green; the fail-closed assertion "
        "must be revisited together with the execution override."
    )
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_phase_execution_gate.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "BLOCKED" in (result.stderr or "")
    # The override recorded in STATE.md is the only execution authority.
    state = (REPO_ROOT / ".planning" / "STATE.md").read_text(encoding="utf-8")
    assert "## Execution Override — Phase 29 (2026-08-03, user authorized)" in state


def test_execution_gate_script_is_read_only() -> None:
    """The gate CLI must not write planning/state/artifacts while it blocks."""
    src = (REPO_ROOT / "scripts" / "check_phase_execution_gate.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "write_text",
        "open(",
        "Path(...).write",
        "git ",
        "STATE.md",
    ):
        assert forbidden not in src, f"gate script must stay read-only: {forbidden}"


# ---------------------------------------------------------------------------
# 2. Request scope: anchors, cutoff narrowing, spoiler rejection (D-06)
# ---------------------------------------------------------------------------


def test_message_create_anchor_scope_is_mutually_exclusive() -> None:
    selection = SelectionCoordinate(
        chapter_id=1,
        source_start=0,
        source_end=4,
        selection_text=CHAPTER_1_TEXT[0:4],
        selection_text_hash=chapter_content_hash(CHAPTER_1_TEXT[0:4]),
        chapter_content_hash=chapter_content_hash(CHAPTER_1_TEXT),
    )
    # chapter_range is exclusive with selection/chapter_id.
    with pytest.raises(ValueError):
        MessageCreate(
            client_message_id="cm-1",
            body="q",
            chapter_range={"chapter_start": 1, "chapter_end": 2},
            selection=selection,
        )
    with pytest.raises(ValueError):
        MessageCreate(
            client_message_id="cm-1",
            body="q",
            chapter_range={"chapter_start": 1, "chapter_end": 2},
            chapter_id=1,
        )
    # chapter_id is required when selection and chapter_range are absent.
    with pytest.raises(ValueError):
        MessageCreate(client_message_id="cm-1", body="q")
    # selection.chapter_id must match chapter_id.
    with pytest.raises(ValueError):
        MessageCreate(
            client_message_id="cm-1",
            body="q",
            chapter_id=2,
            selection=selection,
        )
    # Valid single-chapter anchor.
    msg = MessageCreate(
        client_message_id="cm-1",
        body="q",
        chapter_id=1,
        selection=selection,
    )
    assert msg.selection is not None and msg.chapter_id == 1


def test_narrow_chapter_range_respects_cutoff_and_full_book() -> None:
    # End beyond cutoff is narrowed.
    assert narrow_chapter_range(1, 9, cutoff_chapter_number=3, full_book=False) == 3
    # Fully visible range is untouched.
    assert narrow_chapter_range(1, 3, cutoff_chapter_number=3, full_book=False) == 3
    # Start beyond cutoff is a stable 422 reason.
    with pytest.raises(SelectionValidationError) as exc:
        narrow_chapter_range(4, 9, cutoff_chapter_number=3, full_book=False)
    assert exc.value.code == "chapter_beyond_cutoff"
    # Explicit whole-book switch skips truncation entirely.
    assert narrow_chapter_range(1, 9, cutoff_chapter_number=3, full_book=True) == 9
    assert narrow_chapter_range(4, 9, cutoff_chapter_number=3, full_book=True) == 9
    # Invalid range fails closed.
    with pytest.raises(SelectionValidationError) as exc:
        narrow_chapter_range(0, 5, cutoff_chapter_number=3, full_book=False)
    assert exc.value.code == "invalid_chapter_range"


# ---------------------------------------------------------------------------
# 3. CandidateManifest parity: snapshot/cutoff/owner/version/budget/lineage
# ---------------------------------------------------------------------------


def _budget(**overrides: object) -> BudgetTotals:
    base = dict(
        calls=10,
        input_tokens=2_000,
        output_tokens=1_000,
        cost_usd="0.5",
        cache_hits=1,
    )
    base.update(overrides)
    return BudgetTotals(**base)


LINEAGE = {
    "hierarchy_build_id": "b" * 64,
    "commit": "912ca6b423d6c2309bc2972cbfc083c4eaa280e1",
}


def _dimension(
    kind: DimensionKind,
    status: DimensionStatus,
    *,
    progress: float = 1.0,
    blocked_reason: str | None = None,
    **overrides: object,
) -> DimensionResult:
    kwargs = dict(
        source_snapshot_hash=str(
            overrides.pop("source_snapshot_hash", overrides.pop("snapshot", "a" * 64))
        ),
        cutoff=int(overrides.pop("cutoff", 6)),
        owner_id=int(overrides.pop("owner_id", 1)),
        version_id=int(overrides.pop("version_id", 1)),
        version_key=str(overrides.pop("version_key", "v1")),
        budget=overrides.pop("budget", _budget()),
        lineage=dict(overrides.pop("lineage", LINEAGE)),
    )
    placeholder = DimensionResult(
        dimension=kind,
        status=status,
        progress=progress,
        blocked_reason=blocked_reason,
        checksum="0" * 64,
        **kwargs,
    )
    return placeholder.model_copy(
        update={"checksum": dimension_result_checksum(placeholder)}
    )


def _manifest(*dimensions: DimensionResult, **overrides: object) -> CandidateManifest:
    kwargs = dict(
        source_snapshot_hash=str(
            overrides.pop("source_snapshot_hash", overrides.pop("snapshot", "a" * 64))
        ),
        cutoff=int(overrides.pop("cutoff", 6)),
        owner_id=int(overrides.pop("owner_id", 1)),
        version_id=int(overrides.pop("version_id", 1)),
        version_key=str(overrides.pop("version_key", "v1")),
        budget=overrides.pop("budget", _budget()),
        lineage=dict(overrides.pop("lineage", LINEAGE)),
    )
    placeholder = CandidateManifest(
        dimensions=tuple(dimensions),
        checksum="0" * 64,
        **kwargs,
    )
    return placeholder.model_copy(
        update={"checksum": candidate_manifest_checksum(placeholder)}
    )


def _consistent_dimensions() -> tuple[DimensionResult, ...]:
    return (
        _dimension(DimensionKind.TIMELINE, DimensionStatus.AVAILABLE),
        _dimension(
            DimensionKind.CLUE,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason="clue_unavailable",
        ),
        _dimension(DimensionKind.CHARACTER, DimensionStatus.PARTIAL, progress=0.5),
    )


@pytest.mark.parametrize(
    "field,tamper",
    [
        ("source_snapshot_hash", "f" * 64),
        ("cutoff", 9),
        ("owner_id", 99),
        ("version_id", 99),
        ("version_key", "other-version"),
        ("budget", _budget(calls=99)),
        ("lineage", {"hierarchy_build_id": "e" * 64, "commit": "other"}),
    ],
)
def test_candidate_manifest_parity_tamper_changes_checksum(
    field: str, tamper: object
) -> None:
    dims = _consistent_dimensions()
    manifest = _manifest(*dims)
    baseline = _manifest(*dims, **{field: tamper})
    assert manifest.checksum != baseline.checksum
    # Parity consumers (29-02 runner) reject a mismatch and stop aggregation.
    assert candidate_manifest_checksum(manifest) == manifest.checksum


def test_candidate_manifest_checksum_is_deterministic_and_dimension_sensitive() -> None:
    dims = _consistent_dimensions()
    first = _manifest(*dims)
    second = _manifest(*dims)
    assert first.checksum == second.checksum == candidate_manifest_checksum(first)
    # A blocked dimension with a different blocked_reason changes the checksum.
    dims2 = (
        _dimension(DimensionKind.TIMELINE, DimensionStatus.AVAILABLE),
        _dimension(
            DimensionKind.CLUE,
            DimensionStatus.BLOCKED,
            progress=0.0,
            blocked_reason="other_reason",
        ),
        _dimension(DimensionKind.CHARACTER, DimensionStatus.PARTIAL, progress=0.5),
    )
    assert _manifest(*dims2).checksum != first.checksum


# ---------------------------------------------------------------------------
# 4. Frozen Manifest → cited-answer gateway: leaf-only allowlist (D-08)
# ---------------------------------------------------------------------------


def _freeze_leaf_manifest() -> FrozenManifest:
    source = make_source()
    ref = leaf_ref()
    return freeze_manifest(
        plan=make_plan(),
        source=source,
        evidence=(materialize_evidence_ref(ref, source=source, through_chapter=3),),
        omitted=(),
    )


def test_frozen_manifest_allowlist_is_leaf_only() -> None:
    manifest = _freeze_leaf_manifest()
    verify_manifest(manifest)
    assert manifest.allowed_evidence_ids()
    for key in manifest.allowed_evidence_ids():
        assert is_leaf_evidence_key(key), f"non-leaf allowlist key: {key!r}"
    # leaf_evidence_key only composes leaf fields; a summary/score key never matches.
    assert is_leaf_evidence_key(
        leaf_evidence_key(
            chapter_id=1, source_start=0, source_end=4, content_hash="a" * 64
        )
    )
    assert not is_leaf_evidence_key("summary:arc:vol1")
    assert not is_leaf_evidence_key("score:0.95")
    assert not is_leaf_evidence_key("chat:text:123")


def test_manifest_mutation_fails_closed() -> None:
    manifest = _freeze_leaf_manifest()
    entry = manifest.evidence[0]
    mutated_entry = replace(entry, source_start=1, source_end=2)
    tampered = replace(manifest, evidence=(mutated_entry,))
    with pytest.raises(EvidenceError) as exc:
        verify_manifest(tampered)
    assert exc.value.code == "manifest_mutated"


def test_materialize_evidence_ref_fails_closed_on_stale_lineage() -> None:
    source = make_source()
    ref = leaf_ref(snapshot_hash=HEX_OTHER)
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(ref, source=source, through_chapter=3)
    assert exc.value.code == "stale_snapshot_lineage"


def test_materialize_evidence_ref_fails_closed_on_spoiler() -> None:
    source = make_source()
    ref = leaf_ref(chapter_id=3, chapter_number=3, content=CHAPTER_3_TEXT)
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(ref, source=source, through_chapter=2)
    assert exc.value.code == "beyond_cutoff"


def test_materialize_evidence_ref_fails_closed_on_bad_offsets_or_hash() -> None:
    source = make_source()
    bad_offsets = leaf_ref(start=0, end=999)
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(bad_offsets, source=source, through_chapter=3)
    assert exc.value.code == "invalid_offsets"

    stale = leaf_ref()
    stale = stale.model_copy(update={"content_hash": "f" * 64})
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(stale, source=source, through_chapter=3)
    assert exc.value.code in {"stale_content_hash", "invalid_offsets"}


def test_cited_answer_gateway_rejects_non_leaf_citation() -> None:
    manifest = _freeze_leaf_manifest()
    allowlist = manifest.allowed_evidence_ids()
    allowed = next(iter(allowlist))

    ok = ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {"block_id": "b1", "text": "林安走进竹林。", "evidence_refs": [allowed]}
            ],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        }
    )
    validate_answer_against_manifest(ok, allowlist)
    business_validate_answer(ok, allowed_evidence_ids=allowlist)

    # A citation to a summary/score/chat-text key must fail closed.
    for bad_key in ("summary:arc:vol1", "score:0.95", "chat:text:123", "future:ch99"):
        bad = ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [
                    {"block_id": "b1", "text": "泄漏", "evidence_refs": [bad_key]}
                ],
                "clarifying_question": None,
                "uncertainty": None,
                "suggestion_candidates": [],
            }
        )
        with pytest.raises(ValueError):
            validate_answer_against_manifest(bad, allowlist)


def test_no_evidence_forbids_factual_answer_blocks() -> None:
    env = ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "无证据却作答",
                    "evidence_refs": ["selection:primary"],
                }
            ],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        }
    )
    # With an empty allowlist every factual block is rejected (uncited/hallucination).
    with pytest.raises(ValueError):
        business_validate_answer(env, allowed_evidence_ids=set())
    # An evidence-less envelope must abstain instead of asserting facts.
    abstain = ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [],
            "clarifying_question": "证据不足，无法作答。",
            "uncertainty": None,
            "suggestion_candidates": [],
        }
    )
    business_validate_answer(abstain, allowed_evidence_ids=set())


# ---------------------------------------------------------------------------
# 5. Partial availability and abstention surfaces (D-05/D-09)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_view_exposes_partial_and_abstained_with_leaf_jump_only() -> None:
    source = make_source()
    ref = leaf_ref()
    dimensions = (
        make_available((ref,)),
        make_partial(),
        make_unavailable(),
    )
    payload = QueryPlanService.build_consumer_request(
        intent="reader",
        owner_id=1,
        novel_id=1,
        version_id=1,
        question_text="林安走进哪里？",
        through_chapter=3,
        snapshot_hash=HEX_SNAPSHOT,
        selection={
            "kind": "selection",
            "chapter_id": 1,
            "source_start": 0,
            "source_end": 4,
            "chapter_content_hash": "b" * 64,
        },
    )
    service = QueryPlanService()
    result, view = await service.execute_consumer_manifest(
        payload, source=source, dimension_results=dimensions
    )
    assert isinstance(view, ConsumerQueryPlanView)
    # Partial/unavailable statuses are carried by the fused dimension results —
    # never hidden behind a single availability state.
    fused_statuses = {r.status.value for r in result.fused.dimension_results}
    assert "partial" in fused_statuses and "unavailable" in fused_statuses
    assert view.availability  # plan-level availability is always exposed
    assert not view.abstained
    assert view.citation_jump
    # Every citation jump target is a leaf/raw chapter with exact offsets.
    for jump in view.citation_jump:
        assert jump.chapter_id == 1
        assert jump.source_end > jump.source_start
        assert jump.excerpt == CHAPTER_1_TEXT[jump.source_start : jump.source_end]
        assert is_leaf_evidence_key(jump.evidence_key)
    # The manifest allowlist is exactly the materialized leaf refs.
    assert set(view.allowed_evidence_ids) == result.manifest.allowed_evidence_ids()


@pytest.mark.asyncio
async def test_consumer_view_abstains_when_no_evidence() -> None:
    source = make_source()
    dimensions = (
        make_unavailable(QueryDimension.RAW_TEXT),
        make_unavailable(QueryDimension.WORLD_RULES),
    )
    payload = QueryPlanService.build_consumer_request(
        intent="analysis",
        owner_id=1,
        novel_id=1,
        version_id=1,
        question_text="后章发生了什么事？",
        through_chapter=3,
        snapshot_hash=HEX_SNAPSHOT,
        chapter_range={"kind": "chapter_range", "chapter_start": 1, "chapter_end": 2},
        source="analysis_chat",
    )
    service = QueryPlanService()
    result, view = await service.execute_consumer_manifest(
        payload, source=source, dimension_results=dimensions
    )
    assert view.abstained is True
    assert len(view.citation_jump) == 0
    assert result.manifest.allowed_evidence_ids() == set()


# ---------------------------------------------------------------------------
# 6. Failure / cancel / retry contracts
# ---------------------------------------------------------------------------


class _FakeTransport:
    """Deterministic two-failure transport; no provider, no network."""

    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, **kwargs):
        return self.responses.pop(0)


def _deployment() -> ModelDeployment:
    return ModelDeployment(
        provider="test",
        model_id="reader-balanced",
        revision="r1",
        supports_structured_output=True,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
    )


def _budget_gate(calls: int = 3) -> DualBudgetGate:
    return DualBudgetGate(
        conversation_policy=BudgetPolicy(calls, 10_000, 2_000, Decimal("1")),
        novel_policy=BudgetPolicy(calls, 10_000, 2_000, Decimal("1")),
    )


@pytest.mark.asyncio
async def test_gateway_failure_after_two_attempts_is_visible() -> None:
    transport = _FakeTransport(
        [{"content": "{}", "usage": {}}, {"content": "{}", "usage": {}}]
    )
    with pytest.raises(StructuredOutputRejected) as exc:
        await ReaderChatGateway(transport).generate(
            deployment=_deployment(),
            messages=[],
            allowed_evidence_ids={"selection:primary"},
            budget=_budget_gate(),
            job_id=1,
            max_input_tokens=100,
            max_output_tokens=50,
        )
    assert len(exc.value.attempts) == 2
    assert all(a.status == "failed" for a in exc.value.attempts)
    assert all(a.error_code for a in exc.value.attempts)


def test_retry_uses_original_frozen_manifest_checksum() -> None:
    """Retry must reuse the frozen manifest; rebuilding under new progress is forbidden."""
    from app.services.reader_chat.context import (
        ContextEvidenceEntry,
        ContextManifest,
        canonical_manifest_checksum,
    )

    entry = ContextEvidenceEntry(
        evidence_key="selection:primary",
        source_type="selection",
        source_id="1:0:1",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=1,
        content_hash=HEX_OTHER,
        excerpt="x",
        sort_order=0,
        version_lineage={
            "hierarchy_build_id": "b",
            "hierarchy_checksum": HEX_SNAPSHOT,
            "chapter_content_hash": HEX_SNAPSHOT,
        },
    )
    draft = ContextManifest(
        reading_progress_snapshot={"chapter_id": 1, "full_book": False},
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX_SNAPSHOT,
        evidence=(entry,),
        omitted_evidence_counts={},
        prompt_inputs={"dialogue_framing": {"is_evidence": False}},
        source_status={"selection": "ok"},
        manifest_checksum="",
    )
    good = canonical_manifest_checksum(draft.canonical_payload())
    frozen = freeze_manifest_from_stored(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX_SNAPSHOT,
        evidence=[entry.canonical_dict()],
        omitted_evidence_counts={},
        prompt_inputs=draft.prompt_inputs,
        source_status=draft.source_status,
        expected_checksum=good,
    )
    assert frozen.manifest_checksum == good
    # A rebuilt manifest under new progress (checksum recomputed) must never
    # replace the stored one: the helper returns when the rebuilt manifest
    # diverges, and fails loudly if it ever matches by chance.
    rebuilt_inputs = {"dialogue_framing": {"is_evidence": True}}
    rebuilt_draft = ContextManifest(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX_SNAPSHOT,
        evidence=(entry,),
        omitted_evidence_counts={},
        prompt_inputs=rebuilt_inputs,
        source_status=draft.source_status,
        manifest_checksum="",
    )
    rebuilt_checksum = canonical_manifest_checksum(rebuilt_draft.canonical_payload())
    rebuilt = freeze_manifest_from_stored(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX_SNAPSHOT,
        evidence=[entry.canonical_dict()],
        omitted_evidence_counts={},
        prompt_inputs=rebuilt_inputs,
        source_status=draft.source_status,
        expected_checksum=rebuilt_checksum,
    )
    assert rebuilt.manifest_checksum != frozen.manifest_checksum
    assert_retry_uses_original_checksum(frozen.manifest_checksum, rebuilt)
    with pytest.raises(AssertionError):
        assert_retry_uses_original_checksum(frozen.manifest_checksum, frozen)
    # Tampered stored manifest fails closed on rehydrate.
    with pytest.raises(SelectionValidationError) as exc:
        freeze_manifest_from_stored(
            reading_progress_snapshot=draft.reading_progress_snapshot,
            full_book=False,
            cutoff_chapter_number=1,
            analysis_version_id=None,
            hierarchy_build_id="b",
            hierarchy_checksum=HEX_SNAPSHOT,
            evidence=[entry.canonical_dict()],
            omitted_evidence_counts={},
            prompt_inputs=draft.prompt_inputs,
            source_status=draft.source_status,
            expected_checksum=HEX_OTHER,
        )
    assert exc.value.code == "manifest_checksum_mismatch"


def test_conversations_retry_contract_reuses_original_manifest() -> None:
    """Static contract scan: retry resumes the *original* frozen manifest by checksum."""
    src = (BACKEND_ROOT / "app" / "services" / "reader_chat" / "conversations.py").read_text(
        encoding="utf-8"
    )
    assert "original manifest" in src.lower() or "context_manifest_checksum" in src
    assert "manifest checksum mismatch" in src
    assert "retry_count" in src
    # Cancel path sets cancel_requested on the durable job.
    assert "cancel_requested = True" in src


def test_reader_chat_api_exposes_cancel_and_retry_job_routes() -> None:
    """Static contract scan: the API router must expose cancel/retry POST endpoints."""
    src = (BACKEND_ROOT / "app" / "api" / "reader_chat.py").read_text(encoding="utf-8")
    assert "/jobs/{job_id}/cancel" in src
    assert "/jobs/{job_id}/retry" in src
    assert "@router.post" in src
    assert "status_code=status.HTTP_202_ACCEPTED" in src  # durable message accept


def test_job_status_vocabulary_covers_partial_failure_and_cancel() -> None:
    from app.schemas.reader_chat import GenerationJobStatus

    for expected in (
        "queued",
        "running",
        "paused_budget",
        "paused_dependency",
        "cancelled",
        "completed",
        "failed",
        "failed_validation",
    ):
        assert expected in {s.value for s in GenerationJobStatus}
    # Frontend maps the same vocabulary (shared contract).
    frontend = (REPO_ROOT / "frontend" / "src" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    assert "failed_validation" in frontend and "paused_dependency" in frontend


def test_leaf_citation_smoke_is_deterministic_across_runs() -> None:
    """The frozen leaf allowlist is reproducible and content-addressed."""
    # The plan is created once: its trace embeds created_at, so determinism means
    # the same plan + snapshot always freezes to the same content address.
    plan = make_plan()
    source = make_source()
    ref = leaf_ref()
    frozen = freeze_manifest(
        plan=plan,
        source=source,
        evidence=(materialize_evidence_ref(ref, source=source, through_chapter=3),),
        omitted=(),
    )
    again = freeze_manifest(
        plan=plan,
        source=source,
        evidence=(materialize_evidence_ref(ref, source=source, through_chapter=3),),
        omitted=(),
    )
    assert frozen.manifest_checksum == again.manifest_checksum == frozen.manifest_id
    assert frozen.allowed_evidence_ids() == again.allowed_evidence_ids()
    assert frozen.canonical_payload() == again.canonical_payload()


def test_e2e_mock_contracts_parse_with_real_server_shapes() -> None:
    """The mocked e2e citations must match the real CitationView shape (D-06)."""
    required_keys = {
        "block_id",
        "evidence_key",
        "context_evidence_ref_id",
        "chapter_id",
        "source_start",
        "source_end",
    }
    for spec_path in (
        REPO_ROOT / "frontend" / "e2e" / "reader-chat-quality.spec.ts",
        REPO_ROOT / "frontend" / "e2e" / "analysis-chat-quality.spec.ts",
    ):
        assert spec_path.is_file(), f"missing browser UAT spec: {spec_path}"
        src = spec_path.read_text(encoding="utf-8")
        assert "reader-chat-citation" in src
        assert "SECRET_FUTURE" in src  # spoiler-safe path is asserted in-browser
        # The spec references the shared citation test ids used by the UI.
        assert "reader-citation-highlight" in src or "reader-chat-citation" in src
        # Sanity: the citation fixtures carry every server field the UI renders.
        for key in required_keys:
            assert key in src, f"{spec_path.name} citation fixture missing {key}"
