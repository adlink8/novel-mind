"""Offline knowledge graph fixture evaluation tests."""

import pytest

pytestmark = pytest.mark.unit

import json
import subprocess
import sys
from pathlib import Path

from app.models.knowledge import RELATION_TYPES_BY_DOMAIN_PROFILE
from scripts.run_knowledge_graph_eval import evaluate_fixture, load_fixture


BACKEND_DIR = Path(__file__).resolve().parents[1]
FICTION_FIXTURE = BACKEND_DIR / "evals" / "knowledge_graph_fiction_sample.json"
HISTORY_FIXTURE = BACKEND_DIR / "evals" / "knowledge_graph_history_sample.json"


def test_fixtures_provide_twenty_labeled_examples():
    fiction = load_fixture(FICTION_FIXTURE)
    history = load_fixture(HISTORY_FIXTURE)

    assert fiction["domain_profile"] == "fiction"
    assert history["domain_profile"] == "history"
    assert len(fiction["examples"]) == 10
    assert len(history["examples"]) == 10

    for fixture in (fiction, history):
        for example in fixture["examples"]:
            assert example["expected_decision"] in {
                "accepted",
                "rejected",
                "needs_human_review",
            }
            assert example["expected_evidence_refs"]
            assert set(example["expected_evidence_refs"]).issubset(
                set(example["candidate"]["evidence_refs"])
            )


def test_eval_report_separates_recall_signals_from_accepted_graph_facts():
    report = evaluate_fixture(load_fixture(FICTION_FIXTURE), dry_run=True)

    assert report["success"] is True
    assert report["pipeline"]["llm_script_split"]["llm_responsibility"] == (
        "semantic judgment only"
    )
    assert report["recall_signal_quality"]["candidate_coverage_rate"] == 1.0
    assert report["recall_signal_quality"]["evidence_bound_candidate_rate"] == 1.0
    assert "retrieval" in report["recall_signal_quality"]["signal_kinds_observed"]
    assert report["accepted_graph_fact_quality"]["accepted_precision"] == 1.0
    assert report["accepted_graph_fact_quality"]["false_accepted"] == []


def test_history_and_fiction_share_core_pipeline_with_different_ontology_profiles():
    fiction_report = evaluate_fixture(load_fixture(FICTION_FIXTURE), dry_run=True)
    history_report = evaluate_fixture(load_fixture(HISTORY_FIXTURE), dry_run=True)

    assert fiction_report["pipeline"]["core_steps"] == history_report["pipeline"]["core_steps"]
    assert fiction_report["ontology_profile"] == "fiction.v1"
    assert history_report["ontology_profile"] == "history.v1"
    assert "romantic" in RELATION_TYPES_BY_DOMAIN_PROFILE["fiction"]
    assert "romantic" not in RELATION_TYPES_BY_DOMAIN_PROFILE["history"]
    assert "caused" in RELATION_TYPES_BY_DOMAIN_PROFILE["history"]


def test_evidence_gates_schema_failures_and_review_routing_are_counted():
    fiction_report = evaluate_fixture(load_fixture(FICTION_FIXTURE), dry_run=True)
    history_report = evaluate_fixture(load_fixture(HISTORY_FIXTURE), dry_run=True)

    assert fiction_report["judgment_quality"]["schema_failure_count"] == 1
    assert fiction_report["judgment_quality"]["evidence_gate_failure_count"] == 1
    assert fiction_report["judgment_quality"]["review_routing_accuracy"] == 1.0
    assert history_report["judgment_quality"]["schema_failure_count"] == 1
    assert history_report["judgment_quality"]["evidence_gate_failure_count"] == 1
    assert history_report["judgment_quality"]["review_routing_accuracy"] == 1.0


def test_cli_dry_run_reports_cost_latency_and_optional_blocked_llm_path():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_knowledge_graph_eval.py",
            "--fixture",
            "backend/evals/knowledge_graph_fiction_sample.json",
            "--dry-run",
            "--faithfulness-mode",
            "blocked",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)

    assert report["success"] is True
    assert report["dry_run"] is True
    assert report["cost_latency"]["llm_calls"] == 10
    assert report["cost_latency"]["prompt_tokens"] == 0
    assert report["cost_latency"]["total_cost_usd"] == 0.0
    assert report["cost_latency"]["latency_ms_p50"] == 0.0
    assert report["faithfulness"]["optional_llm_check"]["status"] == "blocked"
