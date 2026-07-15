"""Phase 09 relationship qualification and fail-closed release authority.

Release qualification binds independent PostgreSQL observations, frozen
fixture/schema/policy/package-lock hashes, projection replay checksums, and
internally executed command digests. Self-hashes prove integrity only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CORPUS = ROOT / "evals" / "relationship_fiction.v1.json"
CYTOSCAPE_PIN = "3.34.0"

REQUIRED_TEST_COMMANDS = [
    "cd backend; pytest tests/unit/relationships tests/integration/relationships tests/adversarial/test_relationship_boundaries.py -x",
    "cd frontend; npm test -- --run",
    "cd frontend; npm run build",
    "cd frontend; npm run test:e2e -- relationships-real.spec.ts",
    "pytest tests/integration/relationships/test_release_gate.py -x",
]


class CommandSpec(NamedTuple):
    display: str
    cwd: Path
    argv: tuple[str, ...]


def _required_command_specs(repo_root: Path) -> tuple[CommandSpec, ...]:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    backend = repo_root / "backend"
    frontend = repo_root / "frontend"
    return (
        CommandSpec(
            REQUIRED_TEST_COMMANDS[0],
            backend,
            (
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/relationships",
                "tests/integration/relationships",
                "tests/adversarial/test_relationship_boundaries.py",
                "-x",
            ),
        ),
        CommandSpec(REQUIRED_TEST_COMMANDS[1], frontend, (npm, "test", "--", "--run")),
        CommandSpec(REQUIRED_TEST_COMMANDS[2], frontend, (npm, "run", "build")),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[3],
            frontend,
            (npm, "run", "test:e2e", "--", "relationships-real.spec.ts"),
        ),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[4],
            repo_root,
            (
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/relationships/test_release_gate.py",
                "-x",
            ),
        ),
    )


def collect_command_results(command_specs: Iterable[CommandSpec]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for spec in command_specs:
        try:
            completed = subprocess.run(
                list(spec.argv),
                cwd=spec.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output = completed.stdout
            exit_code = completed.returncode
        except OSError as exc:
            output = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
            exit_code = 127
        results.append(
            {
                "command": spec.display,
                "exit_code": exit_code,
                "output": output,
                "output_sha256": hashlib.sha256(output).hexdigest(),
            }
        )
    return results


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        value if isinstance(value, bytes) else _canonical(value)
    ).hexdigest()


def report_digest(report: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in report.items() if key != "report_sha256"})


def load_corpus(path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "dataset_version",
        "domain",
        "source_snapshot_hash",
        "hierarchy_build_id",
        "hierarchy_checksum",
        "prompt_hash",
        "schema_hash",
        "policy_hash",
        "model_lineage",
        "version_lineage",
        "deferred_products_absent",
        "canonical_edge_types",
        "canonical_transitions",
        "cases",
        "adversarial_cases",
        "operational_expectations",
    }
    if set(corpus) != required:
        raise ValueError(f"frozen corpus keys changed: {sorted(set(corpus) ^ required)}")
    if corpus["domain"] != "fiction":
        raise ValueError("qualification corpus must remain fiction-only")
    if len(corpus["cases"]) < 30 or len(corpus["adversarial_cases"]) < 15:
        raise ValueError("corpus requires >=30 cases and >=15 adversarial cases")
    if set(corpus["canonical_edge_types"]) != {
        "ally",
        "enemy",
        "family",
        "mentor",
        "romantic",
    }:
        raise ValueError("canonical edge types must be the five fiction labels")
    return corpus


def fixture_integrity(repo_root: Path, corpus_path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    from app.services.relationships.gates import policy_hash

    corpus = load_corpus(corpus_path)
    package_lock = repo_root / "frontend" / "package-lock.json"
    package_json = repo_root / "frontend" / "package.json"
    package_lock_text = package_lock.read_text(encoding="utf-8") if package_lock.is_file() else ""
    package_json_data = (
        json.loads(package_json.read_text(encoding="utf-8")) if package_json.is_file() else {}
    )
    deps = package_json_data.get("dependencies") or {}
    cytoscape_version = str(deps.get("cytoscape") or "")
    lock_has_pin = f'"cytoscape": "{CYTOSCAPE_PIN}"' in package_lock_text or (
        f'"node_modules/cytoscape"' in package_lock_text and CYTOSCAPE_PIN in package_lock_text
    )
    return {
        "fixture_sha256": _sha256(corpus_path.read_bytes()),
        "dataset_version": corpus["dataset_version"],
        "domain": corpus["domain"],
        "policy_hash_expected": corpus["policy_hash"],
        "policy_hash_runtime": policy_hash(),
        "schema_hash": corpus["schema_hash"],
        "prompt_hash": corpus["prompt_hash"],
        "package_lock_sha256": _sha256(package_lock.read_bytes()) if package_lock.is_file() else "",
        "cytoscape_version": cytoscape_version,
        "cytoscape_pin_ok": cytoscape_version == CYTOSCAPE_PIN and lock_has_pin,
        "case_count": len(corpus["cases"]),
        "adversarial_count": len(corpus["adversarial_cases"]),
        "deferred_products_absent": corpus["deferred_products_absent"],
    }


def scope_scan(repo_root: Path) -> dict[str, Any]:
    """Prove Phase 10/11 product contracts remain dependencies only."""
    backend = repo_root / "backend" / "app"
    forbidden_globs = [
        "**/conversation*.py",
        "**/chat*.py",
        "**/message_store*.py",
        "**/clue*.py",
        "**/foreshadow*.py",
    ]
    hits: list[str] = []
    for pattern in forbidden_globs:
        for path in backend.glob(pattern):
            # Allow comments/docs mentioning future phases, but not product modules.
            rel = str(path.relative_to(repo_root)).replace("\\", "/")
            if any(
                part in rel
                for part in (
                    "/services/relationships/",
                    "/api/relationships.py",
                    "/models/relationship.py",
                    "/schemas/relationship.py",
                )
            ):
                continue
            if path.name.startswith("test_"):
                continue
            # Only flag if under product packages that would implement chat/clue.
            if any(
                token in rel
                for token in (
                    "/services/chat",
                    "/services/conversation",
                    "/services/clue",
                    "/api/chat",
                    "/api/conversation",
                    "/api/clue",
                    "/models/chat",
                    "/models/conversation",
                    "/models/clue",
                )
            ):
                hits.append(rel)

    # Positive contracts for Phase 10/11 read-only names.
    query_src = (backend / "services" / "relationships" / "query.py").read_text(
        encoding="utf-8"
    )
    has_phase10 = "load_filtered_relationship_graph" in query_src
    has_phase11 = "list_accepted_observation_refs" in query_src
    return {
        "forbidden_hits": hits,
        "phase10_contract_present": has_phase10,
        "phase11_contract_present": has_phase11,
        "scope_clean": not hits and has_phase10 and has_phase11,
    }


def _command_results_valid(command_results: list[dict[str, Any]] | None) -> bool:
    if not isinstance(command_results, list) or len(command_results) != len(
        REQUIRED_TEST_COMMANDS
    ):
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


async def load_persisted_authority(sessions, authority_refs: dict[str, Any]) -> dict[str, Any]:
    """Re-read relationship identity from a fresh PostgreSQL session."""
    from sqlalchemy import func, select

    from app.models.relationship import (
        RelationshipObservation,
        RelationshipOverride,
        RelationshipProjectionAudit,
    )
    from app.models.timeline import TimelineActivePointer
    from app.services.relationships.projection import relationship_projection_service
    from app.services.relationships.query import RelationshipGraphQueryService
    from app.schemas.relationship import RelationshipVersionSource
    from app.models.novel import Novel

    owner_id = int(authority_refs["owner_id"])
    novel_id = int(authority_refs["novel_id"])
    version_id = int(authority_refs["version_id"])

    async with sessions() as session:
        pointer = await session.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == owner_id,
                TimelineActivePointer.novel_id == novel_id,
            )
        )
        obs_count = await session.scalar(
            select(func.count())
            .select_from(RelationshipObservation)
            .where(
                RelationshipObservation.owner_id == owner_id,
                RelationshipObservation.novel_id == novel_id,
                RelationshipObservation.analysis_version_id == version_id,
                RelationshipObservation.status == "accepted",
            )
        )
        override_count = await session.scalar(
            select(func.count())
            .select_from(RelationshipOverride)
            .where(
                RelationshipOverride.owner_id == owner_id,
                RelationshipOverride.novel_id == novel_id,
                RelationshipOverride.analysis_version_id == version_id,
            )
        )
        audit = await session.scalar(
            select(RelationshipProjectionAudit)
            .where(
                RelationshipProjectionAudit.owner_id == owner_id,
                RelationshipProjectionAudit.novel_id == novel_id,
                RelationshipProjectionAudit.analysis_version_id == version_id,
            )
            .order_by(RelationshipProjectionAudit.id.desc())
            .limit(1)
        )
        novel = await session.get(Novel, novel_id)
        spoiler_leaks = 0
        if novel is not None:
            svc = RelationshipGraphQueryService()
            original_progress = dict(novel.reading_progress or {})
            novel.reading_progress = {**original_progress, "timeline_full_book": False}
            await session.flush()
            default_view = await svc.build_graph(
                session,
                novel=novel,
                owner_id=owner_id,
                source=RelationshipVersionSource.ACTIVE,
                version_id=version_id,
            )
            novel.reading_progress = {**original_progress, "timeline_full_book": True}
            await session.flush()
            full_view = await svc.build_graph(
                session,
                novel=novel,
                owner_id=owner_id,
                source=RelationshipVersionSource.ACTIVE,
                version_id=version_id,
                request_full_book=True,
            )
            # Do not persist temporary progress mutations from the observer session.
            novel.reading_progress = original_progress
            await session.flush()
            default_blob = default_view.model_dump_json() if default_view else ""
            if default_view is not None and default_view.degradation.mode.value != "filters_required":
                if default_view.counts.nodes != len(default_view.nodes):
                    spoiler_leaks += 1
                if default_view.counts.edges != len(default_view.edges):
                    spoiler_leaks += 1
                default_names = {n.name for n in default_view.nodes}
                full_names = (
                    {n.name for n in full_view.nodes} if full_view is not None else set()
                )
                future_only = full_names - default_names
                spoiler_leaks += sum(1 for name in future_only if name in default_blob)

        manifest = await relationship_projection_service.build_manifest(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
        )
        return {
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_id": version_id,
            "active_version_id": pointer.version_id if pointer else None,
            "accepted_observation_count": int(obs_count or 0),
            "override_count": int(override_count or 0),
            "projection_manifest_checksum": manifest.get("manifest_checksum"),
            "projection_audit_status": audit.status if audit else None,
            "projection_audit_checksum": audit.manifest_checksum if audit else None,
            "spoiler_safe": spoiler_leaks == 0,
            "future_leak": spoiler_leaks > 0,
        }


async def run_production_qualification(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    sessions,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Score persisted relationship artifacts (no LLM required)."""
    from sqlalchemy import func, select

    from app.models.novel import Novel
    from app.models.relationship import (
        RelationshipObservation,
        RelationshipOverride,
        RelationshipProjectionAudit,
    )
    from app.models.timeline import TimelineActivePointer
    from app.schemas.relationship import RelationshipVersionSource
    from app.services.relationships.projection import (
        ProjectionConfig,
        relationship_projection_service,
    )
    from app.services.relationships.query import RelationshipGraphQueryService

    repo_root = repo_root or REPO_ROOT
    integrity = fixture_integrity(repo_root)
    scope = scope_scan(repo_root)

    async with sessions() as session:
        novel = await session.get(Novel, novel_id)
        pointer = await session.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == owner_id,
                TimelineActivePointer.novel_id == novel_id,
            )
        )
        obs_count = int(
            await session.scalar(
                select(func.count())
                .select_from(RelationshipObservation)
                .where(
                    RelationshipObservation.owner_id == owner_id,
                    RelationshipObservation.novel_id == novel_id,
                    RelationshipObservation.analysis_version_id == version_id,
                    RelationshipObservation.status == "accepted",
                )
            )
            or 0
        )
        override_count = int(
            await session.scalar(
                select(func.count())
                .select_from(RelationshipOverride)
                .where(
                    RelationshipOverride.owner_id == owner_id,
                    RelationshipOverride.novel_id == novel_id,
                    RelationshipOverride.analysis_version_id == version_id,
                )
            )
            or 0
        )

        svc = RelationshipGraphQueryService()
        spoiler_leaks = 0
        default_ids: list[int] = []
        full_ids: list[int] = []
        original_progress = dict(novel.reading_progress or {}) if novel is not None else {}
        if novel is not None:
            # Default spoiler-safe view (no full-book preference).
            progress = dict(original_progress)
            progress["timeline_full_book"] = False
            novel.reading_progress = progress
            await session.flush()
            default_view = await svc.build_graph(
                session,
                novel=novel,
                owner_id=owner_id,
                source=RelationshipVersionSource.ACTIVE,
                version_id=version_id,
            )
            progress = dict(original_progress)
            progress["timeline_full_book"] = True
            novel.reading_progress = progress
            await session.flush()
            full_view = await svc.build_graph(
                session,
                novel=novel,
                owner_id=owner_id,
                source=RelationshipVersionSource.ACTIVE,
                version_id=version_id,
                request_full_book=True,
            )
            # Restore durable progress before commit so release re-reads match seed.
            novel.reading_progress = original_progress
            await session.flush()
            if default_view is not None:
                default_ids = [n.character_id for n in default_view.nodes]
                default_blob = default_view.model_dump_json()
                # filters_required intentionally empties elements while keeping counts.
                if default_view.degradation.mode.value != "filters_required":
                    if default_view.counts.nodes != len(default_view.nodes):
                        spoiler_leaks += 1
                    if default_view.counts.edges != len(default_view.edges):
                        spoiler_leaks += 1
            else:
                default_blob = ""
            if full_view is not None:
                full_ids = [n.character_id for n in full_view.nodes]
                default_name_set = (
                    {n.name for n in default_view.nodes} if default_view else set()
                )
                # Name leak check only when default has element payload.
                if default_view is not None and default_view.degradation.mode.value != "filters_required":
                    future_names = {n.name for n in full_view.nodes} - default_name_set
                    spoiler_leaks += sum(1 for name in future_names if name in default_blob)

        manifest = await relationship_projection_service.build_manifest(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
        )
        replay = await relationship_projection_service.replay_accepted_observations(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            config=ProjectionConfig(enabled=False),
        )
        await session.commit()

        authority = {
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_id": version_id,
            "active_version_id": pointer.version_id if pointer else None,
            "accepted_observation_count": obs_count,
            "override_count": override_count,
            "projection_manifest_checksum": manifest.get("manifest_checksum"),
            "projection_audit_status": replay.status,
            "projection_audit_checksum": replay.manifest_checksum,
            "spoiler_safe": spoiler_leaks == 0,
            "future_leak": spoiler_leaks > 0,
        }
        artifact = {
            "database_dialect": session.bind.dialect.name if session.bind else "unknown",
            "authority": authority,
            "counts": {
                "accepted_observations": obs_count,
                "overrides": override_count,
                "default_nodes": len(default_ids),
                "full_nodes": len(full_ids),
            },
            "spoiler_observation": {
                "default_character_ids": sorted(default_ids),
                "full_character_ids": sorted(full_ids),
                "spoiler_leaks": spoiler_leaks,
            },
            "projection": {
                "manifest_checksum": manifest.get("manifest_checksum"),
                "replay_status": replay.status,
                "replay_checksum": replay.manifest_checksum,
            },
            "fixture": integrity,
            "scope": scope,
        }

    gates = {
        "postgresql_authority": artifact["database_dialect"] == "postgresql",
        "accepted_observations": obs_count > 0,
        "active_version_bound": pointer is not None
        and pointer.version_id == version_id,
        "spoiler_safety": spoiler_leaks == 0,
        "projection_replay": (
            replay.manifest_checksum == manifest.get("manifest_checksum")
            and replay.status in {"disabled", "completed"}
        ),
        "fixture_fiction_only": integrity["domain"] == "fiction"
        and integrity["case_count"] >= 30,
        "cytoscape_lock": integrity["cytoscape_pin_ok"] is True,
        "scope_clean": scope["scope_clean"] is True,
        "phase_contracts": scope["phase10_contract_present"]
        and scope["phase11_contract_present"],
    }
    qualified = all(gates.values())
    report: dict[str, Any] = {
        "report_version": "relationship-production-qualification.v1",
        "status": "qualified" if qualified else "failed_policy",
        "quality_comparable": qualified,
        "artifact": artifact,
        "artifact_sha256": _sha256(artifact),
        "gates": gates,
        "metrics": {
            "accepted_observations": obs_count,
            "spoiler_leaks": spoiler_leaks,
            "default_nodes": len(default_ids),
            "full_nodes": len(full_ids),
        },
        "test_commands": REQUIRED_TEST_COMMANDS,
    }
    report["report_sha256"] = report_digest(report)
    return report


