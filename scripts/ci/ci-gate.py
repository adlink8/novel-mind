#!/usr/bin/env python3
"""Fail-closed CI aggregate gate (Phase 06-07 / D-13 / D-19).

Aggregates producer job results + optional artifact manifests for the
event-applicable matrix. Does NOT re-run tests or re-interpret quality scores.

Success only when every required producer reports a valid success result and
any provided artifact manifests pass hash/schema/staleness checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"

# Terminal GitHub Actions job results we understand.
SUCCESS = "success"
FAILURE = "failure"
CANCELLED = "cancelled"
SKIPPED = "skipped"
TIMED_OUT = "timed_out"

FAILING_RESULTS = frozenset({FAILURE, CANCELLED, TIMED_OUT})

# Event → required producers that must succeed (not skip).
# alert is intentionally excluded: it is a side-effect job on failure, not a
# merge/quality producer for the green path.
REQUIRED_BY_EVENT: dict[str, tuple[str, ...]] = {
    "pull_request": (
        "guard",
        "static",
        "unit",
        "openapi",
        "integration",
        "browser",
        "codeql",
        "workflow-lint",
    ),
    "push": (
        "guard",
        "static",
        "unit",
        "openapi",
        "integration",
        "browser",
        "codeql",
        "workflow-lint",
        "live",
    ),
    "schedule": (
        "guard",
        "static",
        "unit",
        "openapi",
        "integration",
        "browser",
        "codeql",
        "workflow-lint",
        "live",
        "nightly",
        "promote-baseline",
    ),
    "workflow_dispatch": (
        "guard",
        "static",
        "unit",
        "openapi",
        "integration",
        "browser",
        "codeql",
        "workflow-lint",
        "live",
    ),
}

# Producers that may legitimately be skipped for a given event.
EXPECTED_SKIP_BY_EVENT: dict[str, frozenset[str]] = {
    "pull_request": frozenset(
        {"live", "nightly", "promote-baseline", "alert"}
    ),
    "push": frozenset({"nightly", "promote-baseline", "alert"}),
    "schedule": frozenset({"alert"}),
    "workflow_dispatch": frozenset(
        {"nightly", "promote-baseline", "alert"}
    ),
}

# Artifact kinds that, when a producer is required and success, must have a
# valid manifest entry if an artifacts root is supplied.
ARTIFACT_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "unit": {
        "artifact_name": "unit-junit-coverage",
        "schema_version": "ci-artifact.v1",
        "required_when_success": True,
    },
    "openapi": {
        "artifact_name": "openapi-export",
        "schema_version": "ci-artifact.v1",
        "required_when_success": True,
    },
    "integration": {
        "artifact_name": "integration-results",
        "schema_version": "ci-artifact.v1",
        "required_when_success": True,
    },
    "nightly": {
        "artifact_name": "nightly-rag-report",
        "schema_version": "rag-quality.v1",
        "required_when_success": True,
        "require_signature": True,
    },
    "promote-baseline": {
        "artifact_name": "promoted-baseline",
        "schema_version": "baseline-policy.v1",
        "required_when_success": True,
    },
}

# Default max age for artifact manifests (seconds). Stale = fail closed.
DEFAULT_MAX_ARTIFACT_AGE_SECONDS = 6 * 60 * 60  # 6h within a single run envelope


class CiGateError(Exception):
    """Fail-closed aggregate gate error."""


@dataclass
class ProducerResult:
    name: str
    result: str
    reason: str | None = None
    artifact: dict[str, Any] | None = None


@dataclass
class GateVerdict:
    ok: bool
    event: str
    required: list[str]
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": "passed" if self.ok else "failed",
            "event": self.event,
            "required": list(self.required),
            "failures": list(self.failures),
            "details": dict(self.details),
            "gate": "ci-gate",
        }


def normalize_event(event: str) -> str:
    e = (event or "").strip().lower()
    aliases = {
        "pull_request": "pull_request",
        "pr": "pull_request",
        "push": "push",
        "push_protected_main": "push",
        "main": "push",
        "schedule": "schedule",
        "nightly": "schedule",
        "workflow_dispatch": "workflow_dispatch",
        "dispatch": "workflow_dispatch",
    }
    if e not in aliases:
        raise CiGateError(f"unsupported event: {event!r}")
    return aliases[e]


def required_producers(
    event: str,
    *,
    run_nightly: bool = False,
) -> list[str]:
    event = normalize_event(event)
    req = list(REQUIRED_BY_EVENT[event])
    if event == "workflow_dispatch" and run_nightly:
        for extra in ("nightly", "promote-baseline"):
            if extra not in req:
                req.append(extra)
    return req


def expected_skips(
    event: str,
    *,
    run_nightly: bool = False,
) -> frozenset[str]:
    event = normalize_event(event)
    skips = set(EXPECTED_SKIP_BY_EVENT.get(event, frozenset()))
    if event == "workflow_dispatch" and run_nightly:
        skips.discard("nightly")
        skips.discard("promote-baseline")
    return frozenset(skips)


def parse_producer_arg(spec: str) -> tuple[str, str]:
    """Parse 'name=result' producer CLI argument."""
    if "=" not in spec:
        raise CiGateError(f"producer must be name=result, got {spec!r}")
    name, result = spec.split("=", 1)
    name = name.strip()
    result = result.strip().lower()
    if not name or not result:
        raise CiGateError(f"invalid producer spec: {spec!r}")
    return name, result


def load_results_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CiGateError(f"missing results file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CiGateError(f"invalid results JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CiGateError("results JSON must be an object")
    return data


def _producer_map_from_payload(
    payload: dict[str, Any],
) -> dict[str, ProducerResult]:
    raw = payload.get("producers") or payload.get("results") or {}
    if not isinstance(raw, dict):
        raise CiGateError("producers must be a mapping")
    out: dict[str, ProducerResult] = {}
    for name, value in raw.items():
        if isinstance(value, str):
            out[str(name)] = ProducerResult(name=str(name), result=value.lower())
        elif isinstance(value, dict):
            result = str(value.get("result") or value.get("status") or "").lower()
            if not result:
                raise CiGateError(f"producer {name!r} missing result")
            out[str(name)] = ProducerResult(
                name=str(name),
                result=result,
                reason=value.get("reason"),
                artifact=value.get("artifact")
                if isinstance(value.get("artifact"), dict)
                else None,
            )
        else:
            raise CiGateError(f"producer {name!r} has invalid value type")
    return out


def load_artifact_manifests(artifacts_root: Path) -> dict[str, dict[str, Any]]:
    """Load **/manifest.json under artifacts_root keyed by producer or artifact name."""
    manifests: dict[str, dict[str, Any]] = {}
    if not artifacts_root.is_dir():
        raise CiGateError(f"artifacts root missing: {artifacts_root}")
    for path in artifacts_root.rglob("manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CiGateError(f"invalid artifact manifest {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise CiGateError(f"manifest must be object: {path}")
        key = str(
            data.get("producer")
            or data.get("artifact_name")
            or path.parent.name
        )
        manifests[key] = data
        # Also index by artifact_name for lookup flexibility.
        an = data.get("artifact_name")
        if an and str(an) not in manifests:
            manifests[str(an)] = data
    return manifests


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_artifact_manifest(
    producer: str,
    manifest: dict[str, Any],
    *,
    now: float | None = None,
    max_age_seconds: int = DEFAULT_MAX_ARTIFACT_AGE_SECONDS,
    expected_content_hash: str | None = None,
) -> list[str]:
    """Return list of failure reasons (empty if valid)."""
    failures: list[str] = []
    expect = ARTIFACT_EXPECTATIONS.get(producer, {})
    schema = manifest.get("schema_version")
    expected_schema = expect.get("schema_version") or "ci-artifact.v1"
    if schema != expected_schema:
        # Allow rag-quality.v1 for nightly reports that embed quality schema
        if not (
            producer == "nightly"
            and schema in ("rag-quality.v1", "ci-artifact.v1")
        ):
            failures.append(
                f"{producer}: schema mismatch got {schema!r} want {expected_schema!r}"
            )

    status = str(manifest.get("status") or "").lower()
    if status and status not in ("valid", "success", "passed", "ok", "qualified"):
        failures.append(f"{producer}: artifact status not valid ({status!r})")

    content_hash = manifest.get("content_hash") or manifest.get("sha256")
    if expected_content_hash is not None:
        if not content_hash:
            failures.append(f"{producer}: missing content_hash")
        elif str(content_hash) != str(expected_content_hash):
            failures.append(
                f"{producer}: content hash mismatch "
                f"got {content_hash!r} want {expected_content_hash!r}"
            )
    elif manifest.get("expected_content_hash"):
        if str(content_hash) != str(manifest["expected_content_hash"]):
            failures.append(f"{producer}: content hash mismatch vs expected_content_hash")

    produced_at = manifest.get("produced_at") or manifest.get("timestamp")
    if produced_at is not None:
        try:
            ts = float(produced_at)
        except (TypeError, ValueError):
            # ISO-ish: treat unparseable as stale/invalid
            failures.append(f"{producer}: unparseable produced_at {produced_at!r}")
            ts = None
        if ts is not None:
            ref = time.time() if now is None else float(now)
            age = ref - ts
            if age > max_age_seconds:
                failures.append(
                    f"{producer}: stale artifact age={age:.0f}s max={max_age_seconds}s"
                )
            if age < -60:
                failures.append(f"{producer}: artifact timestamp in the future")

    if expect.get("require_signature"):
        sig = (
            manifest.get("report_signature")
            or manifest.get("signature")
            or (manifest.get("report") or {}).get("report_signature")
        )
        if not sig:
            failures.append(f"{producer}: missing report signature")

    return failures


def evaluate_gate(
    *,
    event: str,
    producers: dict[str, ProducerResult],
    run_nightly: bool = False,
    artifacts: dict[str, dict[str, Any]] | None = None,
    now: float | None = None,
    max_artifact_age_seconds: int = DEFAULT_MAX_ARTIFACT_AGE_SECONDS,
    require_artifacts: bool = False,
) -> GateVerdict:
    """Evaluate aggregate gate. Fail closed on any required violation."""
    event_n = normalize_event(event)
    required = required_producers(event_n, run_nightly=run_nightly)
    skips_ok = expected_skips(event_n, run_nightly=run_nightly)
    failures: list[str] = []
    details: dict[str, Any] = {
        "producer_results": {n: p.result for n, p in producers.items()},
        "expected_skips": sorted(skips_ok),
        "run_nightly": run_nightly,
    }

    # Required producers must be present and success.
    for name in required:
        pr = producers.get(name)
        if pr is None:
            failures.append(f"{name}: missing producer result")
            continue
        if pr.result == SKIPPED:
            failures.append(f"{name}: unexpected skipped (required for {event_n})")
            continue
        if pr.result in FAILING_RESULTS:
            label = "timeout" if pr.result == TIMED_OUT else pr.result
            extra = f" ({pr.reason})" if pr.reason else ""
            failures.append(f"{name}: {label}{extra}")
            continue
        if pr.result != SUCCESS:
            failures.append(f"{name}: invalid result {pr.result!r}")
            continue

    # Non-required producers: failure/cancelled still fail the gate if they ran;
    # skipped is OK when expected; unexpected skip of unknown jobs is ignored.
    for name, pr in producers.items():
        if name in required:
            continue
        if pr.result in FAILING_RESULTS:
            # Optional jobs that ran and failed still fail the gate (no silent fail).
            label = "timeout" if pr.result == TIMED_OUT else pr.result
            failures.append(f"{name}: {label} (optional producer failed)")
            continue
        if pr.result == SKIPPED:
            if name not in skips_ok and name not in (
                "alert",
                "nightly",
                "promote-baseline",
                "live",
            ):
                # Unknown producer skipped — ignore
                continue
            if name not in skips_ok and name in ARTIFACT_EXPECTATIONS:
                failures.append(f"{name}: unexpected skipped")
            continue

    # Artifact validation
    if artifacts is not None or require_artifacts:
        arts = artifacts or {}
        for name in required:
            expect = ARTIFACT_EXPECTATIONS.get(name)
            if not expect or not expect.get("required_when_success"):
                continue
            pr = producers.get(name)
            if pr is None or pr.result != SUCCESS:
                continue
            # Prefer inline artifact on producer, then manifest index
            manifest = None
            if pr.artifact:
                manifest = pr.artifact
            else:
                artifact_name = expect.get("artifact_name")
                manifest = arts.get(name) or (
                    arts.get(str(artifact_name)) if artifact_name else None
                )
            if manifest is None:
                if require_artifacts or artifacts is not None:
                    failures.append(
                        f"{name}: missing/stale artifact "
                        f"(expected {expect.get('artifact_name')})"
                    )
                continue
            failures.extend(
                validate_artifact_manifest(
                    name,
                    manifest,
                    now=now,
                    max_age_seconds=max_artifact_age_seconds,
                )
            )

        # Also validate any inline producer.artifact entries
        for name, pr in producers.items():
            if pr.artifact and name in required and pr.result == SUCCESS:
                failures.extend(
                    validate_artifact_manifest(
                        name,
                        pr.artifact,
                        now=now,
                        max_age_seconds=max_artifact_age_seconds,
                    )
                )

    ok = len(failures) == 0
    return GateVerdict(
        ok=ok,
        event=event_n,
        required=required,
        failures=failures,
        details=details,
    )


def evaluate_from_payload(
    payload: dict[str, Any],
    *,
    artifacts: dict[str, dict[str, Any]] | None = None,
    require_artifacts: bool | None = None,
    now: float | None = None,
    max_artifact_age_seconds: int = DEFAULT_MAX_ARTIFACT_AGE_SECONDS,
) -> GateVerdict:
    event = str(payload.get("event") or payload.get("event_name") or "")
    run_nightly = bool(payload.get("run_nightly") or False)
    producers = _producer_map_from_payload(payload)
    if require_artifacts is None:
        require_artifacts = bool(payload.get("require_artifacts") or False)
    art = artifacts
    if art is None and isinstance(payload.get("artifacts"), dict):
        art = payload["artifacts"]  # type: ignore[assignment]
    return evaluate_gate(
        event=event,
        producers=producers,
        run_nightly=run_nightly,
        artifacts=art,
        now=now if now is not None else payload.get("now"),
        max_artifact_age_seconds=int(
            payload.get("max_artifact_age_seconds") or max_artifact_age_seconds
        ),
        require_artifacts=require_artifacts,
    )


def build_results_from_env_map(pairs: list[tuple[str, str]]) -> dict[str, ProducerResult]:
    return {
        name: ProducerResult(name=name, result=result.lower())
        for name, result in pairs
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NovelMind fail-closed ci-gate aggregate (06-07)"
    )
    parser.add_argument(
        "--event",
        help="GitHub event name (pull_request|push|schedule|workflow_dispatch)",
    )
    parser.add_argument(
        "--producer",
        action="append",
        default=[],
        metavar="NAME=RESULT",
        help="Producer result (repeatable). RESULT is success|failure|cancelled|skipped|timed_out",
    )
    parser.add_argument(
        "--results-json",
        type=Path,
        help="Path to results JSON (event + producers mapping)",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Directory of producer artifact manifests (**/manifest.json)",
    )
    parser.add_argument(
        "--require-artifacts",
        action="store_true",
        help="Fail if required producers lack artifact manifests",
    )
    parser.add_argument(
        "--run-nightly",
        action="store_true",
        help="workflow_dispatch with nightly enabled",
    )
    parser.add_argument(
        "--max-artifact-age-seconds",
        type=int,
        default=DEFAULT_MAX_ARTIFACT_AGE_SECONDS,
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write verdict JSON to this path",
    )
    args = parser.parse_args(argv)

    try:
        artifacts: dict[str, dict[str, Any]] | None = None
        if args.artifacts_dir is not None:
            artifacts = load_artifact_manifests(args.artifacts_dir)

        if args.results_json:
            payload = load_results_json(args.results_json)
            if args.event:
                payload["event"] = args.event
            if args.run_nightly:
                payload["run_nightly"] = True
            if args.producer:
                # Merge CLI producers over JSON
                base = _producer_map_from_payload(payload)
                for spec in args.producer:
                    n, r = parse_producer_arg(spec)
                    base[n] = ProducerResult(name=n, result=r)
                payload["producers"] = {
                    n: {"result": p.result, "reason": p.reason, "artifact": p.artifact}
                    for n, p in base.items()
                }
            verdict = evaluate_from_payload(
                payload,
                artifacts=artifacts,
                require_artifacts=args.require_artifacts
                or bool(payload.get("require_artifacts")),
                max_artifact_age_seconds=args.max_artifact_age_seconds,
            )
        else:
            if not args.event:
                raise CiGateError("--event is required without --results-json")
            if not args.producer:
                raise CiGateError("at least one --producer NAME=RESULT is required")
            pairs = [parse_producer_arg(s) for s in args.producer]
            producers = build_results_from_env_map(pairs)
            verdict = evaluate_gate(
                event=args.event,
                producers=producers,
                run_nightly=args.run_nightly,
                artifacts=artifacts,
                max_artifact_age_seconds=args.max_artifact_age_seconds,
                require_artifacts=args.require_artifacts,
            )
    except CiGateError as exc:
        print(f"[ci-gate] FAIL {exc}", file=sys.stderr)
        return 1

    blob = verdict.to_dict()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(blob, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    if verdict.ok:
        print(f"[ci-gate] PASS event={verdict.event} required={verdict.required}")
        return 0

    print(f"[ci-gate] FAIL event={verdict.event}", file=sys.stderr)
    for f in verdict.failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
