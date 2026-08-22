"""Adversarial payload validation and Judge calibration (former sections of
rag_fixture.py). Only test files import these entry points — no app
production caller uses them directly.
"""

from __future__ import annotations

import hmac
import json
from pathlib import Path
from typing import Any, Callable

from app.schemas.eval import (
    SCHEMA_VERSION_RAG_QUALITY,
    CalibrationCase,
    CalibrationReport,
    CalibrationSuite,
    EvalCase,
    FailClosedResult,
    ModelLineage,
    SourceSnapshot,
)

from ._hashing import (
    fail_closed,
    schema_contract_hash,
    sign_payload,
    stable_hash,
    verify_signature,
)
from .core import (
    DEFAULT_SIGNING_SECRET,
    InvalidLineageError,
    _all_refs,
    verify_evidence_ref,
)

MAX_QUESTION_LEN = 4000
MAX_QUOTE_LEN = 2000
ADVERSARIAL_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all prior",
    "system:",
    "you are now",
    "<|im_start|>",
    "### instruction",
)
SCHEMA_SMUGGLING_KEYS = (
    "__proto__",
    "constructor",
    "$ref",
    "metrics",
    "quality_comparable",
    "status",
    "signature",
)

CalibrationJudgeFn = Callable[
    [CalibrationCase, ModelLineage], str
]  # returns predicted gold_verdict label


# ---------------------------------------------------------------------------
# Adversarial validation
# ---------------------------------------------------------------------------


def _contains_injection(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in ADVERSARIAL_INJECTION_MARKERS)


