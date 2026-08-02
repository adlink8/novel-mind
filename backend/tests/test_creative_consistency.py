"""Phase 33-02 local deterministic consistency-gate tests."""

import pytest

from app.schemas.creative_evaluation import CreativeClaim
from app.services.creative_consistency import evaluate_consistency
from app.services.creative_generation_policy import build_context_package

pytestmark = pytest.mark.unit


def _package():
    return build_context_package(
        owner_id=5,
        novel_id=11,
        project_id=19,
        cutoff_chapter_number=3,
        original_evidence=[
            {
                "evidence_key": "chunk:91:7",
                "novel_id": 11,
                "chapter_id": 3,
                "text_chunk_id": 7,
                "source_start": 0,
                "source_end": 12,
                "content_hash": "a" * 64,
            }
        ],
    )


def test_consistency_report_is_deterministic_and_passes_supported_claims():
    claims = [
        CreativeClaim(
            claim_key="c1",
            category="established_fact",
            text="门在第三章出现",
            evidence_keys=["chunk:91:7"],
            chapter_number=3,
            disposition="consistent",
        )
    ]
    first = evaluate_consistency(_package(), claims, owner_id=5, novel_id=11)
    second = evaluate_consistency(_package(), claims, owner_id=5, novel_id=11)
    assert first.status == "passed"
    assert first.citation_coverage == 1
    assert first.report_hash == second.report_hash
    assert first.candidate_only is True


def test_consistency_gate_fails_for_missing_citation_cutoff_and_contradiction():
    report = evaluate_consistency(
        _package(),
        [
            CreativeClaim(
                claim_key="missing",
                category="character_behavior",
                text="未引用主张",
            ),
            CreativeClaim(
                claim_key="future",
                category="timeline",
                text="超出 cutoff",
                evidence_keys=["chunk:91:7"],
                chapter_number=4,
                disposition="consistent",
            ),
            CreativeClaim(
                claim_key="conflict",
                category="established_fact",
                text="与原作冲突",
                evidence_keys=["chunk:91:7"],
                chapter_number=3,
                disposition="contradiction",
            ),
        ],
        owner_id=5,
        novel_id=11,
    )
    codes = {finding.rule_code for finding in report.findings}
    assert report.status == "failed"
    assert {"missing_evidence", "cutoff_exceeded", "contradiction"}.issubset(codes)


def test_unknown_claim_is_warning_but_does_not_pass_as_fact():
    report = evaluate_consistency(
        _package(),
        [
            CreativeClaim(
                claim_key="unknown",
                category="character_behavior",
                text="需要判断",
                evidence_keys=["chunk:91:7"],
                disposition="unknown",
            )
        ],
        owner_id=5,
        novel_id=11,
    )
    assert report.status == "passed_with_warnings"
    assert report.findings[0].rule_code == "uncertain"
