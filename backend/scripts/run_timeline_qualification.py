"""Production-backed Phase 08 timeline qualification.

Release qualification starts the production worker, reads its durable database
artifacts, and measures the spoiler-safe production query. Frozen corpus helpers
remain diagnostics only and cannot satisfy the release evidence gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CORPUS = ROOT / "evals" / "timeline_fiction.v1.json"
REQUIRED_TEST_COMMANDS = [
    "cd backend; pytest tests/unit/timeline tests/integration/timeline tests/adversarial/test_timeline_evidence.py -x",
    "cd frontend; npm test -- --run",
    "cd frontend; npm run build",
    "cd frontend; npm run test:e2e -- timeline-real.spec.ts",
    "pytest tests/ci/test_timeline_release_gate.py -x",
]


class CommandSpec(NamedTuple):
    """Code-owned subprocess definition; ``display`` is the report contract."""

    display: str
    cwd: Path
    argv: tuple[str, ...]


def _required_command_specs(repo_root: Path) -> tuple[CommandSpec, ...]:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    backend = repo_root / "backend"
    frontend = repo_root / "frontend"
    return (
        CommandSpec(
            REQUIRED_TEST_COMMANDS[0], backend,
            (sys.executable, "-m", "pytest", "tests/unit/timeline", "tests/integration/timeline", "tests/adversarial/test_timeline_evidence.py", "-x"),
        ),
        CommandSpec(REQUIRED_TEST_COMMANDS[1], frontend, (npm, "test", "--", "--run")),
        CommandSpec(REQUIRED_TEST_COMMANDS[2], frontend, (npm, "run", "build")),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[3], frontend,
            (npm, "run", "test:e2e", "--", "timeline-real.spec.ts"),
        ),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[4], repo_root,
            (sys.executable, "-m", "pytest", "tests/ci/test_timeline_release_gate.py", "-x"),
        ),
    )


def collect_command_results(command_specs: Iterable[CommandSpec]) -> list[dict[str, Any]]:
    """Execute argv directly and bind each result to its exact combined output."""
    results: list[dict[str, Any]] = []
    for spec in command_specs:
        try:
            completed = subprocess.run(
                list(spec.argv), cwd=spec.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
            )
            output = completed.stdout
            exit_code = completed.returncode
        except OSError as exc:
            output = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
            exit_code = 127
        results.append({
            "command": spec.display,
            "exit_code": exit_code,
            "output": output,
            "output_sha256": hashlib.sha256(output).hexdigest(),
        })
    return results


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else _canonical(value)).hexdigest()


def report_digest(report: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in report.items() if key != "report_sha256"})


def load_corpus(path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "dataset_version", "domain", "source_snapshot_hash", "hierarchy_build_id",
        "hierarchy_checksum", "prompt_hash", "schema_hash", "model_lineage",
        "version_lineage", "deferred_products_absent", "cases", "cross_chapter_groups",
        "operational_expectations",
    }
    if set(corpus) != required:
        raise ValueError(f"frozen corpus keys changed: {sorted(set(corpus) ^ required)}")
    if corpus["domain"] != "fiction" or len(corpus["cases"]) < 20 or len(corpus["cross_chapter_groups"]) < 10:
        raise ValueError("qualification corpus must remain fiction-only with 20 cases and 10 cross-chapter groups")
    return corpus


def run_offline_qualification(path: Path = DEFAULT_CORPUS, *, controls: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate frozen-corpus structure; this report is never release evidence."""
    corpus = load_corpus(path)
    controls = controls or {}
    cases = corpus["cases"]
    groups = corpus["cross_chapter_groups"]
    case_ids = {case["id"] for case in cases}
    evidence_ratio = sum(bool(case.get("evidence")) for case in cases) / len(cases)
    order_ratio = sum(
        (not group.get("story_order")
        or set(group.get("story_order", ())) <= set(group.get("members", ())))
        and set(group.get("members", ())) <= case_ids
        for group in groups
    ) / len(groups)
    gates = {
        "schema_validity": controls.get("schema_valid", True),
        "evidence_validity": controls.get("evidence_valid", True) and evidence_ratio == 1,
        "budget_complete": controls.get("budget_status", "completed") == "completed",
        "spoiler_safety": controls.get("spoiler_leaks", 0) == 0,
        "version_separation": not controls.get("active_candidate_merged", False),
        "fiction_only": corpus["domain"] == "fiction",
        "deferred_products_absent": set(corpus["deferred_products_absent"])
        == {"relationship_graph", "reader_ai", "clue_tracker", "history_corpus"},
    }
    qualified = all(gates.values())
    metrics = {
        "event_precision": evidence_ratio,
        "story_pairwise_accuracy": order_ratio,
        "duplicate_f1": sum("duplicate" in group for group in groups) / len(groups),
        "causal_precision": sum("causal" in case for case in cases) / len(cases),
    }
    report: dict[str, Any] = {
        "report_version": "timeline-corpus-diagnostic.v1",
        "dataset_version": corpus["dataset_version"],
        "fixture_sha256": _sha256(path.read_bytes()),
        "lineage": {key: corpus[key] for key in (
            "source_snapshot_hash", "hierarchy_build_id", "hierarchy_checksum",
            "prompt_hash", "schema_hash", "model_lineage", "version_lineage",
        )},
        "status": "qualified" if qualified else "failed_policy",
        "quality_comparable": qualified,
        "gates": gates,
        "metrics": metrics if qualified else None,
    }
    report["report_sha256"] = report_digest(report)
    return report