def _find_smuggled_keys(obj: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if key in SCHEMA_SMUGGLING_KEYS and path != "":
                # top-level status/signature/metrics are legitimate on envelopes
                found.append(f"{path}.{key}" if path else key)
            if key.startswith("__") or key in {"__proto__", "constructor"}:
                found.append(f"{path}.{key}" if path else key)
            found.extend(_find_smuggled_keys(v, f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_find_smuggled_keys(item, f"{path}[{i}]"))
    return found


def validate_adversarial_payload(
    payload: dict[str, Any],
    *,
    expected_owner_id: int | None = None,
    snapshot: SourceSnapshot | None = None,
) -> FailClosedResult | None:
    """Return FailClosedResult if adversarial/policy violation; else None (ok)."""

    # Length / oversize
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) > 500_000:
        return fail_closed(
            "invalid_fixture", "oversize payload", detail={"bytes": len(raw)}
        )

    question = str(payload.get("question") or "")
    if len(question) > MAX_QUESTION_LEN:
        return fail_closed(
            "invalid_fixture",
            "oversize question",
            detail={"length": len(question)},
        )

    # Instruction injection in question or quotes
    texts_to_scan = [question]
    for claim in payload.get("claims") or []:
        if isinstance(claim, dict):
            texts_to_scan.append(str(claim.get("text") or ""))
    for es in payload.get("equivalent_evidence_sets") or []:
        if not isinstance(es, dict):
            continue
        for ref in es.get("refs") or []:
            if isinstance(ref, dict) and ref.get("quote_text"):
                texts_to_scan.append(str(ref["quote_text"]))
                if len(str(ref["quote_text"])) > MAX_QUOTE_LEN:
                    return fail_closed(
                        "invalid_fixture",
                        "oversize quote",
                        detail={"length": len(str(ref["quote_text"]))},
                    )
    for text in texts_to_scan:
        if _contains_injection(text):
            return fail_closed(
                "failed_policy",
                "instruction injection detected",
                detail={"snippet": text[:80]},
            )

    # Schema smuggling inside nested case body (not top-level envelope)
    nested = {
        k: v
        for k, v in payload.items()
        if k
        not in {"status", "signature", "metrics", "quality_comparable", "suite_type"}
    }
    smuggled = _find_smuggled_keys(nested)
    # Allow legitimate nested fields named carefully — flag only dangerous ones
    dangerous = [
        s
        for s in smuggled
        if any(
            x in s
            for x in (
                "__proto__",
                "constructor",
                "$ref",
                ".metrics",
                ".quality_comparable",
                ".signature",
            )
        )
        or s.endswith(".status")
    ]
    if dangerous:
        return fail_closed(
            "invalid_fixture",
            "schema smuggling detected",
            detail={"keys": dangerous[:20]},
        )

    # Malicious quote/offset
    if snapshot is not None:
        try:
            case = EvalCase.model_validate(
                {
                    **payload,
                    "schema_version": payload.get(
                        "schema_version", SCHEMA_VERSION_RAG_QUALITY
                    ),
                    "snapshot_hash": payload.get(
                        "snapshot_hash", snapshot.manifest_hash
                    ),
                    "case_id": payload.get("case_id", "adv-check"),
                    "case_type": payload.get("case_type", "answerable"),
                    "question": question or "?",
                }
            )
        except Exception as exc:  # noqa: BLE001
            return fail_closed(
                "invalid_fixture",
                f"schema validation failed: {exc}",
            )
        for ref in _all_refs(case):
            if not verify_evidence_ref(ref, snapshot):
                return fail_closed(
                    "invalid_fixture",
                    "malicious quote/offset",
                    detail=ref.model_dump(),
                )

    # Cross-owner
    if expected_owner_id is not None:
        payload_owner = payload.get("owner_id")
        snap_owner = (
            snapshot.owner_id
            if snapshot is not None
            else payload.get("snapshot_owner_id")
        )
        if payload_owner is not None and int(payload_owner) != expected_owner_id:
            return fail_closed(
                "failed_policy",
                "cross-owner evidence rejected",
                detail={"expected": expected_owner_id, "got": payload_owner},
            )
        if snap_owner is not None and int(snap_owner) != expected_owner_id:
            return fail_closed(
                "failed_policy",
                "cross-owner snapshot rejected",
                detail={"expected": expected_owner_id, "got": snap_owner},
            )
        # Evidence hashes must belong to snapshot owned by expected owner
        if snapshot is not None and snapshot.owner_id != expected_owner_id:
            return fail_closed(
                "failed_policy",
                "cross-owner snapshot rejected",
                detail={"expected": expected_owner_id, "got": snapshot.owner_id},
            )

    return None


def load_adversarial_suite(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_adversarial_suite(
    suite: dict[str, Any],
    *,
    snapshot: SourceSnapshot | None = None,
    expected_owner_id: int | None = None,
) -> list[dict[str, Any]]:
    results = []
    for case in suite.get("cases", []):
        fail = validate_adversarial_payload(
            case,
            expected_owner_id=expected_owner_id
            if expected_owner_id is not None
            else suite.get("expected_owner_id"),
            snapshot=snapshot,
        )
        if fail is None:
            results.append(
                {
                    "case_id": case.get("case_id"),
                    "status": "unexpected_pass",
                    "metrics": None,
                    "quality_comparable": False,
                }
            )
        else:
            results.append(
                {
                    "case_id": case.get("case_id"),
                    "status": fail.status,
                    "metrics": fail.metrics,
                    "quality_comparable": fail.quality_comparable,
                    "reason": fail.reason,
                }
            )
    return results


# ---------------------------------------------------------------------------
# Calibration (D-15)
# ---------------------------------------------------------------------------


def calibration_suite_hash(suite_body: dict[str, Any]) -> str:
    return stable_hash(
        {k: v for k, v in suite_body.items() if k not in {"suite_hash", "signature"}}
    )


def freeze_calibration_suite(
    *,
    suite_id: str,
    domain: str,
    cases: list[CalibrationCase],
    prompt_hash: str,
    schema_hash: str | None = None,
    secret: str = DEFAULT_SIGNING_SECRET,
) -> CalibrationSuite:
    body = {
        "schema_version": SCHEMA_VERSION_RAG_QUALITY,
        "suite_id": suite_id,
        "domain": domain,
        "suite_type": "calibration",
        "cases": [c.model_dump() for c in cases],
        "prompt_hash": prompt_hash,
        "schema_hash": schema_hash or schema_contract_hash(),
    }
    sh = calibration_suite_hash(body)
    body["suite_hash"] = sh
    sig = sign_payload(body, secret)
    return CalibrationSuite(
        schema_version=SCHEMA_VERSION_RAG_QUALITY,
        suite_id=suite_id,
        domain=domain,
        cases=cases,
        suite_hash=sh,
        signature=sig,
        prompt_hash=prompt_hash,
        schema_hash=body["schema_hash"],
    )


def verify_calibration_suite(
    suite: CalibrationSuite, secret: str = DEFAULT_SIGNING_SECRET
) -> bool:
    body = {
        "schema_version": suite.schema_version,
        "suite_id": suite.suite_id,
        "domain": suite.domain,
        "suite_type": suite.suite_type,
        "cases": [c.model_dump() for c in suite.cases],
        "prompt_hash": suite.prompt_hash,
        "schema_hash": suite.schema_hash,
        "suite_hash": suite.suite_hash,
        "signature": suite.signature,
    }
    if not hmac.compare_digest(calibration_suite_hash(body), suite.suite_hash):
        return False
    return verify_signature(body, secret)


def assert_calibration_benchmark_isolation(
    calibration: CalibrationSuite | dict[str, Any],
    benchmark: dict[str, Any],
) -> None:
    """Calibration must use different fixture hash AND domain than benchmark."""
    if isinstance(calibration, CalibrationSuite):
        cal_hash = calibration.suite_hash
        cal_domain = calibration.domain
    else:
        cal_hash = calibration.get("suite_hash") or calibration.get("fixture_hash")
        cal_domain = calibration.get("domain")
    bench_hash = benchmark.get("suite_hash") or benchmark.get("fixture_hash")
    bench_domain = benchmark.get("domain")
    if not cal_hash or not bench_hash:
        raise InvalidLineageError("missing calibration or benchmark hash")
    if cal_hash == bench_hash:
        raise InvalidLineageError("calibration hash must differ from benchmark hash")
    if not cal_domain or not bench_domain:
        raise InvalidLineageError("missing calibration or benchmark domain")
    if cal_domain == bench_domain:
        raise InvalidLineageError(
            "calibration domain must differ from benchmark domain"
        )


def default_stub_calibration_judge(case: CalibrationCase, lineage: ModelLineage) -> str:
    """Oracle-aligned stub for offline tests (perfect judge)."""
    _ = lineage
    return case.gold_verdict


def run_judge_calibration(
    suite: CalibrationSuite,
    judge_lineage: ModelLineage,
    *,
    judge_fn: CalibrationJudgeFn | None = None,
    repeats: int = 3,
    secret: str = DEFAULT_SIGNING_SECRET,
    consistency_threshold: float = 0.80,
) -> CalibrationReport:
    """Run 3-repeat calibration; critical false accept must be 0; consistency>=0.80."""
    if not verify_calibration_suite(suite, secret):
        return CalibrationReport(
            suite_hash=suite.suite_hash,
            suite_signature=suite.signature,
            prompt_hash=suite.prompt_hash,
            schema_hash=suite.schema_hash,
            judge_lineage=judge_lineage,
            domain=suite.domain,
            repeats=repeats,
            confusion_matrix={},
            critical_false_accept=0,
            consistency=0.0,
            status="invalid_lineage",
            metrics=None,
            quality_comparable=False,
        )

    # Bind lineage to suite prompt/schema
    if (
        judge_lineage.prompt_hash != suite.prompt_hash
        or judge_lineage.schema_hash != suite.schema_hash
    ):
        return CalibrationReport(
            suite_hash=suite.suite_hash,
            suite_signature=suite.signature,
            prompt_hash=suite.prompt_hash,
            schema_hash=suite.schema_hash,
            judge_lineage=judge_lineage,
            domain=suite.domain,
            repeats=repeats,
            confusion_matrix={},
            critical_false_accept=0,
            consistency=0.0,
            status="invalid_lineage",
            metrics=None,
            quality_comparable=False,
        )

    if not judge_lineage.weights_revision:
        raise InvalidLineageError("judge weights/revision unresolved")

    fn = judge_fn or default_stub_calibration_judge
    labels: set[str] = set()
    confusion: dict[str, dict[str, int]] = {}
    per_case_predictions: list[list[str]] = []
    critical_false_accept = 0

    for case in suite.cases:
        labels.add(case.gold_verdict)
        preds: list[str] = []
        for _ in range(repeats):
            pred = fn(case, judge_lineage)
            preds.append(pred)
            labels.add(pred)
            gold = case.gold_verdict
            confusion.setdefault(gold, {})
            confusion[gold][pred] = confusion[gold].get(pred, 0) + 1
            # critical false accept: gold says reject-class but judge accepts
            reject_like = gold in {
                "reject",
                "unsupported",
                "contradictory",
                "no_answer",
                "hard_negative",
            }
            # Also treat category-based critical
            if case.critical and case.category in {
                "unsupported",
                "contradictory",
                "no_answer",
                "hard_negative",
            }:
                reject_like = True
            if reject_like and pred == "accept":
                critical_false_accept += 1
        per_case_predictions.append(preds)

    # consistency: fraction of cases where all repeats agree
    consistent_cases = sum(1 for preds in per_case_predictions if len(set(preds)) == 1)
    consistency = (
        consistent_cases / len(per_case_predictions) if per_case_predictions else 0.0
    )

    passed = critical_false_accept == 0 and consistency >= consistency_threshold
    status: str = "passed" if passed else "invalid_lineage"

    report = CalibrationReport(
        suite_hash=suite.suite_hash,
        suite_signature=suite.signature,
        prompt_hash=suite.prompt_hash,
        schema_hash=suite.schema_hash,
        judge_lineage=judge_lineage,
        domain=suite.domain,
        repeats=repeats,
        confusion_matrix=confusion,
        critical_false_accept=critical_false_accept,
        consistency=round(consistency, 4),
        status=status,  # type: ignore[arg-type]
        metrics=None
        if not passed
        else {
            "consistency": round(consistency, 4),
            "critical_false_accept": critical_false_accept,
            "n_cases": len(suite.cases),
            "repeats": repeats,
        },
        quality_comparable=False,
    )
    # Sign report
    payload = report.model_dump(by_alias=True)
    payload.pop("signature", None)
    report.signature = sign_payload(payload, secret)
    if not passed:
        report.metrics = None
    return report