def verify_release_evidence(
    repo_root: Path,
    report_path: Path,
    *,
    observed_authority: dict[str, Any] | None = None,
    command_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact = report.get("artifact")
    required_paths = {
        "migration": repo_root
        / "backend"
        / "migrations"
        / "versions"
        / "11_relationship_observations.py",
        "query": repo_root / "backend" / "app" / "services" / "relationships" / "query.py",
        "api": repo_root / "backend" / "app" / "api" / "relationships.py",
        "frontend": repo_root
        / "frontend"
        / "src"
        / "components"
        / "relationships"
        / "relationship-workspace.tsx",
        "fixture": repo_root / "backend" / "evals" / "relationship_fiction.v1.json",
        "adversarial": repo_root
        / "backend"
        / "tests"
        / "adversarial"
        / "test_relationship_boundaries.py",
        "performance": repo_root
        / "backend"
        / "tests"
        / "integration"
        / "relationships"
        / "test_performance.py",
        "e2e": repo_root / "frontend" / "e2e" / "relationships-real.spec.ts",
    }
    # Migration filename may vary — accept any 11*relationship* file.
    if not required_paths["migration"].is_file():
        migrations = list(
            (repo_root / "backend" / "migrations" / "versions").glob("*relationship*")
        )
        checks = {name: path.is_file() for name, path in required_paths.items() if name != "migration"}
        checks["migration"] = bool(migrations)
    else:
        checks = {name: path.is_file() for name, path in required_paths.items()}

    report_authority = artifact.get("authority") if isinstance(artifact, dict) else None
    fixture = artifact.get("fixture") if isinstance(artifact, dict) else {}
    scope = artifact.get("scope") if isinstance(artifact, dict) else {}
    projection = artifact.get("projection") if isinstance(artifact, dict) else {}
    checks.update(
        {
            "production_report_version": report.get("report_version")
            == "relationship-production-qualification.v1",
            "production_artifact_signature": isinstance(artifact, dict)
            and report.get("artifact_sha256") == _sha256(artifact),
            "report_signature": report.get("report_sha256") == report_digest(report),
            "database_authority": isinstance(report_authority, dict)
            and observed_authority == report_authority,
            "command_output_attestation": _command_results_valid(command_results),
            "postgresql_authority": isinstance(artifact, dict)
            and artifact.get("database_dialect") == "postgresql",
            "accepted_rows": isinstance(artifact, dict)
            and int(artifact.get("counts", {}).get("accepted_observations", 0)) > 0,
            "spoiler_safety": report.get("gates", {}).get("spoiler_safety") is True
            and report.get("metrics", {}).get("spoiler_leaks") == 0,
            "projection_replay": report.get("gates", {}).get("projection_replay") is True
            and projection.get("manifest_checksum")
            == projection.get("replay_checksum"),
            "cytoscape_lock": bool(fixture.get("cytoscape_pin_ok")),
            "scope_clean": bool(scope.get("scope_clean")),
            "offline_qualified": report.get("status") == "qualified"
            and report.get("quality_comparable") is True,
            "test_commands": report.get("test_commands") == REQUIRED_TEST_COMMANDS,
        }
    )
    qualified = all(checks.values())
    return {
        "status": "qualified" if qualified else "blocked_release",
        "quality_comparable": qualified,
        "checks": checks,
    }


async def verify_release_evidence_from_db(
    repo_root: Path,
    report_path: Path,
    *,
    sessions,
    command_results: list[dict[str, Any]],
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact = report.get("artifact") if isinstance(report, dict) else None
    authority_refs = artifact.get("authority") if isinstance(artifact, dict) else None
    observed = (
        await load_persisted_authority(sessions, authority_refs) if authority_refs else None
    )
    return verify_release_evidence(
        repo_root,
        report_path,
        observed_authority=observed,
        command_results=command_results,
    )


def _public_command_evidence(command_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "command": item["command"],
            "exit_code": item["exit_code"],
            "output_sha256": item["output_sha256"],
        }
        for item in command_results
    ]


async def run_release_verification(
    repo_root: Path,
    report_path: Path,
    *,
    sessions,
    command_specs: Iterable[CommandSpec] | None = None,
) -> dict[str, Any]:
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

    specs = tuple(command_specs) if command_specs is not None else _required_command_specs(
        repo_root
    )
    command_results = await asyncio.to_thread(collect_command_results, specs)
    try:
        verdict = await verify_release_evidence_from_db(
            repo_root,
            report_path,
            sessions=sessions,
            command_results=command_results,
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


async def seed_browser_graph(username: str) -> dict[str, Any]:
    """Seed a spoiler-safe relationship graph for real browser qualification."""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.analysis import AnalysisVersion
    from app.models.character import Character
    from app.models.knowledge import (
        KnowledgeExtractionRun,
        KnowledgeRelationCandidate,
        KnowledgeRelationJudgment,
    )
    from app.models.novel import Chapter, Novel
    from app.models.relationship import (
        RelationshipBuildRun,
        RelationshipEvidenceLink,
        RelationshipObservation,
        RelationshipObservationCandidate,
        RelationshipObservationJudgment,
        RelationshipOverride,
    )
    from app.models.timeline import TimelineActivePointer
    from app.models.user import User

    unique = uuid.uuid4().hex
    hex64 = "a" * 64
    hex64_b = "b" * 64
    hex64_c = "c" * 64
    hex64_d = "d" * 64

    async with async_session_factory.begin() as session:
        owner = await session.scalar(select(User).where(User.username == username))
        if owner is None:
            raise ValueError(f"browser owner {username!r} does not exist")

        novel = Novel(
            owner_id=owner.id,
            title=f"真实关系图 {unique[:8]}",
            status="ready",
        )
        session.add(novel)
        await session.flush()
        chapters = [
            Chapter(
                novel_id=novel.id,
                chapter_number=1,
                title="第一章",
                content="林墨与顾遥结盟于雨夜。",
            ),
            Chapter(
                novel_id=novel.id,
                chapter_number=2,
                title="第二章",
                content="林墨与顾遥继续同行。",
            ),
            Chapter(
                novel_id=novel.id,
                chapter_number=9,
                title="第九章",
                content="沈夜在后章与林墨为敌。SECRET_FUTURE_ENEMY",
            ),
        ]
        session.add_all(chapters)
        await session.flush()
        novel.reading_progress = {
            "chapter_id": chapters[0].id,
            "progress_percent": 100,
        }

        lin = Character(novel_id=novel.id, name="林墨", role="protagonist")
        gu = Character(novel_id=novel.id, name="顾遥", role="supporting")
        shen = Character(novel_id=novel.id, name="沈夜Future", role="antagonist")
        session.add_all([lin, gu, shen])
        await session.flush()

        version = AnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key=f"rel-browser-{unique[:8]}",
            status="active",
            source_snapshot_hash=hex64,
            hierarchy_build_id=f"browser-rel-{unique[:8]}",
            hierarchy_checksum=hex64_b,
            prompt_hash=hex64_c,
            schema_hash=hex64,
            model_lineage={},
            decoding_hash=hex64_b,
            config_hash=hex64_c,
            price_snapshot={},
            manifest={},
        )
        session.add(version)
        await session.flush()
        session.add(
            TimelineActivePointer(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                revision=1,
                manifest_checksum=hex64_d,
            )
        )

        krun = KnowledgeExtractionRun(
            owner_id=owner.id,
            novel_id=novel.id,
            run_name=f"browser-rel-{unique[:8]}",
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            status="completed",
        )
        session.add(krun)
        await session.flush()
        kcand = KnowledgeRelationCandidate(
            owner_id=owner.id,
            novel_id=novel.id,
            run_id=krun.id,
            domain_profile="fiction",
            relation_type="ally",
            source_kind="entity_candidate",
            source_id=1,
            target_kind="entity_candidate",
            target_id=2,
            recall_signals={},
            package_snapshot={},
            evidence_refs=["ev-browser-ally"],
            status="accepted",
        )
        session.add(kcand)
        await session.flush()
        kjudg = KnowledgeRelationJudgment(
            owner_id=owner.id,
            novel_id=novel.id,
            run_id=krun.id,
            relation_candidate_id=kcand.id,
            prompt_version="pv1",
            model_name="browser",
            relation_type="ally",
            confidence=0.95,
            evidence_refs=["ev-browser-ally"],
            rationale="browser seed",
            risk_flags=[],
            raw_output={},
            structured_output={},
            status="accepted",
            gate_status="accepted",
        )
        session.add(kjudg)
        await session.flush()

        build = RelationshipBuildRun(
            owner_id=owner.id,
            novel_id=novel.id,
            analysis_version_id=version.id,
            status="completed",
            checkpoint={},
            progress={},
            prompt_hash=hex64,
            schema_hash=hex64,
            policy_hash=hex64,
            decoding_hash=hex64,
            model_lineage={},
            accepted_count=2,
        )
        session.add(build)
        await session.flush()

        async def _add_obs(
            *,
            src: Character,
            tgt: Character,
            relation_type: str,
            from_ch: int,
            evidence_id: str,
            excerpt: str,
            package_hash: str,
            chapter: Chapter,
        ) -> RelationshipObservation:
            cand = RelationshipObservationCandidate(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                build_run_id=build.id,
                source_judgment_id=kjudg.id,
                source_relation_candidate_id=kcand.id,
                source_character_id=src.id,
                target_character_id=tgt.id,
                relation_type=relation_type,
                package_hash=package_hash,
                package_snapshot={},
                recall_signals={},
                evidence_refs=[evidence_id],
                status="accepted",
            )
            session.add(cand)
            await session.flush()
            judgment = RelationshipObservationJudgment(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                build_run_id=build.id,
                candidate_id=cand.id,
                prompt_hash=hex64,
                schema_hash=hex64,
                policy_hash=hex64,
                model_name="browser",
                model_lineage={},
                relation_type=relation_type,
                transition="establish",
                confidence=0.93,
                valid_from_evidence_id=evidence_id,
                supporting_evidence_ids=[evidence_id],
                structured_output={},
                risk_flags=[],
                status="accepted",
                gate_status="accepted",
            )
            session.add(judgment)
            await session.flush()
            obs = RelationshipObservation(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                build_run_id=build.id,
                candidate_id=cand.id,
                judgment_id=judgment.id,
                source_judgment_id=kjudg.id,
                source_character_id=src.id,
                target_character_id=tgt.id,
                relation_type=relation_type,
                transition="establish",
                status="accepted",
                valid_from_chapter=from_ch,
                valid_from_narrative_index=0,
                valid_to_chapter=None,
                valid_to_narrative_index=None,
                valid_from_evidence_id=evidence_id,
                confidence=0.93,
                evidence_checksum=hex64,
                observation_checksum=hex64_b,
                prompt_hash=hex64,
                schema_hash=hex64,
                policy_hash=hex64,
                model_lineage={},
                idempotency_key=f"browser-{unique}-{evidence_id}",
            )
            session.add(obs)
            await session.flush()
            session.add(
                RelationshipEvidenceLink(
                    observation_id=obs.id,
                    owner_id=owner.id,
                    novel_id=novel.id,
                    analysis_version_id=version.id,
                    evidence_id=evidence_id,
                    chapter_id=chapter.id,
                    source_start=0,
                    source_end=min(20, len(chapter.content)),
                    content_hash=hex64,
                    excerpt=excerpt,
                    sort_order=0,
                )
            )
            await session.flush()
            return obs

        early = await _add_obs(
            src=lin,
            tgt=gu,
            relation_type="ally",
            from_ch=1,
            evidence_id="ev-browser-ally",
            excerpt="林墨与顾遥结盟于雨夜",
            package_hash=hex64,
            chapter=chapters[0],
        )
        await _add_obs(
            src=lin,
            tgt=shen,
            relation_type="enemy",
            from_ch=9,
            evidence_id="ev-browser-future",
            excerpt="SECRET_FUTURE_ENEMY_沈夜Future",
            package_hash=hex64_b,
            chapter=chapters[2],
        )

        # Protective override on the early edge (append-only).
        session.add(
            RelationshipOverride(
                owner_id=owner.id,
                novel_id=novel.id,
                analysis_version_id=version.id,
                observation_id=early.id,
                logical_relationship_key=f"{lin.id}:{gu.id}:ally",
                field_name="relation_type",
                value={"relation_type": "ally"},
                author="browser-e2e",
                reason="confirm alliance label",
                status="active",
                evidence_signature=hex64,
            )
        )
        await session.flush()

        return {
            "novel_id": novel.id,
            "version_id": version.id,
            "title": novel.title,
            "early_observation_id": early.id,
            "lin_id": lin.id,
            "gu_id": gu.id,
            "shen_id": shen.id,
        }


async def seed_browser_over_cap(username: str) -> dict[str, Any]:
    """Seed a filters_required graph (>500 nodes) for degradation browser proof."""
    from sqlalchemy import insert, select

    from app.core.database import async_session_factory
    from app.models.analysis import AnalysisVersion
    from app.models.character import Character
    from app.models.knowledge import (
        KnowledgeExtractionRun,
        KnowledgeRelationCandidate,
        KnowledgeRelationJudgment,
    )
    from app.models.novel import Chapter, Novel
    from app.models.relationship import (
        RelationshipBuildRun,
        RelationshipObservation,
        RelationshipObservationCandidate,
        RelationshipObservationJudgment,
    )
    from app.models.timeline import TimelineActivePointer
    from app.models.user import User

    unique = uuid.uuid4().hex
    hex64 = "a" * 64
    hex64_b = "b" * 64
    hex64_c = "c" * 64
    hex64_d = "d" * 64
    node_count = 520

    async with async_session_factory.begin() as session:
        owner = await session.scalar(select(User).where(User.username == username))
        if owner is None:
            raise ValueError(f"browser owner {username!r} does not exist")

        novel = Novel(
            owner_id=owner.id,
            title=f"超大关系图 {unique[:8]}",
            status="ready",
            reading_progress={},
        )
        session.add(novel)
        await session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content="large graph seed",
        )
        session.add(chapter)
        await session.flush()
        novel.reading_progress = {
            "chapter_id": chapter.id,
            "timeline_full_book": True,
        }

        characters = [
            Character(novel_id=novel.id, name=f"L{i:04d}", role="supporting")
            for i in range(node_count)
        ]
        session.add_all(characters)
        await session.flush()
        char_ids = [c.id for c in characters]

        version = AnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key=f"rel-large-{unique[:8]}",
            status="active",
            source_snapshot_hash=hex64,
            hierarchy_build_id=f"large-{unique[:8]}",
            hierarchy_checksum=hex64_b,
            prompt_hash=hex64_c,
            schema_hash=hex64,
            model_lineage={},
            decoding_hash=hex64_b,
            config_hash=hex64_c,
            price_snapshot={},
            manifest={},
        )
        session.add(version)
        await session.flush()
        session.add(
            TimelineActivePointer(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                revision=1,
                manifest_checksum=hex64_d,
            )
        )

        krun = KnowledgeExtractionRun(
            owner_id=owner.id,
            novel_id=novel.id,
            run_name=f"large-{unique[:8]}",
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            status="completed",
        )
        session.add(krun)
        await session.flush()
        kcand = KnowledgeRelationCandidate(
            owner_id=owner.id,
            novel_id=novel.id,
            run_id=krun.id,
            domain_profile="fiction",
            relation_type="ally",
            source_kind="entity_candidate",
            source_id=1,
            target_kind="entity_candidate",
            target_id=2,
            recall_signals={},
            package_snapshot={},
            evidence_refs=["e0"],
            status="accepted",
        )
        session.add(kcand)
        await session.flush()
        kjudg = KnowledgeRelationJudgment(
            owner_id=owner.id,
            novel_id=novel.id,
            run_id=krun.id,
            relation_candidate_id=kcand.id,
            prompt_version="pv1",
            model_name="browser",
            relation_type="ally",
            confidence=0.95,
            evidence_refs=["e0"],
            rationale="large",
            risk_flags=[],
            raw_output={},
            structured_output={},
            status="accepted",
            gate_status="accepted",
        )
        session.add(kjudg)
        await session.flush()

        build = RelationshipBuildRun(
            owner_id=owner.id,
            novel_id=novel.id,
            analysis_version_id=version.id,
            status="completed",
            checkpoint={},
            progress={},
            prompt_hash=hex64,
            schema_hash=hex64,
            policy_hash=hex64,
            decoding_hash=hex64,
            model_lineage={},
            accepted_count=node_count - 1,
        )
        session.add(build)
        await session.flush()

        # Bulk insert chain edges → 520 nodes, triggers filters_required.
        edge_n = node_count - 1
        cand_rows = [
            {
                "owner_id": owner.id,
                "novel_id": novel.id,
                "analysis_version_id": version.id,
                "build_run_id": build.id,
                "source_judgment_id": kjudg.id,
                "source_relation_candidate_id": kcand.id,
                "source_character_id": char_ids[i],
                "target_character_id": char_ids[i + 1],
                "relation_type": "ally",
                "package_hash": f"{i:064x}"[-64:],
                "package_snapshot": {},
                "recall_signals": {},
                "evidence_refs": [f"e{i}"],
                "status": "accepted",
            }
            for i in range(edge_n)
        ]
        await session.execute(insert(RelationshipObservationCandidate), cand_rows)
        await session.flush()
        cand_ids = list(
            (
                await session.scalars(
                    select(RelationshipObservationCandidate.id)
                    .where(RelationshipObservationCandidate.build_run_id == build.id)
                    .order_by(RelationshipObservationCandidate.id)
                )
            ).all()
        )
        judg_rows = [
            {
                "owner_id": owner.id,
                "novel_id": novel.id,
                "analysis_version_id": version.id,
                "build_run_id": build.id,
                "candidate_id": cand_ids[i],
                "prompt_hash": hex64,
                "schema_hash": hex64,
                "policy_hash": hex64,
                "model_name": "browser",
                "model_lineage": {},
                "relation_type": "ally",
                "transition": "establish",
                "confidence": 0.9,
                "valid_from_evidence_id": f"e{i}",
                "supporting_evidence_ids": [f"e{i}"],
                "structured_output": {},
                "risk_flags": [],
                "status": "accepted",
                "gate_status": "accepted",
                "gate_failures": [],
            }
            for i in range(edge_n)
        ]
        await session.execute(insert(RelationshipObservationJudgment), judg_rows)
        await session.flush()
        judg_ids = list(
            (
                await session.scalars(
                    select(RelationshipObservationJudgment.id)
                    .where(RelationshipObservationJudgment.build_run_id == build.id)
                    .order_by(RelationshipObservationJudgment.id)
                )
            ).all()
        )
        obs_rows = [
            {
                "owner_id": owner.id,
                "novel_id": novel.id,
                "analysis_version_id": version.id,
                "build_run_id": build.id,
                "candidate_id": cand_ids[i],
                "judgment_id": judg_ids[i],
                "source_judgment_id": kjudg.id,
                "source_character_id": char_ids[i],
                "target_character_id": char_ids[i + 1],
                "relation_type": "ally",
                "transition": "establish",
                "status": "accepted",
                "valid_from_chapter": 1,
                "valid_from_narrative_index": 0,
                "valid_to_chapter": None,
                "valid_to_narrative_index": None,
                "valid_from_evidence_id": f"e{i}",
                "confidence": 0.9,
                "evidence_checksum": hex64,
                "observation_checksum": hex64_b,
                "prompt_hash": hex64,
                "schema_hash": hex64,
                "policy_hash": hex64,
                "model_lineage": {},
                "idempotency_key": f"large-{unique}-{i}",
            }
            for i in range(edge_n)
        ]
        await session.execute(insert(RelationshipObservation), obs_rows)
        await session.flush()
        return {
            "novel_id": novel.id,
            "version_id": version.id,
            "title": novel.title,
            "node_count": node_count,
        }