def run_live_qualification(path: Path = DEFAULT_CORPUS, *, chapter_runner=None, reconcile_runner=None) -> dict[str, Any]:
    """Legacy transport diagnostic; production release evidence comes from persisted attempts."""
    fixture_sha = _sha256(path.read_bytes())
    if chapter_runner is None or reconcile_runner is None:
        return {"status": "blocked_dependency", "quality_comparable": False, "metrics": None, "deployments": [], "fixture_sha256": fixture_sha}
    try:
        deployments = [chapter_runner(), reconcile_runner()]
    except (ConnectionError, TimeoutError, OSError) as exc:
        return {"status": "blocked_dependency", "quality_comparable": False, "metrics": None, "deployments": [], "reason": type(exc).__name__, "fixture_sha256": fixture_sha}
    valid = [item.get("tier") for item in deployments] == ["balanced", "quality"] and all(
        item.get("status") == "completed"
        and item.get("schema_valid") is True
        and item.get("evidence_valid") is True
        and item.get("spoiler_leaks") == 0
        and item.get("budget_status") == "completed"
        and all(item.get(key) for key in ("provider", "model", "revision"))
        for item in deployments
    )
    outage = any(item.get("status") in {"outage", "timeout", "unavailable"} for item in deployments)
    if not valid:
        return {"status": "blocked_dependency" if outage else "failed_policy", "quality_comparable": False, "metrics": None, "deployments": deployments, "fixture_sha256": fixture_sha}
    return {
        "status": "qualified", "quality_comparable": True, "deployments": deployments,
        "metrics": {
            "calls": len(deployments),
            "tokens": sum(int(item["tokens"]) for item in deployments),
            "cost_usd_total": round(sum(float(item["cost_usd"]) for item in deployments), 8),
            "latency_p95_ms": max(float(item["latency_ms"]) for item in deployments),
        },
        "fixture_sha256": fixture_sha,
    }


def _candidate_id(logical_event_id: str) -> str:
    return logical_event_id.split(":", 1)[-1]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _pairwise_accuracy(actual: list[str], expected: list[str]) -> float:
    if len(expected) < 2:
        return 1.0 if actual == expected else 0.0
    positions = {item: index for index, item in enumerate(actual)}
    correct = total = 0
    for left_index, left in enumerate(expected):
        for right in expected[left_index + 1:]:
            total += 1
            if left in positions and right in positions and positions[left] < positions[right]:
                correct += 1
    return _ratio(correct, total)


def _p95(values: Iterable[int | None]) -> float:
    measured = sorted(int(value) for value in values if value is not None)
    if not measured:
        return 0.0
    return float(measured[max(0, math.ceil(len(measured) * 0.95) - 1)])


def _spoiler_observation(default_view, full_view, cutoff: int | None) -> dict[str, Any]:
    """Compare independently queried default/full views against persisted progress."""
    default_ids = {event.id for event in (default_view.events if default_view else [])}
    full_events = list(full_view.events if full_view else [])
    future_ids = {
        event.id for event in full_events
        if cutoff is not None and event.narrative_chapter_number > cutoff
    }
    leaked_event_ids = sorted(default_ids & future_ids)
    leaked_edge_ids = sorted(
        [edge.source_event_id, edge.target_event_id]
        for edge in (default_view.causal_edges if default_view else [])
        if edge.source_event_id not in default_ids or edge.target_event_id not in default_ids
    )
    count_mismatches: list[str] = []
    if default_view is not None:
        participant_count = sum(len(event.participants) for event in default_view.events)
        expected = {
            "events": len(default_view.events),
            "participants": participant_count,
            "causal_edges": len(default_view.causal_edges),
        }
        actual = default_view.counts.model_dump()
        count_mismatches = sorted(key for key, value in expected.items() if actual.get(key) != value)
    return {
        "cutoff_chapter": cutoff,
        "default_event_ids": sorted(default_ids),
        "full_event_ids": sorted(event.id for event in full_events),
        "future_event_ids": sorted(future_ids),
        "leaked_event_ids": leaked_event_ids,
        "leaked_edge_ids": leaked_edge_ids,
        "count_mismatches": count_mismatches,
        "leak_count": len(leaked_event_ids) + len(leaked_edge_ids) + len(count_mismatches),
    }


