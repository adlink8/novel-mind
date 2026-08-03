"""Adversarial lineage / leakage tests for the reading-QA gold set (Phase 29-01).

REQ-QA-01 / D-02, D-04, D-05: lineage spoofing, wrong owner, source mutation,
fingerprint tampering and spoiler leakage must block qualification. ``blocked``
is a legal verdict; nothing ever passes by default.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.qualification.gold_set import (
    GoldSetError,
    freeze_gold_set,
    load_gold_set,
    slice_content_hash,
)
from app.services.qualification.rubric import (
    CODE_BEYOND_CUTOFF,
    CODE_CROSS_NOVEL,
    CODE_CROSS_OWNER,
    CODE_CROSS_SNAPSHOT,
    CODE_CROSS_VERSION,
    CODE_FINGERPRINT_MISMATCH,
    CODE_FOREIGN_CHAPTER,
    CODE_NO_ANSWER_HALLUCINATION,
    CODE_SPOILER_LEAK,
    RubricVerdict,
    audit_dataset,
    evaluate_qualification,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

GOLD_PATH = (
    Path(__file__).resolve().parents[2] / "evals" / "reading_qa_v1.json"
)
QUAL_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "qualification"
)


@pytest.fixture(scope="module")
def gold_set():
    return load_gold_set(GOLD_PATH)


@pytest.fixture(scope="module")
def raw_payload() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _gold_answers(gold_set):
    out = {}
    for s in gold_set.samples:
        if s.expected_answerability == "answerable":
            sa = s.source_answers[0]
            out[s.id] = {
                "answer": sa.answer,
                "evidence": [r.model_dump(mode="json") for r in sa.evidence],
                "abstained": False,
            }
        else:
            out[s.id] = {"answer": "", "evidence": [], "abstained": True}
    return out


def test_lineage_spoofing_cross_snapshot_blocks(gold_set):
    answers = _gold_answers(gold_set)
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    ref = sample.source_answers[0].evidence[0].model_dump(mode="json")
    ref["source_snapshot_hash"] = "e" * 64  # forged snapshot lineage
    answers[sample.id]["evidence"] = [ref]
    result = evaluate_qualification(gold_set, candidate_answers=answers)
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_CROSS_SNAPSHOT in result.reason_codes


def test_wrong_owner_blocks(gold_set):
    answers = _gold_answers(gold_set)
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    result = evaluate_qualification(
        gold_set,
        candidate_answers=answers,
        context_by_sample={sample.id: {"owner_id": gold_set.owner_id + 1}},
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_CROSS_OWNER in result.reason_codes


def test_wrong_novel_and_version_block(gold_set):
    answers = _gold_answers(gold_set)
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    result = evaluate_qualification(
        gold_set,
        candidate_answers=answers,
        context_by_sample={
            sample.id: {
                "novel_id": gold_set.novel_id + 1,
                "version_id": gold_set.version_id + 1,
            }
        },
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_CROSS_NOVEL in result.reason_codes
    assert CODE_CROSS_VERSION in result.reason_codes


def test_fingerprint_tampering_blocks_load(raw_payload):
    mutated = deepcopy(raw_payload)
    mutated["fingerprint"] = "0" * 64
    with pytest.raises(GoldSetError) as ei:
        freeze_gold_set(mutated, require_frozen=True)
    assert ei.value.code == CODE_FINGERPRINT_MISMATCH


def test_source_mutation_detected(raw_payload):
    # mutate chapter text without touching hashes -> re-slice hash mismatch
    mutated = deepcopy(raw_payload)
    mutated["source"]["chapters"][0]["content"] = (
        mutated["source"]["chapters"][0]["content"].replace("旧书店", "旧客栈")
    )
    with pytest.raises(GoldSetError, match="content_hash mismatch"):
        freeze_gold_set(mutated, require_frozen=False)


def test_spoiler_leak_blocks_even_with_valid_evidence(gold_set):
    sample = next(s for s in gold_set.samples if s.bucket == "spoiler")
    # cite a beyond-cutoff chapter 6 ref AND borrow its text
    chapter = gold_set.chapter_by_number(6)
    ref = {
        "chapter_id": chapter.chapter_id,
        "chapter_number": 6,
        "source_start": 0,
        "source_end": 6,
        "content_hash": slice_content_hash(chapter.content, 0, 6),
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    result = evaluate_qualification(
        gold_set,
        candidate_answers={
            sample.id: {
                "answer": "何太太被捕，灯塔重新亮起",
                "evidence": [ref],
                "abstained": False,
            }
        },
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_SPOILER_LEAK in result.reason_codes


def test_no_answer_hallucination_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.bucket == "no_answer")
    result = evaluate_qualification(
        gold_set,
        candidate_answers={
            sample.id: {
                "answer": "她叫李月",
                "evidence": [],
                "abstained": False,
            }
        },
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_NO_ANSWER_HALLUCINATION in result.reason_codes


def test_beyond_cutoff_with_clean_text_still_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "local_01")
    chapter = gold_set.chapter_by_number(6)
    ref = {
        "chapter_id": chapter.chapter_id,
        "chapter_number": 6,
        "source_start": 0,
        "source_end": 6,
        "content_hash": slice_content_hash(chapter.content, 0, 6),
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    result = evaluate_qualification(
        gold_set,
        candidate_answers={
            sample.id: {
                "answer": "在阁楼的旧笔记本里",
                "evidence": [ref],
                "abstained": False,
            }
        },
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_BEYOND_CUTOFF in result.reason_codes


def test_foreign_chapter_citation_blocks(gold_set):
    sample = next(s for s in gold_set.samples if s.id == "cross_01")
    ref = {
        "chapter_id": 4242,
        "chapter_number": 3,
        "source_start": 0,
        "source_end": 4,
        "content_hash": "a" * 64,
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    result = evaluate_qualification(
        gold_set,
        candidate_answers={
            sample.id: {
                "answer": sample.source_answers[0].answer,
                "evidence": [ref],
                "abstained": False,
            }
        },
    )
    assert result.verdict == RubricVerdict.BLOCKED
    assert CODE_FOREIGN_CHAPTER in result.reason_codes


def test_blocked_is_legal_not_a_pass(gold_set):
    result = evaluate_qualification(gold_set)
    assert result.verdict in (RubricVerdict.QUALIFIED_CANDIDATE, RubricVerdict.BLOCKED)
    banned = {"promoted", "active", "production_ready", "passed", "current"}
    assert not (set(result.reason_codes) & banned)
    assert audit_dataset(gold_set) == []
    assert gold_set.fingerprint is not None


def test_modules_never_touch_provider_or_promotion():
    for name in ("gold_set.py", "rubric.py"):
        src = (QUAL_DIR / name).read_text(encoding="utf-8")
        for forbidden in ("litellm", "openai", "prepare_baseline", "ActiveBaseline"):
            assert forbidden not in src, f"{name} contains {forbidden!r}"
