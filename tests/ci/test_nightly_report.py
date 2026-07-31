"""Phase 22-G2 Nightly control-plane report contracts."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SECRET = "nightly-test-secret"


def _load():
    path = REPO / "scripts" / "ci" / "finalize-nightly-report.py"
    spec = importlib.util.spec_from_file_location("finalize_nightly_report", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nr = _load()


def _load_promotion():
    path = REPO / "scripts" / "ci" / "promote-baseline.py"
    spec = importlib.util.spec_from_file_location("promote_baseline_for_nightly", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


promotion = _load_promotion()


def _authority(ready: bool) -> dict:
    return {
        "schema_version": "nightly-authority.v1",
        "provider_ready": ready,
        "reason": "eligible_runner_online" if ready else "no_eligible_runner",
        "required_labels": ["self-hosted", "linux", "ollama"],
        "runner": {"name": "quality-1"} if ready else None,
    }


def _provider_report(status: str = "qualified") -> dict:
    report = {
        "schema_version": "rag-quality.v1",
        "policy_version": "rag-quality-policy.v1",
        "policy_hash": "policy-1",
        "status": status,
        "quality_comparable": status in {"passed", "qualified"},
        "metrics": {
            "context_recall_at_5_mean": 0.9,
            "answer_relevance_mean": 0.91,
            "cost_usd_total": 0.12,
            "answer_faithfulness_95lb": 0.93,
            "context_precision_mean": 0.88,
            "verdict_consistency": 0.95,
        },
        "repeats": 3,
    }
    body = nr.stable_dumps(report).encode("utf-8")
    report["report_signature"] = hmac.new(
        SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return report


def test_missing_runner_emits_signed_noncomparable_report() -> None:
    report = nr.finalize_report(
        authority=_authority(False),
        provider_result="skipped",
        provider_report=None,
        secret=SECRET,
        run_id="1",
        commit_sha="abc",
        event_name="schedule",
    )
    assert report["status"] == "blocked_dependency"
    assert report["quality_comparable"] is False
    assert report["metrics"] is None
    assert report["promotable"] is False
    assert report["report_signature"] == nr.sign_canonical_report(report, SECRET)


def test_valid_provider_report_preserves_metrics_and_authority() -> None:
    report = nr.finalize_report(
        authority=_authority(True),
        provider_result="success",
        provider_report=_provider_report(),
        secret=SECRET,
        run_id="2",
        commit_sha="def",
        event_name="schedule",
    )
    assert report["status"] == "qualified"
    assert report["quality_comparable"] is True
    assert report["promotable"] is True
    assert report["execution_authority"]["runner"]["name"] == "quality-1"
    assert report["report_signature"] == nr.sign_canonical_report(report, SECRET)


@pytest.mark.parametrize("provider_result", ["failure", "cancelled", "missing"])
def test_missing_provider_report_fails_policy(provider_result: str) -> None:
    report = nr.finalize_report(
        authority=_authority(True),
        provider_result=provider_result,
        provider_report=None,
        secret=SECRET,
        run_id="3",
        commit_sha="ghi",
        event_name="schedule",
    )
    assert report["status"] == "failed_policy"
    assert report["metrics"] is None
    assert report["promotable"] is False


def test_invalid_provider_signature_fails_closed() -> None:
    provider = _provider_report()
    provider["report_signature"] = "0" * 64
    report = nr.finalize_report(
        authority=_authority(True),
        provider_result="success",
        provider_report=provider,
        secret=SECRET,
        run_id="4",
        commit_sha="jkl",
        event_name="schedule",
    )
    assert report["status"] == "failed_policy"
    assert report["reason"] == "provider_report_signature_invalid"


def test_secret_is_required() -> None:
    with pytest.raises(nr.NightlyReportError, match="signing secret"):
        nr.finalize_report(
            authority=_authority(False),
            provider_result="skipped",
            provider_report=None,
            secret="",
            run_id="5",
            commit_sha="mno",
            event_name="schedule",
        )


def test_only_finalized_qualified_report_can_prepare_promotion(tmp_path: Path) -> None:
    report = nr.finalize_report(
        authority=_authority(True),
        provider_result="success",
        provider_report=_provider_report(),
        secret=SECRET,
        run_id="6",
        commit_sha="pqr",
        event_name="schedule",
    )
    path = tmp_path / "qualified.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    prepared = promotion.prepare(
        path,
        REPO / ".github" / "quality" / "baseline-policy.yml",
        tmp_path / "prepared.json",
        secret=SECRET,
    )
    assert prepared["status"] == "prepared"


def test_finalized_blocked_dependency_cannot_promote(tmp_path: Path) -> None:
    report = nr.finalize_report(
        authority=_authority(False),
        provider_result="skipped",
        provider_report=None,
        secret=SECRET,
        run_id="7",
        commit_sha="stu",
        event_name="schedule",
    )
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(promotion.BaselinePromotionError, match="rejected"):
        promotion.prepare(
            path,
            REPO / ".github" / "quality" / "baseline-policy.yml",
            tmp_path / "prepared.json",
            secret=SECRET,
        )