def _evidence_rows(evidence) -> list[dict[str, Any]]:
    return [{
        "id": row.id,
        "event_id": row.event_id,
        "chapter_id": row.chapter_id,
        "evidence_id": row.evidence_id,
        "source_start": row.source_start,
        "source_end": row.source_end,
        "content_hash": row.content_hash,
    } for row in evidence]


def _command_results_valid(command_results: list[dict[str, Any]] | None) -> bool:
    if not isinstance(command_results, list) or len(command_results) != len(REQUIRED_TEST_COMMANDS):
        return False
    by_command = {item.get("command"): item for item in command_results}
    if set(by_command) != set(REQUIRED_TEST_COMMANDS):
        return False
    return all(
        item.get("exit_code") == 0
        and isinstance(item.get("output"), bytes)
        and isinstance(item.get("output_sha256"), str)
        and len(item["output_sha256"]) == 64
        and item["output_sha256"] == hashlib.sha256(item["output"]).hexdigest()
        for item in by_command.values()
    )


async def run_production_qualification(
    run_id: int,
    *,
    runtime,
    sessions,
    expected_event_ids: list[str],
    expected_story_order: list[str],
) -> dict[str, Any]:
    """Execute the production worker and score only persisted/query artifacts."""
    from sqlalchemy import func, select

    from app.models.analysis import AnalysisBudgetLedger, AnalysisChapterStage, AnalysisRun, AnalysisVersion, ModelCallAttempt
    from app.models.novel import Novel
    from app.models.timeline import MachineTimelineEvent, TimelineActivePointer, TimelineEvidenceRef
    from app.schemas.timeline import TimelineOrdering, TimelineVersionSource
    from app.services.timeline.query import _chapter_cutoff, build_version_view
    from app.services.timeline.worker import run_timeline_worker

    await run_timeline_worker(run_id, runtime=runtime)
    async with sessions() as session:
        run = await session.get(AnalysisRun, run_id)
        if run is None:
            raise ValueError(f"analysis run {run_id} does not exist")
        novel = await session.get(Novel, run.novel_id)
        version = await session.get(AnalysisVersion, run.version_id) if run.version_id else None
        pointer = await session.scalar(select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == run.owner_id,
            TimelineActivePointer.novel_id == run.novel_id,
        ))
        events = list((await session.scalars(select(MachineTimelineEvent).where(
            MachineTimelineEvent.version_id == run.version_id,
        ).order_by(MachineTimelineEvent.narrative_chapter_number, MachineTimelineEvent.narrative_index, MachineTimelineEvent.id))).all()) if run.version_id else []
        event_ids = [event.id for event in events]
        evidence = list((await session.scalars(select(TimelineEvidenceRef).where(
            TimelineEvidenceRef.event_id.in_(event_ids),
        ).order_by(TimelineEvidenceRef.event_id, TimelineEvidenceRef.id))).all()) if event_ids else []
        attempts = list((await session.scalars(select(ModelCallAttempt).where(
            ModelCallAttempt.run_id == run.id,
        ).order_by(ModelCallAttempt.id))).all())
        stages = list((await session.scalars(select(AnalysisChapterStage).where(
            AnalysisChapterStage.run_id == run.id,
            AnalysisChapterStage.status == "completed",
        ).order_by(AnalysisChapterStage.id))).all())
        ledger = await session.scalar(select(AnalysisBudgetLedger).where(AnalysisBudgetLedger.run_id == run.id))
        visible = await build_version_view(
            session, novel=novel, owner_id=run.owner_id,
            source=TimelineVersionSource.ACTIVE, ordering=TimelineOrdering.NARRATIVE,
            person=None, include_causal=True, request_full_book=False,
        ) if novel is not None else None
        cutoff = await _chapter_cutoff(session, novel) if novel is not None else None
        if novel is not None:
            # Qualification needs both production query modes without persisting a
            # disclosure preference to the user's novel. This session is rolled back.
            novel.reading_progress = {**(novel.reading_progress or {}), "timeline_full_book": True}
            await session.flush()
            full_visible = await build_version_view(
                session, novel=novel, owner_id=run.owner_id,
                source=TimelineVersionSource.ACTIVE, ordering=TimelineOrdering.NARRATIVE,
                person=None, include_causal=True, request_full_book=True,
            )
        else:
            full_visible = None

        normalized_ids = [_candidate_id(event.logical_event_id) for event in events]
        expected = set(expected_event_ids)
        actual = set(normalized_ids)
        matched = actual & expected
        story_ids = [_candidate_id(event.logical_event_id) for event in sorted(
            events,
            key=lambda event: (
                event.story_rank is None, event.story_rank,
                event.narrative_chapter_number, event.narrative_index, event.id,
            ),
        )]
        evidence_event_ids = {row.event_id for row in evidence}
        visible_ids = [_candidate_id(event.logical_event_id) for event in (visible.events if visible else [])]
        full_visible_ids = [_candidate_id(event.logical_event_id) for event in (full_visible.events if full_visible else [])]
        spoiler_observation = _spoiler_observation(visible, full_visible, cutoff)
        provider_attempts = [attempt for attempt in attempts if attempt.provider_request_id]
        metrics = {
            "event_precision": _ratio(len(matched), len(actual)),
            "event_recall": _ratio(len(matched), len(expected)),
            "story_pairwise_accuracy": _pairwise_accuracy(story_ids, expected_story_order),
            "evidence_coverage": _ratio(len(evidence_event_ids), len(events)),
            "spoiler_leaks": spoiler_observation["leak_count"],
            "provider_calls": len(provider_attempts),
            "cost_usd_total": round(sum(float(attempt.cost_usd or Decimal("0")) for attempt in provider_attempts), 8),
            "latency_p95_ms": _p95(attempt.latency_ms for attempt in provider_attempts),
        }
        raw_evidence = _evidence_rows(evidence)
        authority = {
            "run_id": run.id,
            "run_status": run.status,
            "version_id": run.version_id,
            "active_version_id": pointer.version_id if pointer else None,
            "manifest_checksum": version.manifest_checksum if version else None,
            "call_audit_ids": [attempt.id for attempt in attempts],
            "call_audit_states": [{
                "id": attempt.id,
                "status": attempt.status,
                "request_hash": attempt.request_hash,
                "response_hash": attempt.response_hash,
            } for attempt in attempts],
            "evidence_ref_ids": [row.id for row in evidence],
            "raw_evidence_sha256": _sha256(raw_evidence),
        }
        artifact = {
            "database_dialect": session.bind.dialect.name,
            "authority": authority,
            "run": {"id": run.id, "status": run.status, "version_id": run.version_id, "progress": run.progress},
            "version": None if version is None else {
                "id": version.id, "status": version.status,
                "source_snapshot_hash": version.source_snapshot_hash,
                "hierarchy_build_id": version.hierarchy_build_id,
                "hierarchy_checksum": version.hierarchy_checksum,
                "prompt_hash": version.prompt_hash, "schema_hash": version.schema_hash,
                "model_lineage": version.model_lineage,
                "manifest_checksum": version.manifest_checksum,
            },
            "active_pointer": None if pointer is None else {
                "version_id": pointer.version_id, "revision": pointer.revision,
                "manifest_checksum": pointer.manifest_checksum,
            },
            "counts": {
                "events": len(events), "evidence_refs": len(evidence),
                "model_attempts": len(attempts), "completed_stages": len(stages),
            },
            "events": [{
                "id": event.id, "logical_event_id": _candidate_id(event.logical_event_id),
                "chapter_number": event.narrative_chapter_number,
                "narrative_index": event.narrative_index, "story_rank": event.story_rank,
                "publication_status": event.publication_status,
            } for event in events],
            "evidence_refs": raw_evidence,
            "attempts": [{
                "id": attempt.id, "stage_key": attempt.stage_key,
                "attempt_number": attempt.attempt_number, "status": attempt.status,
                "provider_request_id": attempt.provider_request_id,
                "request_hash": attempt.request_hash, "response_hash": attempt.response_hash,
                "usage": attempt.usage, "cost_usd": str(attempt.cost_usd or 0),
                "latency_ms": attempt.latency_ms,
            } for attempt in attempts],
            "stages": [{
                "stage_key": stage.stage_key, "artifact_checksum": stage.artifact_checksum,
            } for stage in stages],
            "budget": None if ledger is None else {
                "settled_calls": ledger.settled_calls,
                "settled_input_tokens": ledger.settled_input_tokens,
                "settled_output_tokens": ledger.settled_output_tokens,
                "settled_cost_usd": str(ledger.settled_cost_usd),
                "reserved_calls": ledger.reserved_calls,
            },
            "visible_default_event_ids": visible_ids,
            "visible_full_event_ids": full_visible_ids,
            "spoiler_observation": spoiler_observation,
        }

    gates = {
        "worker_completed": artifact["run"]["status"] == "completed",
        "active_promoted": artifact["active_pointer"] is not None
        and artifact["active_pointer"]["version_id"] == artifact["run"]["version_id"],
        "production_artifacts": artifact["database_dialect"] == "postgresql"
        and bool(events) and len(evidence_event_ids) == len(events)
        and bool(stages) and bool(attempts) and ledger is not None,
        "call_audit": bool(provider_attempts)
        and all(attempt.status == "succeeded" and attempt.response_hash for attempt in provider_attempts),
        "budget_settled": ledger is not None and ledger.reserved_calls == 0
        and ledger.settled_calls == len(provider_attempts),
        "spoiler_safety": metrics["spoiler_leaks"] == 0
        and set(visible_ids) <= set(full_visible_ids)
        and (len(visible_ids) < len(full_visible_ids) if len(events) > 1 else True),
        "quality_thresholds": metrics["event_precision"] >= 0.9
        and metrics["event_recall"] >= 0.9
        and metrics["story_pairwise_accuracy"] >= 0.9
        and metrics["evidence_coverage"] == 1.0,
    }
    qualified = all(gates.values())
    report: dict[str, Any] = {
        "report_version": "timeline-production-qualification.v2",
        "status": "qualified" if qualified else "failed_policy",
        "quality_comparable": qualified,
        "artifact": artifact,
        "artifact_sha256": _sha256(artifact),
        "gates": gates,
        "metrics": metrics,
        "test_commands": REQUIRED_TEST_COMMANDS,
    }
    report["report_sha256"] = report_digest(report)
    return report


