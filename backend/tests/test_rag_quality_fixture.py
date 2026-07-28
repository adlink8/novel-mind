"""Frozen fixture pipeline: hash/offset/equivalence/lineage/regeneration (06-03)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas.eval import EvalCase, JudgeFixtureVerdict, ModelLineage
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    MAX_REGENERATE,
    InvalidLineageError,
    build_source_snapshot,
    compute_fixture_hash,
    freeze_eval_case,
    load_json,
    make_evidence_ref,
    package_benchmark_suite,
    prompt_file_hash,
    prompts_dir,
    resolve_lineage,
    run_deterministic_checks,
    run_fixture_pipeline,
    schema_contract_hash,
    validate_generator_judge_isolation,
    verify_evidence_ref,
    verify_frozen_case,
    verify_source_snapshot,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

SECRET = DEFAULT_SIGNING_SECRET
CREATED = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
EVALS = Path(__file__).resolve().parents[1] / "evals"


def _lineages() -> tuple[ModelLineage, ModelLineage]:
    sch = schema_contract_hash()
    g = resolve_lineage(
        provider="ollama",
        model_family="qwen",
        model_id="qwen3.5:9b",
        weights_revision="qwen-rev-1",
        prompt_hash=prompt_file_hash(prompts_dir() / "rag_fixture_generator.v1.txt"),
        prompt_version="rag_fixture_generator.v1",
        schema_hash=sch,
        started_at=CREATED,
    )
    j = resolve_lineage(
        provider="ollama",
        model_family="gemma",
        model_id="gemma4-local",
        weights_revision="gemma-rev-2",
        prompt_hash=prompt_file_hash(prompts_dir() / "rag_fixture_judge.v1.txt"),
        prompt_version="rag_fixture_judge.v1",
        schema_hash=sch,
        started_at=CREATED,
    )
    return g, j


def _snap(owner_id: int = 1, work_id: int = 10):
    texts = [
        "路明非站在卡塞尔学院的门前，第一次看见青铜与火之王的传说写在石碑上。",
        "恺撒·加图索拔出狄克推多，寒光映着雨夜的湖面。",
    ]
    return build_source_snapshot(
        owner_id=owner_id,
        work_id=work_id,
        texts=texts,
        version="t-v1",
        secret=SECRET,
        created_at=CREATED,
    ), texts


def test_snapshot_signature_and_manifest_repeatable():
    s1, _ = _snap()
    s2, _ = _snap()
    assert s1.manifest_hash == s2.manifest_hash
    assert verify_source_snapshot(s1, SECRET)
    assert verify_source_snapshot(s2, SECRET)
    # Content hash is truth — not DB autoincrement
    assert all(len(c.content_hash) == 64 for c in s1.chunks)


def test_evidence_offset_quote_roundtrip():
    snap, texts = _snap()
    quote = "第一次看见青铜与火之王的传说"
    start = texts[0].index(quote)
    end = start + len(quote)
    ref = make_evidence_ref(snap, snap.chunks[0].content_hash, start, end)
    assert ref.quote_text == quote
    assert verify_evidence_ref(ref, snap)
    bad = ref.model_copy(update={"quote_hash": "0" * 64})
    assert not verify_evidence_ref(bad, snap)
    bad_off = ref.model_copy(update={"end_offset": ref.end_offset + 3})
    assert not verify_evidence_ref(bad_off, snap)


def test_generator_judge_isolation_requires_family_and_weights():
    g, j = _lineages()
    validate_generator_judge_isolation(g, j)  # ok

    same_family = j.model_copy(update={"model_family": g.model_family})
    with pytest.raises(InvalidLineageError):
        validate_generator_judge_isolation(g, same_family)

    same_weights = j.model_copy(update={"weights_revision": g.weights_revision})
    with pytest.raises(InvalidLineageError):
        validate_generator_judge_isolation(g, same_weights)

    # provider-only difference is not enough if family or weights match
    only_provider = g.model_copy(update={"provider": "other-cloud"})
    with pytest.raises(InvalidLineageError):
        validate_generator_judge_isolation(g, only_provider)


def test_freeze_pipeline_answerable_no_answer_hard_negative():
    snap, texts = _snap()
    g, j = _lineages()
    quote = "第一次看见青铜与火之王的传说"
    start = texts[0].index(quote)
    end = start + len(quote)
    ev = {"content_hash": snap.chunks[0].content_hash, "start": start, "end": end}

    job, case = run_fixture_pipeline(
        snapshot=snap,
        owner_id=1,
        work_id=10,
        case_spec={
            "case_id": "fx-ans",
            "case_type": "answerable",
            "question": "路明非第一次在哪里看见传说？",
            "evidence": [ev],
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "在卡塞尔学院门前石碑上",
                    "critical": True,
                    "evidence_set_ids": ["s1"],
                }
            ],
            "reference_answer": "在卡塞尔学院门前的石碑上",
        },
        generator_lineage=g,
        judge_lineage=j,
        secret=SECRET,
    )
    assert job.status == "frozen"
    assert case is not None and case.status == "frozen"
    assert case.fixture_hash and case.signature
    assert verify_frozen_case(case, SECRET)
    assert job.metrics is None
    assert job.quality_comparable is False
    # signature repeatable
    again = freeze_eval_case(case, SECRET)
    assert again.fixture_hash == case.fixture_hash
    assert again.signature == case.signature

    job2, case2 = run_fixture_pipeline(
        snapshot=snap,
        owner_id=1,
        work_id=10,
        case_spec={
            "case_id": "fx-na",
            "case_type": "no_answer",
            "question": "银行余额？",
            "reference_answer": "insufficient evidence",
        },
        generator_lineage=g,
        judge_lineage=j,
        secret=SECRET,
    )
    assert job2.status == "frozen" and case2 is not None

    # hard negative near-miss from second chunk
    q2 = "雨夜的湖面"
    s2 = texts[1].index(q2)
    e2 = s2 + len(q2)
    job3, case3 = run_fixture_pipeline(
        snapshot=snap,
        owner_id=1,
        work_id=10,
        case_spec={
            "case_id": "fx-hn",
            "case_type": "hard_negative",
            "question": "无关近邻证据？",
            "evidence": [
                {"content_hash": snap.chunks[1].content_hash, "start": s2, "end": e2}
            ],
            "false_claim": "湖面描述支持传说",
        },
        generator_lineage=g,
        judge_lineage=j,
        secret=SECRET,
    )
    assert job3.status == "frozen" and case3 is not None


def test_invalid_lineage_fail_closed_same_family():
    snap, _ = _snap()
    g, j = _lineages()
    j_bad = j.model_copy(update={"model_family": g.model_family})
    job, case = run_fixture_pipeline(
        snapshot=snap,
        owner_id=1,
        work_id=10,
        case_spec={
            "case_id": "fx-bad-lineage",
            "case_type": "no_answer",
            "question": "x?",
            "reference_answer": "insufficient evidence",
        },
        generator_lineage=g,
        judge_lineage=j_bad,
        secret=SECRET,
    )
    assert job.status == "invalid_lineage"
    assert job.metrics is None
    assert job.quality_comparable is False
    assert case is None


def test_db_id_only_truth_fails_deterministic():
    snap, _ = _snap()
    case = EvalCase(
        case_id="db-only",
        snapshot_hash=snap.manifest_hash,
        question="q",
        case_type="answerable",
        claims=[
            {"claim_id": "c1", "text": "x", "critical": True, "evidence_set_ids": []}
        ],
        gold_chunk_db_ids=[1, 2, 3],
    )
    checks = run_deterministic_checks(
        case, snap, expected_owner_id=1, expected_work_id=10
    )
    assert not checks.all_passed
    assert checks.leak_ok is False or checks.critical_claim_support_ok is False


def test_regenerate_then_quarantine():
    snap, texts = _snap()
    g, j = _lineages()

    def always_reject_judge(case, snapshot, lineage):
        return JudgeFixtureVerdict(
            faithfulness=1,
            coverage=1,
            sufficiency=1,
            critical_ambiguity=1,
            reason_codes=["reject"],
            accepted=False,
        )

    quote = "青铜与火之王"
    start = texts[0].index(quote)
    end = start + len(quote)
    job, case = run_fixture_pipeline(
        snapshot=snap,
        owner_id=1,
        work_id=10,
        case_spec={
            "case_id": "fx-quar",
            "case_type": "answerable",
            "question": "传说？",
            "evidence": [
                {
                    "content_hash": snap.chunks[0].content_hash,
                    "start": start,
                    "end": end,
                }
            ],
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "传说写在石碑上",
                    "critical": True,
                    "evidence_set_ids": ["s1"],
                }
            ],
            "reference_answer": "石碑",
        },
        generator_lineage=g,
        judge_lineage=j,
        judge=always_reject_judge,
        secret=SECRET,
        max_regenerate=MAX_REGENERATE,
    )
    assert job.status == "quarantined"
    assert job.attempt == MAX_REGENERATE
    assert job.metrics is None
    assert job.quality_comparable is False
    assert case is not None
    assert case.status == "quarantined"
    assert case.attempt == MAX_REGENERATE


def test_cross_owner_snapshot_job_fails_policy():
    snap, _ = _snap(owner_id=1, work_id=10)
    g, j = _lineages()
    job, case = run_fixture_pipeline(
        snapshot=snap,
        owner_id=2,  # different owner
        work_id=10,
        case_spec={
            "case_id": "fx-xo",
            "case_type": "no_answer",
            "question": "?",
            "reference_answer": "insufficient evidence",
        },
        generator_lineage=g,
        judge_lineage=j,
        secret=SECRET,
    )
    assert job.status in {"failed_policy", "quarantined", "invalid_fixture"}
    assert job.metrics is None
    assert case is None or case.status != "frozen"


def test_packaged_benchmark_fixture_loads_and_verifies():
    path = EVALS / "fixtures" / "rag-quality-benchmark.v1.json"
    assert path.is_file()
    data = load_json(path)
    assert data["suite_type"] == "benchmark"
    assert data["domain"] == "fiction"
    assert data["suite_hash"]
    assert data["signature"]
    assert len(data["cases"]) >= 3
    for c in data["cases"]:
        assert c["status"] == "frozen"
        assert c["fixture_hash"]
        # never rely solely on gold DB ids
        assert (
            c.get("equivalent_evidence_sets") is not None
            or c["case_type"] == "no_answer"
        )


def test_equivalent_evidence_sets_supported():
    snap, texts = _snap()
    g, j = _lineages()
    quote = "卡塞尔学院"
    start = texts[0].index(quote)
    end = start + len(quote)
    # two identical refs as equivalent set members (same support, different offsets optional)
    ref_a = make_evidence_ref(snap, snap.chunks[0].content_hash, start, end)
    # second set uses slightly different span still supporting
    quote2 = "路明非站在卡塞尔学院的门前"
    s2 = texts[0].index(quote2)
    e2 = s2 + len(quote2)
    ref_b = make_evidence_ref(snap, snap.chunks[0].content_hash, s2, e2)

    from app.services.rag_fixture import default_stub_generator

    case = default_stub_generator(
        snap,
        {
            "case_id": "eq-1",
            "case_type": "answerable",
            "question": "他站在哪里？",
            "evidence": [
                {
                    "content_hash": snap.chunks[0].content_hash,
                    "start": start,
                    "end": end,
                }
            ],
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "站在卡塞尔学院门前",
                    "critical": True,
                    "evidence_set_ids": ["s1"],
                }
            ],
            "reference_answer": "卡塞尔学院门前",
        },
        g,
    )
    # expand to two equivalent sets
    from app.schemas.eval import EquivalentEvidenceSet

    case.equivalent_evidence_sets = [
        EquivalentEvidenceSet(set_id="s1", refs=[ref_a]),
        EquivalentEvidenceSet(set_id="s2", refs=[ref_b]),
    ]
    case.claims[0].evidence_set_ids = ["s1", "s2"]
    checks = run_deterministic_checks(
        case, snap, expected_owner_id=1, expected_work_id=10
    )
    assert checks.equivalent_sets_ok
    assert checks.critical_claim_support_ok
    frozen = freeze_eval_case(case, SECRET)
    assert verify_frozen_case(frozen, SECRET)
    assert compute_fixture_hash(frozen) == frozen.fixture_hash


def test_package_benchmark_hash_stable():
    snap, texts = _snap()
    g, j = _lineages()
    quote = "狄克推多"
    start = texts[1].index(quote)
    end = start + len(quote)
    job, case = run_fixture_pipeline(
        snapshot=snap,
        owner_id=1,
        work_id=10,
        case_spec={
            "case_id": "pack-1",
            "case_type": "answerable",
            "question": "拔出了什么？",
            "evidence": [
                {
                    "content_hash": snap.chunks[1].content_hash,
                    "start": start,
                    "end": end,
                }
            ],
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "狄克推多",
                    "critical": True,
                    "evidence_set_ids": ["s1"],
                }
            ],
            "reference_answer": "狄克推多",
        },
        generator_lineage=g,
        judge_lineage=j,
        secret=SECRET,
    )
    assert case and case.status == "frozen"
    a = package_benchmark_suite(
        domain="fiction", snapshot=snap, cases=[case], secret=SECRET
    )
    b = package_benchmark_suite(
        domain="fiction", snapshot=snap, cases=[case], secret=SECRET
    )
    assert a["suite_hash"] == b["suite_hash"]
    assert a["signature"] == b["signature"]
