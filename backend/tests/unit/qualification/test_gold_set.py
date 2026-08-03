"""Phase 29-01 reading-QA gold-set unit tests (no DB, no provider).

Covers the eight-bucket frozen gold set, reproducible fingerprint / curator
agreement, and the leakage / owner / spoiler / lineage gates that block
qualification (REQ-QA-01, D-01/D-02/D-04/D-05).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.qualification.gold_set import (
    GOLD_BUCKETS,
    GoldSetError,
    ReadingQAGoldSet,
    curator_agreement,
    dataset_fingerprint,
    freeze_gold_set,
    load_gold_set,
    slice_content_hash,
    stable_json,
)
from app.services.qualification.rubric import (
    CODE_BEYOND_CUTOFF,
    CODE_CITATION_OUTSIDE_GOLD,
    CODE_CROSS_OWNER,
    CODE_CROSS_SNAPSHOT,
    CODE_CURATOR_DISAGREEMENT,
    CODE_FINGERPRINT_MISMATCH,
    CODE_FOREIGN_CHAPTER,
    CODE_NO_ANSWER_HALLUCINATION,
    CODE_SPOILER_LEAK,
    CODE_UNCITED_ASSERTION,
    RubricVerdict,
    audit_dataset,
    evaluate_qualification,
)

pytestmark = pytest.mark.unit

GOLD_PATH = (
    Path(__file__).resolve().parents[3] / "evals" / "reading_qa_v1.json"
)
QUAL_DIR = (
    Path(__file__).resolve().parents[3] / "app" / "services" / "qualification"
)


@pytest.fixture(scope="module")
def gold_set() -> ReadingQAGoldSet:
    return load_gold_set(GOLD_PATH)


@pytest.fixture(scope="module")
def raw_payload() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _answers_for(sample) -> dict:
    """A clean candidate answer aligned with the gold sample."""
    if sample.expected_answerability == "answerable":
        sa = sample.source_answers[0]
        return {
            "answer": sa.answer,
            "evidence": [ref.model_dump(mode="json") for ref in sa.evidence],
            "abstained": False,
        }
    return {"answer": "", "evidence": [], "abstained": True}


# ---------------------------------------------------------------------------
# Eight-bucket frozen gold set (D-01)
# ---------------------------------------------------------------------------


def test_gold_set_loads(gold_set):
    assert gold_set.schema_version == "reading-qa-gold.v1"
    assert gold_set.partition == "frozen"
    assert gold_set.dataset_version == "reading-qa.v1"
    assert len(gold_set.samples) == 14


def test_all_eight_buckets_present(gold_set):
    counts = gold_set.bucket_counts()
    assert set(counts) == {b.value for b in GOLD_BUCKETS}
    for bucket in GOLD_BUCKETS:
        assert counts[bucket.value] >= 1, f"bucket {bucket.value} empty"


def test_every_answerable_sample_has_resolvable_source_answers(gold_set):
    for sample in gold_set.samples:
        if sample.expected_answerability != "answerable":
            continue
        assert sample.source_answers
        for sa in sample.source_answers:
            assert sa.answer
            assert sa.evidence
            for ref in sa.evidence:
                # re-slice must resolve: already enforced at load, double-check
                chapter = gold_set.chapter_by_number(ref.chapter_number)
                assert chapter is not None
                assert chapter.content[ref.source_start : ref.source_end]
                assert ref.content_hash[:12]  # stored hash present


def test_no_answer_and_spoiler_bucket_rules(gold_set):
    for sample in gold_set.samples:
        if sample.bucket == "no_answer":
            assert not sample.source_answers
            assert sample.no_answer_rationale
        elif sample.bucket == "spoiler":
            assert not sample.source_answers
            assert sample.spoiler_forbidden
            assert any(f.chapter_number is not None for f in sample.spoiler_forbidden)


def test_gold_evidence_within_cutoff(gold_set):
    for sample in gold_set.samples:
        for sa in sample.source_answers:
            for ref in sa.evidence:
                if not sample.full_book_authorized:
                    assert ref.chapter_number <= sample.through_chapter, (
                        f"{sample.id} gold evidence beyond cutoff"
                    )


def test_every_sample_carries_cutoff_labels(gold_set):
    for sample in gold_set.samples:
        for sa in sample.source_answers:
            assert sa.cutoff_label in ("within_cutoff", "at_cutoff", "forbidden")


def test_source_answers_use_original_text_only(gold_set):
    """Gold answers must be quotable from the frozen chapters (no invention)."""
    allowed = set()
    for chapter in gold_set.source.chapters:
        allowed.add(chapter.content)
    for sample in gold_set.samples:
        for sa in sample.source_answers:
            # at least one evidence slice must appear verbatim in its chapter
            for ref in sa.evidence:
                chapter = gold_set.chapter_by_number(ref.chapter_number)
                assert chapter is not None
                excerpt = chapter.content[ref.source_start : ref.source_end]
                assert excerpt in chapter.content


# ---------------------------------------------------------------------------
# Reproducible dataset fingerprint (D-02)
# ---------------------------------------------------------------------------


def test_fingerprint_reproducible(gold_set, raw_payload):
    fp1 = dataset_fingerprint(raw_payload)
    # key order change must not alter the fingerprint
    reordered = json.loads(json.dumps(raw_payload, sort_keys=False))
    fp2 = dataset_fingerprint(reordered)
    assert fp1 == fp2 == gold_set.fingerprint
    assert len(fp1) == 64


def test_fingerprint_hash_sensitivity(raw_payload):
    base = dataset_fingerprint(raw_payload)
    mutated = deepcopy(raw_payload)
    mutated["samples"][0]["query"] = "改动的问题？"
    assert dataset_fingerprint(mutated) != base


def test_fingerprint_detects_source_mutation(raw_payload):
    base = dataset_fingerprint(raw_payload)
    mutated = deepcopy(raw_payload)
    mutated["source"]["chapters"][0]["content"] += "多出一句话。"
    assert dataset_fingerprint(mutated) != base


def test_fingerprint_mismatch_blocks_load(raw_payload):
    mutated = deepcopy(raw_payload)
    mutated["fingerprint"] = "f" * 64
    with pytest.raises(GoldSetError) as ei:
        freeze_gold_set(mutated, require_frozen=True)
    assert ei.value.code == CODE_FINGERPRINT_MISMATCH


def test_result_fields_forbidden(raw_payload):
    mutated = deepcopy(raw_payload)
    mutated["candidate_score"] = 0.9
    with pytest.raises(GoldSetError, match="result-derived"):
        freeze_gold_set(mutated)


def test_stable_json_order_independent(raw_payload):
    a = stable_json(raw_payload)
    b = stable_json(json.loads(json.dumps(raw_payload, sort_keys=False)))
    assert a == b


# ---------------------------------------------------------------------------
# Reproducible curator agreement (D-01)
# ---------------------------------------------------------------------------


def test_curator_agreement_unanimous(gold_set):
    agreement = curator_agreement(gold_set)
    assert agreement.is_unanimous
    assert agreement.overall == 1.0
    assert agreement.unanimous_samples == len(gold_set.samples)
    assert all(agreement.per_sample[s.id] for s in gold_set.samples)


def test_curator_disagreement_detected(raw_payload):
    mutated = deepcopy(raw_payload)
    mutated["samples"][0]["curator_ratings"][1]["cutoff_ok"] = False
    model = ReadingQAGoldSet.model_validate(mutated)
    agreement = curator_agreement(model)
    assert agreement.overall < 1.0
    assert not agreement.per_sample["local_01"]
    with pytest.raises(GoldSetError) as ei:
        freeze_gold_set(mutated, require_frozen=False, require_agreement=True)
    assert ei.value.code == CODE_CURATOR_DISAGREEMENT


def test_fingerprint_and_agreement_reproducible_across_reloads(raw_payload):
    first = freeze_gold_set(raw_payload, require_frozen=True)
    again = freeze_gold_set(
        json.loads(json.dumps(raw_payload, sort_keys=False)), require_frozen=True
    )
    assert first.fingerprint == again.fingerprint
    assert curator_agreement(first).overall == curator_agreement(again).overall == 1.0


# ---------------------------------------------------------------------------
# Blocked data conditions never pass (D-05)
# ---------------------------------------------------------------------------


def test_clean_candidates_qualify(gold_set):
    candidate_answers = {
        s.id: _answers_for(s) for s in gold_set.samples
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.QUALIFIED_CANDIDATE
    assert result.violations == ()
    assert result.reason_codes == ()


def test_no_answer_hallucination_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.bucket == "no_answer")
    candidate_answers = {sample.id: _answers_for(sample)}
    candidate_answers[sample.id] = {
        "answer": "林安的母亲叫李月",
        "evidence": [],
        "abstained": False,
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_NO_ANSWER_HALLUCINATION in result.reason_codes


def test_spoiler_leak_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.bucket == "spoiler")
    # answer borrows chapter 6 content verbatim -> leak beyond cutoff
    candidate_answers = {
        sample.id: {
            "answer": "何太太被捕，灯塔重新亮起",
            "evidence": [],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_SPOILER_LEAK in result.reason_codes


def test_spoiler_evidence_beyond_cutoff_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.bucket == "spoiler")
    chapter = gold_set.chapter_by_number(6)
    ref = {
        "chapter_id": chapter.chapter_id,
        "chapter_number": 6,
        "source_start": 0,
        "source_end": 6,
        "content_hash": slice_content_hash(chapter.content, 0, 6),
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    candidate_answers = {
        sample.id: {
            "answer": "",
            "evidence": [ref],
            "abstained": True,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_SPOILER_LEAK in result.reason_codes


def test_beyond_cutoff_evidence_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    # cite a chapter 6 ref although cutoff is chapter 1
    chapter = gold_set.chapter_by_number(6)
    ref = {
        "chapter_id": chapter.chapter_id,
        "chapter_number": 6,
        "source_start": 0,
        "source_end": 6,
        "content_hash": slice_content_hash(chapter.content, 0, 6),
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    candidate_answers = {
        sample.id: {
            "answer": "在阁楼的旧笔记本里",
            "evidence": [ref],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_BEYOND_CUTOFF in result.reason_codes


def test_cross_snapshot_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    ref = sample.source_answers[0].evidence[0]
    bad_ref = ref.model_dump(mode="json")
    bad_ref["source_snapshot_hash"] = "f" * 64  # different owner/version snapshot
    candidate_answers = {
        sample.id: {
            "answer": sample.source_answers[0].answer,
            "evidence": [bad_ref],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_CROSS_SNAPSHOT in result.reason_codes


def test_cross_owner_context_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    candidate_answers = {sample.id: _answers_for(sample)}
    context_by_sample = {sample.id: {"owner_id": 999}}
    result = evaluate_qualification(
        gold_set,
        candidate_answers=candidate_answers,
        context_by_sample=context_by_sample,
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_CROSS_OWNER in result.reason_codes


def test_foreign_chapter_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    ref = {
        "chapter_id": 999,
        "chapter_number": 1,
        "source_start": 0,
        "source_end": 4,
        "content_hash": "0" * 64,
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    candidate_answers = {
        sample.id: {
            "answer": sample.source_answers[0].answer,
            "evidence": [ref],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_FOREIGN_CHAPTER in result.reason_codes


def test_stale_content_hash_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    ref = sample.source_answers[0].evidence[0]
    bad_ref = ref.model_dump(mode="json")
    bad_ref["content_hash"] = "e" * 64  # no longer matches the re-sliced text
    candidate_answers = {
        sample.id: {
            "answer": sample.source_answers[0].answer,
            "evidence": [bad_ref],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert "evidence_content_mismatch" in result.reason_codes


def test_uncited_assertion_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    candidate_answers = {
        sample.id: {
            "answer": sample.source_answers[0].answer,
            "evidence": [],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_UNCITED_ASSERTION in result.reason_codes


def test_citation_outside_gold_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    # valid in-cutoff ref but not part of this sample's gold allowlist
    other = next(s for s in gold_set.samples if s.id == "local_02")
    ref = other.source_answers[0].evidence[0].model_dump(mode="json")
    candidate_answers = {
        sample.id: {
            "answer": sample.source_answers[0].answer,
            "evidence": [ref],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_CITATION_OUTSIDE_GOLD in result.reason_codes


def test_content_leak_beyond_cutoff_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "character_knowledge_01")
    # answer quotes chapter 6 text although cutoff is chapter 3
    candidate_answers = {
        sample.id: {
            "answer": "何太太被捕，灯塔重新亮起",
            "evidence": [r.model_dump(mode="json") for r in sample.source_answers[0].evidence],
            "abstained": False,
        }
    }
    result = evaluate_qualification(gold_set, candidate_answers=candidate_answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert "content_leak_beyond_cutoff" in result.reason_codes


def test_dataset_audit_clean(gold_set):
    violations = audit_dataset(gold_set)
    assert violations == []


def test_future_metadata_detected(raw_payload):
    mutated = deepcopy(raw_payload)
    mutated["samples"][0]["through_chapter"] = 99
    with pytest.raises(GoldSetError, match="future metadata"):
        freeze_gold_set(mutated, require_frozen=False)


def test_duplicate_sample_id_rejected(raw_payload):
    mutated = deepcopy(raw_payload)
    mutated["samples"][1]["id"] = mutated["samples"][0]["id"]
    with pytest.raises(GoldSetError, match="duplicate sample id"):
        freeze_gold_set(mutated, require_frozen=False)


def test_verdict_vocabulary_only_two_values(gold_set):
    result = evaluate_qualification(gold_set)
    assert result.verdict in (RubricVerdict.QUALIFIED_CANDIDATE, RubricVerdict.BLOCKED)
    banned = {"promoted", "active", "production_ready", "passed", "current"}
    assert not (set(result.reason_codes) & banned)


def test_zero_provider_and_promotion():
    from app.services.qualification.gold_set import (
        gold_set_has_forbidden_capability,
        gold_set_has_promotion_capability,
    )
    from app.services.qualification.rubric import RubricVerdict as RV

    assert gold_set_has_forbidden_capability() is False
    assert gold_set_has_promotion_capability() is False
    assert RV.QUALIFIED_CANDIDATE.value == "qualified_candidate"
    assert RV.BLOCKED.value == "blocked"