async def load_persisted_authority(sessions, authority_refs: dict[str, Any]) -> dict[str, Any]:
    """Re-read qualification identity from the authoritative database."""
    from sqlalchemy import select

    from app.models.analysis import AnalysisRun, AnalysisVersion, ModelCallAttempt
    from app.models.timeline import MachineTimelineEvent, TimelineActivePointer, TimelineEvidenceRef

    run_id = int(authority_refs["run_id"])
    version_id = int(authority_refs["version_id"])
    async with sessions() as session:
        run = await session.get(AnalysisRun, run_id)
        version = await session.get(AnalysisVersion, version_id)
        if run is None or version is None or run.version_id != version_id:
            return {}
        pointer = await session.scalar(select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == run.owner_id,
            TimelineActivePointer.novel_id == run.novel_id,
        ))
        attempts = list((await session.scalars(select(ModelCallAttempt).where(
            ModelCallAttempt.run_id == run_id,
        ).order_by(ModelCallAttempt.id))).all())
        evidence = list((await session.scalars(
            select(TimelineEvidenceRef)
            .join(MachineTimelineEvent, MachineTimelineEvent.id == TimelineEvidenceRef.event_id)
            .where(MachineTimelineEvent.version_id == version_id)
            .order_by(TimelineEvidenceRef.event_id, TimelineEvidenceRef.id)
        )).all())
        raw_evidence = _evidence_rows(evidence)
        return {
            "run_id": run.id,
            "run_status": run.status,
            "version_id": version.id,
            "active_version_id": pointer.version_id if pointer else None,
            "manifest_checksum": version.manifest_checksum,
            "call_audit_ids": [attempt.id for attempt in attempts],
            "call_audit_states": [{
                "id": attempt.id,
                "status": attempt.status,
                "request_hash": attempt.request_hash,
                "response_hash": attempt.response_hash,
            } for attempt in attempts],
            "evidence_ref_ids": [row.id for row in evidence],
            "raw_evidence_sha256": _sha256(raw_evidence),
        }


