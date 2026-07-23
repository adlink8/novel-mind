"""Frozen Phase 08 fiction qualification and deterministic replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_timeline_qualification import load_corpus, run_offline_qualification

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "evals" / "timeline_fiction.v1.json"


def test_frozen_fiction_corpus_has_locked_coverage_and_no_deferred_products():
    corpus = load_corpus(CORPUS)
    assert corpus["dataset_version"] == "timeline-fiction.v1"
    assert corpus["domain"] == "fiction"
    assert len(corpus["cases"]) >= 20
    assert len(corpus["cross_chapter_groups"]) >= 10
    assert {case["precision"] for case in corpus["cases"]} == {
        "exact",
        "relative",
        "fuzzy",
        "unknown",
    }
    assert {case["shape"] for case in corpus["cases"]} >= {
        "forward",
        "flashback",
        "interlude",
    }
    assert set(corpus["deferred_products_absent"]) == {
        "relationship_graph",
        "reader_ai",
        "clue_tracker",
        "history_corpus",
    }


def test_offline_qualification_is_byte_deterministic_and_lineage_bound():
    first = run_offline_qualification(CORPUS)
    second = run_offline_qualification(CORPUS)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["status"] == "qualified"
    assert first["quality_comparable"] is True
    assert first["lineage"]["source_snapshot_hash"] == "a" * 64
    assert first["lineage"]["prompt_hash"] == "c" * 64
    assert first["lineage"]["schema_hash"] == "d" * 64
    assert first["metrics"]["event_precision"] >= 0.90
    assert first["metrics"]["story_pairwise_accuracy"] >= 0.90
    assert first["metrics"]["duplicate_f1"] >= 0.90
    assert first["metrics"]["causal_precision"] >= 0.90


@pytest.mark.parametrize(
    ("mutation", "gate"),
    [
        ({"evidence_valid": False}, "evidence_validity"),
        ({"budget_status": "paused_budget"}, "budget_complete"),
        ({"spoiler_leaks": 1}, "spoiler_safety"),
        ({"active_candidate_merged": True}, "version_separation"),
    ],
)
def test_critical_failures_cannot_qualify(mutation, gate):
    report = run_offline_qualification(CORPUS, controls=mutation)
    assert report["status"] == "failed_policy"
    assert report["quality_comparable"] is False
    assert report["metrics"] is None
    assert report["gates"][gate] is False
