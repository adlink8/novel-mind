"""Frozen fiction/history narrative retrieval evaluation tests."""

import json
from pathlib import Path

import pytest

from app.services.knowledge_units.eval import NarrativeEvalError, evaluate_fixture, load_and_evaluate


EVALS = Path(__file__).parents[1] / "evals"


@pytest.mark.parametrize("name", ["narrative_units_fiction.json", "narrative_units_history.json"])
def test_frozen_fixture_passes_all_release_gates(name):
    report = load_and_evaluate(EVALS / name)
    assert report["passed"] is True
    assert report["case_count"] == 6
    assert report["strategies"]["hybrid"]["recall_at_5"] >= report["strategies"]["chunks"]["recall_at_5"]
    assert report["canary"]["passed"] is True
    assert report["faithfulness_failures"] == 0


def test_frozen_hash_mismatch_is_rejected():
    payload = json.loads((EVALS / "narrative_units_fiction.json").read_text(encoding="utf-8"))
    payload["dataset_hash"] = "0" * 64
    with pytest.raises(NarrativeEvalError, match="hash mismatch"):
        evaluate_fixture(payload)


def test_critical_canary_error_blocks_release():
    payload = json.loads((EVALS / "narrative_units_history.json").read_text(encoding="utf-8"))
    payload["cases"][0]["stale"] = True
    report = evaluate_fixture(payload)
    assert report["passed"] is False
    assert report["canary"]["stale"] == 1


def test_faithfulness_failure_blocks_release():
    payload = json.loads((EVALS / "narrative_units_fiction.json").read_text(encoding="utf-8"))
    payload["cases"][0]["faithful"] = False
    assert evaluate_fixture(payload)["passed"] is False