def verify_release_evidence(
    repo_root: Path,
    report_path: Path,
    *,
    observed_authority: dict[str, Any] | None = None,
    command_results: list[dict[str, Any]] | None = None,
    require_live: bool = False,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact = report.get("artifact")
    required_paths = {
        "migration": repo_root / "backend" / "migrations" / "versions" / "10_analysis_timeline_versions.py",
        "worker": repo_root / "backend" / "app" / "services" / "timeline" / "worker.py",
        "api": repo_root / "backend" / "app" / "api" / "timeline.py",
        "frontend": repo_root / "frontend" / "src" / "app" / "analysis" / "page.tsx",
        "real_qualification": repo_root / "backend" / "tests" / "integration" / "timeline" / "test_real_qualification.py",
    }
    checks = {name: path.is_file() for name, path in required_paths.items()}
    counts = artifact.get("counts", {}) if isinstance(artifact, dict) else {}
    pointer = artifact.get("active_pointer") if isinstance(artifact, dict) else None
    report_authority = artifact.get("authority") if isinstance(artifact, dict) else None
    checks.update({
        "production_report_version": report.get("report_version") == "timeline-production-qualification.v2",
        "production_artifact_signature": isinstance(artifact, dict)
        and report.get("artifact_sha256") == _sha256(artifact),
        "report_signature": report.get("report_sha256") == report_digest(report),
        "database_authority": isinstance(report_authority, dict)
        and observed_authority == report_authority,
        "command_output_attestation": _command_results_valid(command_results),
        "worker_completed": isinstance(artifact, dict)
        and artifact.get("run", {}).get("status") == "completed",
        "postgresql_authority": isinstance(artifact, dict)
        and artifact.get("database_dialect") == "postgresql",
        "active_promoted": bool(pointer)
        and pointer.get("version_id") == artifact.get("run", {}).get("version_id"),
        "persisted_rows": all(int(counts.get(key, 0)) > 0 for key in (
            "events", "evidence_refs", "model_attempts", "completed_stages",
        )),
        "offline_qualified": report.get("status") == "qualified"
        and report.get("quality_comparable") is True and report.get("metrics") is not None,
        "spoiler_safety": report.get("gates", {}).get("spoiler_safety") is True
        and report.get("metrics", {}).get("spoiler_leaks") == 0,
        "quality_thresholds": report.get("gates", {}).get("quality_thresholds") is True,
        "test_commands": report.get("test_commands") == REQUIRED_TEST_COMMANDS,
    })
    if require_live:
        live = report.get("live", {})
        checks["live_dual_model"] = live.get("status") == "qualified" \
            and live.get("quality_comparable") is True and live.get("metrics") is not None
    qualified = all(checks.values())
    return {"status": "qualified" if qualified else "blocked_release", "quality_comparable": qualified, "checks": checks}


async def verify_release_evidence_from_db(
    repo_root: Path,
    report_path: Path,
    *,
    sessions,
    command_results: list[dict[str, Any]],
    require_live: bool = False,
) -> dict[str, Any]:
    """Release entrypoint that independently resolves report references from DB."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact = report.get("artifact") if isinstance(report, dict) else None
    authority_refs = artifact.get("authority") if isinstance(artifact, dict) else None
    observed = await load_persisted_authority(sessions, authority_refs) if authority_refs else None
    return verify_release_evidence(
        repo_root,
        report_path,
        observed_authority=observed,
        command_results=command_results,
        require_live=require_live,
    )


def _public_command_evidence(command_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose attestations without leaking captured test or service output."""
    return [{
        "command": item["command"],
        "exit_code": item["exit_code"],
        "output_sha256": item["output_sha256"],
    } for item in command_results]


async def run_release_verification(
    repo_root: Path,
    report_path: Path,
    *,
    sessions,
    command_specs: Iterable[CommandSpec] | None = None,
    require_live: bool = False,
) -> dict[str, Any]:
    """Run commands and independently observe DB authority for one release verdict."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("artifact"), dict):
            raise ValueError("release report must contain an artifact object")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "blocked_release",
            "quality_comparable": False,
            "checks": {"well_formed_report": False},
            "command_results": [],
            "error": type(exc).__name__,
        }

    specs = tuple(command_specs) if command_specs is not None else _required_command_specs(repo_root)
    command_results = await asyncio.to_thread(collect_command_results, specs)
    try:
        verdict = await verify_release_evidence_from_db(
            repo_root,
            report_path,
            sessions=sessions,
            command_results=command_results,
            require_live=require_live,
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        verdict = {
            "status": "blocked_release",
            "quality_comparable": False,
            "checks": {"well_formed_report": False},
            "error": type(exc).__name__,
        }
    verdict["command_results"] = _public_command_evidence(command_results)
    return verdict


def render_markdown(report: dict[str, Any]) -> str:
    artifact = report["artifact"]
    metrics = report["metrics"]
    lines = [
        "# Phase 08 Qualification", "",
        f"**Release status: {report['status'].upper()}**", "",
        "Production worker and persisted PostgreSQL/SQLAlchemy artifacts are the qualification authority.", "",
        f"- Run: `{artifact['run']['id']}` / version `{artifact['run']['version_id']}` / `{artifact['run']['status']}`",
        f"- Artifact SHA-256: `{report['artifact_sha256']}`",
        f"- Report SHA-256: `{report['report_sha256']}`", "",
        "## Measured Production Artifacts", "",
        "| Artifact | Count |", "|---|---:|",
        f"| Persisted events | {artifact['counts']['events']} |",
        f"| Evidence refs | {artifact['counts']['evidence_refs']} |",
        f"| Model attempts | {artifact['counts']['model_attempts']} |",
        f"| Completed stages | {artifact['counts']['completed_stages']} |", "",
        "## Measured Metrics", "", "| Metric | Value |", "|---|---:|",
        f"| Event precision | {metrics['event_precision']:.3f} |",
        f"| Event recall | {metrics['event_recall']:.3f} |",
        f"| Story pairwise accuracy | {metrics['story_pairwise_accuracy']:.3f} |",
        f"| Evidence coverage | {metrics['evidence_coverage']:.3f} |",
        f"| Spoiler leaks | {metrics['spoiler_leaks']} |",
        f"| Provider calls | {metrics['provider_calls']} |",
        f"| Settled cost | ${metrics['cost_usd_total']:.8f} |",
        f"| p95 latency | {metrics['latency_p95_ms']:.1f} ms |", "",
        "## Gates", "",
    ]
    lines.extend(f"- {'PASS' if passed else 'FAIL'} — `{name}`" for name, passed in report["gates"].items())
    lines.extend(["", "## Required Test Commands", ""])
    lines.extend(f"- `{command}`" for command in report["test_commands"])
    lines.extend([
        "", "## Signed Raw Artifact", "",
        "The canonical JSON below hashes to the artifact SHA-256 above.", "",
        "```json", json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2), "```", "",
    ])
    return "\n".join(lines)


async def _run_cli(args) -> dict[str, Any]:
    from app.core.database import async_session_factory
    from app.services.timeline.worker import production_runtime

    expectations = json.loads(args.expectations.read_text(encoding="utf-8"))
    return await run_production_qualification(
        args.run_id, runtime=production_runtime(), sessions=async_session_factory,
        expected_event_ids=expectations["expected_event_ids"],
        expected_story_order=expectations["expected_story_order"],
    )


async def _run_release_cli(args) -> dict[str, Any]:
    from app.core.database import async_session_factory

    return await run_release_verification(
        ROOT.parent,
        args.report,
        sessions=async_session_factory,
        require_live=args.require_live,
    )


class _ControlledE2ETransport:
    def __init__(self, *, pause_on_second_extraction: bool = False) -> None:
        self.pause_on_second_extraction = pause_on_second_extraction
        self.extractions = 0

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        schema_name = kwargs["response_format"].__name__
        if schema_name == "TimelineExtraction":
            self.extractions += 1
            if self.pause_on_second_extraction and self.extractions == 2:
                raise ConnectionError("controlled partial checkpoint")
            payload = json.loads(kwargs["messages"][1]["content"])
            chapter_id = payload["scope"]["chapter_id"]
            evidence = payload["evidence"][0]
            first_chapter = evidence["source_start"] == 0
            content = {
                "events": [{
                    "candidate_id": f"browser-event-{chapter_id}",
                    "title": "第一批事件" if first_chapter else "后章隐藏事件",
                    "description": evidence["text"], "event_type": "plot",
                    "narrative_chapter_number": chapter_id, "narrative_index": 0,
                    "participants": [{"mention": "林墨" if first_chapter else "顾遥", "entity_id": None}],
                    "story_time": {"precision": "unknown"},
                    "evidence": [{
                        "chapter_id": chapter_id, "evidence_id": evidence["evidence_id"],
                        "source_start": evidence["source_start"], "source_end": evidence["source_end"],
                        "content_hash": evidence["content_hash"],
                    }],
                    "confidence": 0.95,
                }],
                "story_time_constraints": [],
            }
        else:
            content = {"duplicate_groups": [], "story_constraints": [], "causal_edges": []}
        return {
            "id": f"browser-request-{uuid.uuid4().hex}", "content": json.dumps(content),
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }


def _controlled_e2e_runtime(sessions, transport):
    from app.services.timeline.model_gateway import ModelDeployment, PostgresCallRepository, TimelineModelGateway
    from app.services.timeline.worker import TimelineWorkerRuntime

    deployment = lambda model: ModelDeployment(
        "controlled", model, "e2e-r1", True, Decimal("1"), Decimal("2"),
    )
    return TimelineWorkerRuntime(
        sessions=sessions,
        gateway=TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions)),
        extraction_deployment=deployment("balanced-browser"),
        reconciliation_deployment=deployment("quality-browser"),
    )


async def seed_browser_partial(username: str) -> dict[str, Any]:
    """Seed Phase 07 evidence and stop the production worker after one chapter."""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.analysis import AnalysisRun
    from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
    from app.models.novel import Chapter, Novel
    from app.models.user import User
    from app.services.timeline.worker import run_timeline_worker

    unique = uuid.uuid4().hex
    async with async_session_factory.begin() as session:
        owner = await session.scalar(select(User).where(User.username == username))
        if owner is None:
            raise ValueError(f"browser owner {username!r} does not exist")
        novel = Novel(owner_id=owner.id, title=f"真实时间线 {unique[:8]}", status="ready")
        session.add(novel)
        await session.flush()
        chapters = [
            Chapter(novel_id=novel.id, chapter_number=2, title="第二章", content="林墨在雨夜发现线索。"),
            Chapter(novel_id=novel.id, chapter_number=9, title="第九章", content="顾遥在后章揭示真相。"),
        ]
        session.add_all(chapters)
        await session.flush()
        # 结构树按 chapter_count 建章节范围（第 1..N 章），非连续章号需覆盖到最大章号，
        # 否则候选事件（第 2 章）会落在默认「第 1 章」范围之外而被筛掉
        novel.chapter_count = max(c.chapter_number for c in chapters)
        novel.word_count = sum(len(c.content) for c in chapters)
        novel.reading_progress = {"chapter_id": chapters[0].id, "progress_percent": 100}
        build = ChunkBuild(
            build_id=f"browser-{unique}", novel_id=novel.id, status="active",
            source_snapshot_hash=_sha256({"novel": novel.id, "source": unique}),
            manifest_checksum=_sha256({"novel": novel.id, "hierarchy": unique}),
            chunker_name="browser-e2e", chunker_version="1",
            chunker_config_hash=_sha256("browser-e2e"), collection_name="browser-e2e",
            is_candidate=False, immutable=True,
        )
        session.add(build)
        session.add(ChunkActivePointer(novel_id=novel.id, build_id=build.build_id, committed_at=datetime.now(UTC)))
        for index, chapter in enumerate(chapters):
            session.add(ChunkHierarchyNode(
                build_id=build.build_id, novel_id=novel.id,
                node_id=f"browser-evidence-{chapter.id}", level="evidence",
                chapter_id=chapter.id, chapter_number=chapter.chapter_number,
                parent_id=f"browser-scene-{chapter.id}", child_ids=[], content=chapter.content,
                content_hash=hashlib.sha256(chapter.content.encode()).hexdigest(),
                source_start=index * 100, source_end=index * 100 + len(chapter.content),
                chunk_type="paragraph", decision_lineage=[], order_index=index,
            ))
        run = AnalysisRun(owner_id=owner.id, novel_id=novel.id, status="pending", active_key="active")
        session.add(run)
        await session.flush()
        run_id, novel_id = run.id, novel.id

    runtime = _controlled_e2e_runtime(
        async_session_factory, _ControlledE2ETransport(pause_on_second_extraction=True),
    )
    await run_timeline_worker(run_id, runtime=runtime)
    async with async_session_factory.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run.status != "paused_dependency" or run.progress.get("completed_chapters") != 1:
            raise RuntimeError(f"controlled partial worker did not pause after one chapter: {run.status} {run.progress}")
        # Browser first-entry still calls start-or-resume. Hold a valid lease so its
        # production deployment cannot race the controlled provider checkpoint.
        run.lease_id = "browser-partial-hold"
        run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    return {"novel_id": novel_id, "run_id": run_id, "title": f"真实时间线 {unique[:8]}"}


async def resume_browser_timeline(run_id: int) -> dict[str, Any]:
    """Resume the same durable run with a controlled provider and promote it."""
    from app.core.database import async_session_factory
    from app.models.analysis import AnalysisRun
    from app.models.timeline import TimelineActivePointer
    from app.services.timeline.worker import run_timeline_worker
    from sqlalchemy import select

    async with async_session_factory.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None:
            raise ValueError(f"browser run {run_id} does not exist")
        run.status = "pending"
        run.status_reason = None
        run.lease_id = None
        run.lease_expires_at = None
    runtime = _controlled_e2e_runtime(async_session_factory, _ControlledE2ETransport())
    await run_timeline_worker(run_id, runtime=runtime)
    async with async_session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        pointer = await session.scalar(select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == run.owner_id,
            TimelineActivePointer.novel_id == run.novel_id,
        ))
        if run.status != "completed" or pointer is None or pointer.version_id != run.version_id:
            raise RuntimeError(f"browser run failed to promote: {run.status}")
        return {"novel_id": run.novel_id, "run_id": run.id, "version_id": run.version_id}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 08 production timeline qualification")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--expectations", type=Path)
    parser.add_argument("--verify-release", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--e2e-seed-user")
    parser.add_argument("--e2e-resume-run", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.e2e_seed_user:
        print("E2E_RESULT=" + json.dumps(asyncio.run(seed_browser_partial(args.e2e_seed_user))))
        return 0
    if args.e2e_resume_run is not None:
        print("E2E_RESULT=" + json.dumps(asyncio.run(resume_browser_timeline(args.e2e_resume_run))))
        return 0
    if args.verify_release:
        if args.report is None:
            parser.error("--report is required with --verify-release")
        verdict = asyncio.run(_run_release_cli(args))
        rendered = json.dumps(verdict, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if verdict["status"] == "qualified" else 1
    if args.run_id is None or args.expectations is None:
        parser.error("--run-id and --expectations are required for qualification")
    report = asyncio.run(_run_cli(args))
    rendered = render_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
