"""Adversarial fail-closed gates for queryplan evidence (D-07/D-08/D-09/D-14).

Every non-leaf citation — summary, score, routing metadata or chat text — is
rejected by the local cited-answer gate before a ``QueryPlanAnswer`` artifact is
created. Legal leaf citations pass, and an evidence-less answer must abstain
(D-09). The provable import chain
``queryplan.service -> reader_chat.gateway::business_validate_answer ->
reader_chat.schemas::validate_answer_against_manifest`` is asserted, and the
queryplan package imports no domain-mutation service (D-14).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.schemas.reader_chat import ReaderAnswerEnvelope
from app.services.queryplan.adapters import (
    ChapterRecord,
    DimensionResult,
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.evidence import (
    FrozenManifest,
    freeze_manifest,
    materialize_evidence_ref,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
    QueryPlan,
)
from app.services.queryplan.service import QueryPlanAnswer, QueryPlanService

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
QUERYPLAN_DIR = ROOT / "app" / "services" / "queryplan"
GATEWAY_FILE = ROOT / "app" / "services" / "reader_chat" / "gateway.py"
SCHEMA_FILE = ROOT / "app" / "schemas" / "reader_chat.py"

FORBIDDEN_DOMAIN_MODULES = (
    "app.services.timeline.promotion",
    "app.services.timeline.overrides",
    "app.services.relationships.worker",
    "app.services.relationships.candidates",
    "app.models.timeline",
    "app.models.relationship",
)

HEX_SNAPSHOT = "c" * 64
CHAPTER_1_TEXT = "林安走进竹林，剑客随后现身。🀄 她低声问：你是谁？"


def make_chapter() -> ChapterRecord:
    return ChapterRecord(
        chapter_id=1,
        chapter_number=1,
        content=CHAPTER_1_TEXT,
        content_hash=chapter_content_hash(CHAPTER_1_TEXT),
    )


def make_source() -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash=HEX_SNAPSHOT,
        chapters=(make_chapter(),),
    )


def make_plan() -> QueryPlan:
    result = parse_query_plan(
        {
            "intent": "reader",
            "owner_id": 1,
            "novel_id": 1,
            "version_id": 1,
            "question_text": "林安走进哪里？",
            "reading_progress": {
                "through_chapter": 1,
                "snapshot_hash": HEX_SNAPSHOT,
                "full_book_authorized": False,
            },
            "source": "reader_chat",
        }
    )
    assert isinstance(result, QueryPlan), result
    return result


def leaf_ref() -> EvidenceRef:
    excerpt = CHAPTER_1_TEXT[:10]
    return EvidenceRef(
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=10,
        content_hash=chapter_content_hash(excerpt),
        source_snapshot_hash=HEX_SNAPSHOT,
    )


def make_available(refs: tuple[EvidenceRef, ...] = (leaf_ref(),)) -> DimensionResult:
    return DimensionResult(
        dimension=QueryDimension.RAW_TEXT,
        status=AvailabilityStatus.AVAILABLE,
        reason="reader_ok",
        provenance="exact_reader_v1",
        stage=FallbackStage.EXACT_READER,
        refs=refs,
    )


def make_unavailable() -> DimensionResult:
    return DimensionResult(
        dimension=QueryDimension.WORLD_RULES,
        status=AvailabilityStatus.UNAVAILABLE,
        reason="dimension_unavailable",
        provenance="deterministic_contract_v1",
        stage=FallbackStage.STABLE_UNAVAILABLE,
    )


def make_manifest(*, with_evidence: bool = True) -> FrozenManifest:
    plan = make_plan()
    source = make_source()
    evidence = (
        (materialize_evidence_ref(leaf_ref(), source=source, through_chapter=1),)
        if with_evidence
        else ()
    )
    return freeze_manifest(plan=plan, source=source, evidence=evidence, omitted=())


def envelope_with_refs(
    refs: list[str], *, text: str = "林安走进竹林。"
) -> ReaderAnswerEnvelope:
    return ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [{"block_id": "b1", "text": text, "evidence_refs": refs}],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        }
    )


# ---------------------------------------------------------------------------
# Leaf-only citation gate (D-08)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "summary:chapter1",
        "score:0.97",
        "routing:novel:1:dimension:raw_text",
        "chat:user-message-42",
        "selection:primary",
        "qp:1:0:10:" + "f" * 64,
    ],
)
def test_non_leaf_or_forged_refs_rejected_before_answer_creation(ref: str):
    manifest = make_manifest()
    service = QueryPlanService()
    with pytest.raises(ValueError):
        service.gate_answer(manifest, envelope_with_refs([ref]))


def test_legal_leaf_citation_passes_gate():
    manifest = make_manifest()
    key = sorted(manifest.allowed_evidence_ids())[0]
    service = QueryPlanService()
    service.gate_answer(manifest, envelope_with_refs([key]))


async def test_execute_with_non_leaf_ref_creates_no_answer_artifact():
    called: list[FrozenManifest] = []

    async def bad_producer(manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        called.append(manifest)
        return envelope_with_refs(["summary:chapter1"])

    service = QueryPlanService()
    with pytest.raises(ValueError):
        await service.execute(
            make_plan(),
            make_source(),
            dimension_results=[make_available()],
            answer_producer=bad_producer,
        )
    # The producer was reached, but no QueryPlanAnswer artifact was created.
    assert len(called) == 1


async def test_execute_with_legal_leaf_producer_returns_artifact():
    async def good_producer(manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        key = sorted(manifest.allowed_evidence_ids())[0]
        return envelope_with_refs([key])

    answer = await QueryPlanService().execute(
        make_plan(),
        make_source(),
        dimension_results=[make_available()],
        answer_producer=good_producer,
    )
    assert isinstance(answer, QueryPlanAnswer)
    assert answer.abstained is False


async def test_tampered_manifest_fails_closed_at_the_gate():
    manifest = make_manifest()
    key = sorted(manifest.allowed_evidence_ids())[0]
    import dataclasses

    tampered = dataclasses.replace(manifest, owner_id=999)
    service = QueryPlanService()
    with pytest.raises(ValueError):
        service.gate_answer(tampered, envelope_with_refs([key]))


# ---------------------------------------------------------------------------
# Abstention (D-09)
# ---------------------------------------------------------------------------


async def test_evidence_less_answer_must_abstain():
    async def abstain_producer(_manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        return ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [],
                "clarifying_question": "证据不足，无法作答。",
                "uncertainty": None,
                "suggestion_candidates": [],
            }
        )

    answer = await QueryPlanService().execute(
        make_plan(),
        make_source(),
        dimension_results=[make_unavailable()],
        answer_producer=abstain_producer,
    )
    assert answer.abstained is True
    assert answer.envelope.answer_blocks == []
    assert answer.manifest.allowed_evidence_ids() == set()


async def test_evidence_less_factual_block_is_forbidden():
    async def factual_producer(_manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        return envelope_with_refs(["summary:chapter1"])

    with pytest.raises(ValueError):
        await QueryPlanService().execute(
            make_plan(),
            make_source(),
            dimension_results=[make_unavailable()],
            answer_producer=factual_producer,
        )


def test_heuristic_candidate_can_never_be_cited():
    """Candidate-only recall never enters the manifest; citing it fails (D-15)."""
    manifest = make_manifest(with_evidence=False)
    assert manifest.allowed_evidence_ids() == set()
    service = QueryPlanService()
    with pytest.raises(ValueError):
        service.gate_answer(manifest, envelope_with_refs(["qp:1:0:8:" + "0" * 64]))


# ---------------------------------------------------------------------------
# Provable call chain and D-14 boundaries
# ---------------------------------------------------------------------------


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_service_imports_cited_answer_gateway_and_schema_allowlist():
    """queryplan.service -> reader_chat.gateway::business_validate_answer ->
    reader_chat.schemas::validate_answer_against_manifest (provable)."""
    service_src = (QUERYPLAN_DIR / "service.py").read_text(encoding="utf-8")
    assert "business_validate_answer" in service_src
    imports = _module_imports(QUERYPLAN_DIR / "service.py")
    assert "app.services.reader_chat.gateway" in imports

    gateway_src = GATEWAY_FILE.read_text(encoding="utf-8")
    assert "validate_answer_against_manifest" in gateway_src
    assert "from app.schemas.reader_chat import" in gateway_src

    schema_src = SCHEMA_FILE.read_text(encoding="utf-8")
    assert "def validate_answer_against_manifest" in schema_src


def test_queryplan_evidence_package_imports_no_domain_mutation_services():
    """D-14: no promotion / active-pointer / consumer cutover reachable."""
    for path in QUERYPLAN_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        imports = _module_imports(path)
        for forbidden in FORBIDDEN_DOMAIN_MODULES:
            assert forbidden not in imports, (
                f"{path.name} imports domain mutation module {forbidden}"
            )
            assert not any(
                mod == forbidden or mod.startswith(forbidden + ".") for mod in imports
            )


def test_manifest_evidence_carries_only_leaf_fields():
    """No summary / score / routing / chat field can exist on manifest evidence."""
    manifest = make_manifest()
    payload = manifest.canonical_payload()
    for entry in payload["evidence"]:
        assert "summary" not in entry
        assert "score" not in entry
        assert "routing" not in entry
        assert "chat" not in entry
        assert set(entry) == {
            "evidence_key",
            "chapter_id",
            "chapter_number",
            "source_start",
            "source_end",
            "content_hash",
            "source_snapshot_hash",
            "excerpt",
        }