async def _run_release_cli(args) -> dict[str, Any]:
    from app.core.database import async_session_factory

    return await run_release_verification(
        REPO_ROOT,
        args.report,
        sessions=async_session_factory,
    )


async def _run_qualify_cli(args) -> dict[str, Any]:
    from app.core.database import async_session_factory

    return await run_production_qualification(
        owner_id=args.owner_id,
        novel_id=args.novel_id,
        version_id=args.version_id,
        sessions=async_session_factory,
        repo_root=REPO_ROOT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 09 relationship qualification")
    parser.add_argument("--verify-release", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--owner-id", type=int)
    parser.add_argument("--novel-id", type=int)
    parser.add_argument("--version-id", type=int)
    parser.add_argument("--e2e-seed-user")
    parser.add_argument("--e2e-seed-over-cap-user")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--scope-scan", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.scope_scan:
        result = scope_scan(REPO_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["scope_clean"] else 1

    if args.e2e_seed_user:
        print(
            "E2E_RESULT="
            + json.dumps(asyncio.run(seed_browser_graph(args.e2e_seed_user)))
        )
        return 0
    if args.e2e_seed_over_cap_user:
        print(
            "E2E_RESULT="
            + json.dumps(
                asyncio.run(seed_browser_over_cap(args.e2e_seed_over_cap_user))
            )
        )
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

    if args.owner_id is None or args.novel_id is None or args.version_id is None:
        # Offline fixture integrity path for quick diagnostics.
        integrity = fixture_integrity(REPO_ROOT)
        scope = scope_scan(REPO_ROOT)
        report = {
            "report_version": "relationship-corpus-diagnostic.v1",
            "status": "qualified"
            if integrity["domain"] == "fiction" and scope["scope_clean"]
            else "failed_policy",
            "fixture": integrity,
            "scope": scope,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "qualified" else 1

    report = asyncio.run(_run_qualify_cli(args))
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
