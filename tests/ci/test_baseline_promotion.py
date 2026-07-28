"""Baseline prepare/commit and alert isolation tests (06-06 / D-18)."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

POLICY_PATH = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"
SECRET = "test-baseline-secret-06-06"

pytestmark = pytest.mark.contract


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pb = _load("promote_baseline", REPO_ROOT / "scripts" / "ci" / "promote-baseline.py")


def _metrics() -> dict:
    return {
        "context_recall_at_5_mean": 0.92,
        "answer_relevance_mean": 0.91,
        "cost_usd_total": 0.12,
        "answer_faithfulness_95lb": 0.93,
        "context_precision_mean": 0.88,
        "verdict_consistency": 0.95,
    }


def _signed_report(
    *,
    status: str = "passed",
    quality_comparable: bool = True,
    metrics: dict | None = None,
    schema_version: str = "rag-quality.v1",
    repeats: int = 3,
    extra: dict | None = None,
    secret: str = SECRET,
) -> dict:
    report: dict = {
        "status": status,
        "quality_comparable": quality_comparable,
        "schema_version": schema_version,
        "policy_hash": "abc123",
        "policy_version": "rag-quality-policy.v1",
        "repeats": repeats,
        "metrics": metrics if metrics is not None else _metrics(),
        "n_cases": 4,
    }
    if extra:
        report.update(extra)
    report["report_signature"] = pb.sign_report_payload(report, secret)
    return report


def test_prepare_and_commit_happy_path(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report = _signed_report(status="qualified")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    prep_out = tmp_path / "prepare.json"
    env = pb.prepare(report_path, POLICY_PATH, prep_out, secret=SECRET)
    assert env["status"] == "prepared"
    assert prep_out.is_file()

    base_out = tmp_path / "baseline.json"
    committed = pb.commit(prep_out, POLICY_PATH, base_out, secret=SECRET)
    assert committed["status"] == "committed"
    assert base_out.is_file()
    data = json.loads(base_out.read_text(encoding="utf-8"))
    assert data["metrics"]["answer_faithfulness_95lb"] == 0.93
    assert data["signature"]


def test_passed_status_promotable(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_signed_report(status="passed")), encoding="utf-8")
    pb.prepare(report_path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


@pytest.mark.parametrize(
    "status",
    [
        "failed_policy",
        "quality_regression",
        "blocked_dependency",
        "invalid_fixture",
        "invalid_lineage",
        "quarantined",
        "cancelled",
    ],
)
def test_rejected_statuses_fail_closed(tmp_path: Path, status: str) -> None:
    report_path = tmp_path / "report.json"
    report = _signed_report(
        status=status,
        quality_comparable=False,
        metrics=None if status.startswith("blocked") or status.startswith("invalid") else _metrics(),
    )
    # blocked/invalid often have null metrics — force for signature path
    if report.get("metrics") is None:
        report["metrics"] = _metrics()
        report["quality_comparable"] = False
        report["report_signature"] = pb.sign_report_payload(report, SECRET)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError):
        pb.prepare(report_path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_unsigned_report_rejected(tmp_path: Path) -> None:
    report = _signed_report()
    report.pop("report_signature")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="signature"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_tampered_signature_rejected(tmp_path: Path) -> None:
    report = _signed_report()
    report["report_signature"] = "0" * 64
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="signature"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_wrong_schema_rejected(tmp_path: Path) -> None:
    report = _signed_report(schema_version="wrong.v0")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="schema_version"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_null_metrics_rejected(tmp_path: Path) -> None:
    report = {
        "status": "passed",
        "quality_comparable": True,
        "schema_version": "rag-quality.v1",
        "metrics": None,
        "repeats": 3,
    }
    report["report_signature"] = pb.sign_report_payload(report, SECRET)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="metrics"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_fulltext_in_report_rejected(tmp_path: Path) -> None:
    report = _signed_report(extra={"note": "NOVEL_FULLTEXT_BEGIN\nlong novel..."})
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="fulltext"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_quality_comparable_false_rejected(tmp_path: Path) -> None:
    report = _signed_report(quality_comparable=False)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="quality_comparable"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)


def test_commit_requires_prepared(tmp_path: Path) -> None:
    bad = {"status": "draft", "schema_version": "baseline-prepare.v1", "metrics": _metrics()}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="prepared"):
        pb.commit(path, POLICY_PATH, tmp_path / "b.json", secret=SECRET)


def test_alert_allowed_matrix() -> None:
    assert pb.alert_allowed(event_name="schedule", ref="refs/heads/main", is_fork=False)
    assert pb.alert_allowed(event_name="push", ref="refs/heads/main", is_fork=False)
    assert not pb.alert_allowed(event_name="pull_request", ref="refs/heads/main", is_fork=False)
    assert not pb.alert_allowed(event_name="schedule", ref="refs/heads/main", is_fork=True)
    assert not pb.alert_allowed(event_name="push", ref="refs/heads/feature", is_fork=False)


def test_alert_dedup() -> None:
    fp = "deadbeef"
    existing = pb.dedupe_alert(
        fingerprint=fp,
        open_issue_titles=["[quality-alert] Nightly failure (deadbeef)"],
    )
    assert existing is not None
    assert pb.dedupe_alert(fingerprint=fp, open_issue_titles=["other"]) is None


def test_cli_prepare_commit(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_signed_report()), encoding="utf-8")
    prep = tmp_path / "prep.json"
    base = tmp_path / "base.json"
    rc = pb.main(
        [
            "prepare",
            "--report",
            str(report_path),
            "--policy",
            str(POLICY_PATH),
            "--out",
            str(prep),
            "--secret",
            SECRET,
        ]
    )
    assert rc == 0
    rc = pb.main(
        [
            "commit",
            "--prepared",
            str(prep),
            "--policy",
            str(POLICY_PATH),
            "--baseline-out",
            str(base),
            "--secret",
            SECRET,
        ]
    )
    assert rc == 0


def test_repeats_gate(tmp_path: Path) -> None:
    report = _signed_report(repeats=1)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(pb.BaselinePromotionError, match="repeats"):
        pb.prepare(path, POLICY_PATH, tmp_path / "p.json", secret=SECRET)
