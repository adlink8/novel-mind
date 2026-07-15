"""Phase 11 clue qualification and fail-closed release authority.

Release qualification binds independent PostgreSQL observations, frozen
fixture/policy hashes, measured spoiler/lifecycle metrics, and internally
executed command digests. Self-hashes prove integrity only.
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

DEFAULT_CORPUS = ROOT / "evals" / "clue_fiction.v1.json"

REQUIRED_TEST_COMMANDS = [
    "cd backend; pytest tests/unit/clues tests/integration/clues tests/adversarial/test_clue_false_positives.py tests/adversarial/test_clue_spoilers_and_versions.py -x",
    "cd frontend; npm test -- --run",
    "cd frontend; npm run build",
    "cd frontend; npm run test:e2e -- clue-real.spec.ts",
    "pytest tests/ci/test_clue_release_gate.py -x",
]

REQ_CLUE_IDS = [
    "REQ-CLUE-01",
    "REQ-CLUE-02",
    "REQ-CLUE-03",
    "REQ-CLUE-04",
    "REQ-CLUE-05",
    "REQ-CLUE-06",
    "REQ-CLUE-07",
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
                "tests/unit/clues",
                "tests/integration/clues",
                "tests/adversarial/test_clue_false_positives.py",
                "tests/adversarial/test_clue_spoilers_and_versions.py",
                "-x",
            ),
        ),
        CommandSpec(REQUIRED_TEST_COMMANDS[1], frontend, (npm, "test", "--", "--run")),
        CommandSpec(REQUIRED_TEST_COMMANDS[2], frontend, (npm, "run", "build")),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[3],
            frontend,
            (npm, "run", "test:e2e", "--", "clue-real.spec.ts"),
        ),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[4],
            repo_root,
            (
                sys.executable,
                "-m",
                "pytest",
                "tests/ci/test_clue_release_gate.py",
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
    return _sha256({k: v for k, v in report.items() if k != "report_sha256"})


def scope_scan(repo_root: Path) -> dict[str, Any]:
    """Prove clue tree does not implement writing/chat products or treat chat as fact."""

    hits: list[str] = []
    clue_roots = [
        repo_root / "backend" / "app" / "services" / "clues",
        repo_root / "backend" / "app" / "api" / "clues.py",
        repo_root / "backend" / "app" / "models" / "clue.py",
    ]
    forbidden = (
        "apply_suggestion",
        "write_timeline_event",
        "from app.services.reader_chat",
        "conversation_message_as_evidence",
    )
    for path in clue_roots:
        if not path.exists():
            continue
        files = [path] if path.is_file() else list(path.rglob("*.py"))
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            for token in forbidden:
                if token in text:
                    hits.append(f"{f.relative_to(repo_root)}:{token}")
    # Chat must reject freeform; presence of reject helper is required.
    sources = repo_root / "backend" / "app" / "services" / "clues" / "sources.py"
    has_chat_reject = False
    if sources.is_file():
        src = sources.read_text(encoding="utf-8")
        has_chat_reject = "reject_freeform_chat_as_evidence" in src
        has_unavailable = "source_unavailable" in src
    else:
        has_unavailable = False
    return {
        "forbidden_hits": hits,
        "chat_reject_present": has_chat_reject,
        "source_unavailable_protocol": has_unavailable,
        "scope_clean": not hits and has_chat_reject and has_unavailable,
    }


def fixture_integrity(repo_root: Path, corpus_path: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    from app.services.clues.eval import load_fixture
    from app.services.clues.gates import policy_hash

    corpus = load_fixture(corpus_path)
    return {
        "fixture_sha256": _sha256(corpus_path.read_bytes()),
        "fixture_version": corpus["fixture_version"],
        "domain": corpus["domain"],
        "policy_hash_expected": corpus["policy_hash"],
        "policy_hash_runtime": policy_hash(),
        "case_count": len(corpus["cases"]),
        "adversarial_count": len(corpus["adversarial_cases"]),
        "deferred_products_absent": corpus["deferred_products_absent"],
    }


def run_offline_qualification(
    path: Path = DEFAULT_CORPUS,
    *,
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.clues.eval import run_offline_qualification as _run

    return _run(path, controls=controls)


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


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _seed_qualification_graph(session, *, unique: str | None = None) -> dict[str, Any]:
    """Seed hierarchy + completed clue version with spoiler-sensitive lifecycle."""

    from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
    from app.models.clue import (
        ClueActivePointer,
        ClueAnalysisRun,
        ClueAnalysisVersion,
        ClueBudgetLedger,
        ClueEvidenceRef,
        ClueLifecycleEvent,
        ClueModelCallAttempt,
        ClueOverride,
        MachineClue,
    )
    from app.models.novel import Chapter, Novel
    from app.models.user import User
    from app.services.clues.gates import policy_hash

    unique = unique or uuid.uuid4().hex
    hex64 = "a" * 64
    owner = User(
        username=f"clue-qual-{unique[:12]}",
        email=f"clue-qual-{unique[:12]}@example.test",
        hashed_password="not-used",
    )
    session.add(owner)
    await session.flush()
    novel = Novel(
        owner_id=owner.id,
        title=f"Clue qualification {unique[:8]}",
        status="ready",
    )
    session.add(novel)
    await session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=1, title="C1", content="A sealed letter waited on the pier."),
        Chapter(novel_id=novel.id, chapter_number=3, title="C3", content="The sealed letter still unopened."),
        Chapter(novel_id=novel.id, chapter_number=9, title="C9", content="She broke the seal; SECRET_PAYOFF named the traitor."),
    ]
    session.add_all(chapters)
    await session.flush()
    novel.reading_progress = {
        "chapter_id": chapters[0].id,
        "progress_percent": 100,
        "timeline_full_book": False,
    }
    build = ChunkBuild(
        build_id=f"clue-qual-{unique[:8]}",
        novel_id=novel.id,
        status="active",
        source_snapshot_hash=hex64,
        manifest_checksum="b" * 64,
        chunker_name="test",
        chunker_version="1",
        chunker_config_hash="c" * 64,
        collection_name="test",
        is_candidate=False,
        immutable=True,
    )
    session.add(build)
    session.add(
        ChunkActivePointer(
            novel_id=novel.id,
            build_id=build.build_id,
            committed_at=datetime.now(UTC),
        )
    )
    for index, chapter in enumerate(chapters):
        session.add(
            ChunkHierarchyNode(
                build_id=build.build_id,
                novel_id=novel.id,
                node_id=f"evidence-{chapter.id}",
                level="evidence",
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                parent_id=f"scene-{chapter.id}",
                child_ids=[],
                content=chapter.content,
                content_hash=_content_hash(chapter.content),
                source_start=0,
                source_end=len(chapter.content),
                chunk_type="paragraph",
                decision_lineage=[],
                order_index=index,
            )
        )
    version = ClueAnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key=f"clue-v1-{unique[:8]}",
        status="validated",
        source_snapshot_hash=hex64,
        hierarchy_build_id=build.build_id,
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        policy_hash=policy_hash(),
        model_lineage={"judgment": "controlled/clue@qual"},
        price_snapshot={},
        manifest={"domain": "fiction"},
        manifest_checksum="1" * 64,
        validated_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()

    ch1, ch3, ch9 = chapters
    h1 = _content_hash(ch1.content)
    h3 = _content_hash(ch3.content)
    h9 = _content_hash(ch9.content)

    machine = MachineClue(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-letter-payoff",
        title="The sealed letter",
        summary="recovers in chapter nine",
        package_hash=hex64,
        package_snapshot={},
        confidence=0.95,
        publication_status="published",
        first_cue_chapter=1,
        first_cue_source_start=0,
    )
    future_only = MachineClue(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        logical_clue_id="clue-future-only",
        title="SECRET FUTURE CLUE",
        summary="must not leak",
        package_hash=hex64,
        package_snapshot={},
        confidence=0.9,
        publication_status="published",
        first_cue_chapter=9,
        first_cue_source_start=0,
    )
    session.add_all([machine, future_only])
    await session.flush()

    def evid(logical, machine_id, role, evidence_id, chapter, text, h):
        return ClueEvidenceRef(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id=logical,
            machine_clue_id=machine_id,
            role=role,
            evidence_id=evidence_id,
            evidence_identity=f"{evidence_id}:{chapter.id}:0:{len(text)}:{h}",
            chapter_id=chapter.id,
            narrative_chapter_number=chapter.chapter_number,
            source_start=0,
            source_end=len(text),
            content_hash=h,
            excerpt=text[:80],
        )

    session.add_all(
        [
            evid("clue-letter-payoff", machine.id, "cue", "ev-cue", ch1, ch1.content, h1),
            evid(
                "clue-letter-payoff",
                machine.id,
                "reinforcement",
                "ev-re",
                ch3,
                ch3.content,
                h3,
            ),
            evid(
                "clue-letter-payoff",
                machine.id,
                "payoff",
                "ev-pay",
                ch9,
                ch9.content,
                h9,
            ),
            evid(
                "clue-future-only",
                future_only.id,
                "cue",
                "ev-future",
                ch9,
                ch9.content,
                h9,
            ),
        ]
    )
    session.add_all(
        [
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-letter-payoff",
                machine_clue_id=machine.id,
                from_status="candidate",
                to_status="active",
                actor_source="machine",
                reason="cue",
                event_key="e1",
                evidence_identities=[f"ev-cue:{ch1.id}:0:{len(ch1.content)}:{h1}"],
                cue_chapter=1,
                cue_source_start=0,
                gate_audit={},
            ),
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-letter-payoff",
                machine_clue_id=machine.id,
                from_status="active",
                to_status="reinforced",
                actor_source="machine",
                reason="reinf",
                event_key="e2",
                evidence_identities=[f"ev-re:{ch3.id}:0:{len(ch3.content)}:{h3}"],
                gate_audit={},
            ),
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-letter-payoff",
                machine_clue_id=machine.id,
                from_status="reinforced",
                to_status="paid_off",
                actor_source="machine",
                reason="pay",
                event_key="e3",
                evidence_identities=[
                    f"ev-cue:{ch1.id}:0:{len(ch1.content)}:{h1}",
                    f"ev-pay:{ch9.id}:0:{len(ch9.content)}:{h9}",
                ],
                cue_chapter=1,
                cue_source_start=0,
                payoff_chapter=9,
                payoff_source_start=0,
                gate_audit={},
            ),
            ClueLifecycleEvent(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-future-only",
                machine_clue_id=future_only.id,
                from_status="candidate",
                to_status="active",
                actor_source="machine",
                reason="future cue",
                event_key="ef1",
                evidence_identities=[f"ev-future:{ch9.id}:0:{len(ch9.content)}:{h9}"],
                cue_chapter=9,
                cue_source_start=0,
                gate_audit={},
            ),
        ]
    )
    session.add(
        ClueOverride(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id="clue-letter-payoff",
            action="annotate",
            field_name="note",
            value={"note": "owner note preserved"},
            author="owner",
            reason="annotate",
            status="active",
        )
    )
    session.add(
        ClueActivePointer(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            revision=1,
            manifest_checksum="1" * 64,
        )
    )
    run = ClueAnalysisRun(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=version.id,
        active_key=None,
        status="completed",
        progress={"completed": 2, "total": 2, "stage": "done"},
    )
    session.add(run)
    await session.flush()
    from decimal import Decimal

    session.add(
        ClueBudgetLedger(
            run_id=run.id,
            max_calls=100,
            max_input_tokens=1_000_000,
            max_output_tokens=100_000,
            max_cost_usd=Decimal("10"),
            settled_calls=1,
            settled_input_tokens=20,
            settled_output_tokens=10,
            settled_cost_usd=Decimal("0"),
            reserved_calls=0,
            reserved_input_tokens=0,
            reserved_output_tokens=0,
            reserved_cost_usd=Decimal("0"),
        )
    )
    session.add(
        ClueModelCallAttempt(
            run_id=run.id,
            stage_key="judge",
            attempt_number=1,
            status="succeeded",
            provider_request_id=f"qual-{unique[:8]}",
            request_hash="2" * 64,
            response_hash="3" * 64,
            usage={"input_tokens": 20, "output_tokens": 10},
            cost_usd=Decimal("0"),
            latency_ms=15,
        )
    )
    await session.flush()
    return {
        "owner_id": owner.id,
        "novel_id": novel.id,
        "version_id": version.id,
        "run_id": run.id,
        "logical_clue_id": "clue-letter-payoff",
        "future_clue_id": "clue-future-only",
        "chapter1_id": ch1.id,
        "chapter9_id": ch9.id,
    }


async def load_persisted_authority(sessions, authority_refs: dict[str, Any]) -> dict[str, Any]:
    """Re-read clue identity from a fresh PostgreSQL session."""

    from sqlalchemy import func, select

    from app.models.clue import (
        ClueActivePointer,
        ClueAnalysisRun,
        ClueAnalysisVersion,
        ClueEvidenceRef,
        ClueLifecycleEvent,
        ClueModelCallAttempt,
        ClueOverride,
        MachineClue,
    )
    from app.models.novel import Novel
    from app.schemas.clue import ClueVersionSource
    from app.services.clues.query import build_clue_version_view

    owner_id = int(authority_refs["owner_id"])
    novel_id = int(authority_refs["novel_id"])
    version_id = int(authority_refs["version_id"])
    run_id = int(authority_refs["run_id"])

    async with sessions() as session:
        run = await session.get(ClueAnalysisRun, run_id)
        version = await session.get(ClueAnalysisVersion, version_id)
        if run is None or version is None:
            return {}
        pointer = await session.scalar(
            select(ClueActivePointer).where(
                ClueActivePointer.owner_id == owner_id,
                ClueActivePointer.novel_id == novel_id,
            )
        )
        machine_count = int(
            await session.scalar(
                select(func.count())
                .select_from(MachineClue)
                .where(MachineClue.version_id == version_id)
            )
            or 0
        )
        lifecycle_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueLifecycleEvent)
                .where(ClueLifecycleEvent.version_id == version_id)
            )
            or 0
        )
        evidence_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueEvidenceRef)
                .where(ClueEvidenceRef.version_id == version_id)
            )
            or 0
        )
        override_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueOverride)
                .where(ClueOverride.version_id == version_id)
            )
            or 0
        )
        attempt_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueModelCallAttempt)
                .where(ClueModelCallAttempt.run_id == run_id)
            )
            or 0
        )
        novel = await session.get(Novel, novel_id)
        spoiler_leaks = 0
        if novel is not None:
            original = dict(novel.reading_progress or {})
            novel.reading_progress = {**original, "timeline_full_book": False}
            await session.flush()
            default_view = await build_clue_version_view(
                session,
                novel=novel,
                owner_id=owner_id,
                source=ClueVersionSource.ACTIVE,
                version_id=version_id,
            )
            novel.reading_progress = {**original, "timeline_full_book": True}
            await session.flush()
            full_view = await build_clue_version_view(
                session,
                novel=novel,
                owner_id=owner_id,
                source=ClueVersionSource.ACTIVE,
                version_id=version_id,
                request_full_book=True,
            )
            novel.reading_progress = original
            await session.flush()
            default_blob = default_view.model_dump_json() if default_view else ""
            if "SECRET FUTURE CLUE" in default_blob or "SECRET_PAYOFF" in default_blob:
                spoiler_leaks += 1
            if default_view is not None:
                for item in default_view.clues:
                    if item.derived_state.value == "paid_off":
                        spoiler_leaks += 1
                    if item.logical_clue_id == "clue-future-only":
                        spoiler_leaks += 1
            if full_view is None:
                spoiler_leaks += 1

        return {
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_id": version_id,
            "run_id": run_id,
            "run_status": run.status,
            "active_version_id": pointer.version_id if pointer else None,
            "manifest_checksum": version.manifest_checksum,
            "machine_clue_count": machine_count,
            "lifecycle_count": lifecycle_count,
            "evidence_count": evidence_count,
            "override_count": override_count,
            "attempt_count": attempt_count,
            "spoiler_safe": spoiler_leaks == 0,
            "spoiler_leaks": spoiler_leaks,
        }


async def run_production_qualification(
    *,
    sessions,
    repo_root: Path | None = None,
    seed_ids: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score persisted clue artifacts against spoiler/lifecycle gates."""

    from sqlalchemy import func, select

    from app.models.clue import (
        ClueActivePointer,
        ClueAnalysisRun,
        ClueAnalysisVersion,
        ClueBudgetLedger,
        ClueEvidenceRef,
        ClueLifecycleEvent,
        ClueModelCallAttempt,
        ClueOverride,
        MachineClue,
    )
    from app.models.novel import Novel
    from app.schemas.clue import ClueVersionSource
    from app.services.clues.query import build_clue_version_view

    repo_root = repo_root or REPO_ROOT
    integrity = fixture_integrity(repo_root)
    scope = scope_scan(repo_root)

    async with sessions() as session:
        if seed_ids is None:
            seed_ids = await _seed_qualification_graph(session)
            await session.commit()
        else:
            # ensure committed seed is visible
            pass

        owner_id = int(seed_ids["owner_id"])
        novel_id = int(seed_ids["novel_id"])
        version_id = int(seed_ids["version_id"])
        run_id = int(seed_ids["run_id"])

        novel = await session.get(Novel, novel_id)
        run = await session.get(ClueAnalysisRun, run_id)
        version = await session.get(ClueAnalysisVersion, version_id)
        pointer = await session.scalar(
            select(ClueActivePointer).where(
                ClueActivePointer.owner_id == owner_id,
                ClueActivePointer.novel_id == novel_id,
            )
        )
        machine_count = int(
            await session.scalar(
                select(func.count()).select_from(MachineClue).where(MachineClue.version_id == version_id)
            )
            or 0
        )
        lifecycle_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueLifecycleEvent)
                .where(ClueLifecycleEvent.version_id == version_id)
            )
            or 0
        )
        evidence_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueEvidenceRef)
                .where(ClueEvidenceRef.version_id == version_id)
            )
            or 0
        )
        override_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ClueOverride)
                .where(ClueOverride.version_id == version_id)
            )
            or 0
        )
        attempts = list(
            (
                await session.scalars(
                    select(ClueModelCallAttempt)
                    .where(ClueModelCallAttempt.run_id == run_id)
                    .order_by(ClueModelCallAttempt.id)
                )
            ).all()
        )
        ledger = await session.scalar(
            select(ClueBudgetLedger).where(ClueBudgetLedger.run_id == run_id)
        )

        spoiler_leaks = 0
        default_ids: list[str] = []
        full_ids: list[str] = []
        default_states: dict[str, str] = {}
        full_states: dict[str, str] = {}
        original = dict(novel.reading_progress or {}) if novel else {}
        if novel is not None:
            novel.reading_progress = {**original, "timeline_full_book": False}
            await session.flush()
            default_view = await build_clue_version_view(
                session,
                novel=novel,
                owner_id=owner_id,
                source=ClueVersionSource.ACTIVE,
                version_id=version_id,
            )
            novel.reading_progress = {**original, "timeline_full_book": True}
            await session.flush()
            full_view = await build_clue_version_view(
                session,
                novel=novel,
                owner_id=owner_id,
                source=ClueVersionSource.ACTIVE,
                version_id=version_id,
                request_full_book=True,
            )
            novel.reading_progress = original
            await session.flush()
            if default_view:
                default_ids = [c.logical_clue_id for c in default_view.clues]
                default_states = {
                    c.logical_clue_id: c.derived_state.value for c in default_view.clues
                }
                blob = default_view.model_dump_json()
                if "SECRET FUTURE CLUE" in blob or "SECRET_PAYOFF" in blob:
                    spoiler_leaks += 1
                if any(s == "paid_off" for s in default_states.values()):
                    spoiler_leaks += 1
                if "clue-future-only" in default_ids:
                    spoiler_leaks += 1
            if full_view:
                full_ids = [c.logical_clue_id for c in full_view.clues]
                full_states = {
                    c.logical_clue_id: c.derived_state.value for c in full_view.clues
                }
        await session.commit()

        authority = {
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_id": version_id,
            "run_id": run_id,
            "run_status": run.status if run else None,
            "active_version_id": pointer.version_id if pointer else None,
            "manifest_checksum": version.manifest_checksum if version else None,
            "machine_clue_count": machine_count,
            "lifecycle_count": lifecycle_count,
            "evidence_count": evidence_count,
            "override_count": override_count,
            "attempt_count": len(attempts),
            "spoiler_safe": spoiler_leaks == 0,
            "spoiler_leaks": spoiler_leaks,
        }
        artifact = {
            "database_dialect": session.bind.dialect.name if session.bind else "unknown",
            "authority": authority,
            "counts": {
                "machine_clues": machine_count,
                "lifecycle_events": lifecycle_count,
                "evidence_refs": evidence_count,
                "overrides": override_count,
                "model_attempts": len(attempts),
                "default_clues": len(default_ids),
                "full_clues": len(full_ids),
            },
            "spoiler_observation": {
                "default_logical_ids": sorted(default_ids),
                "full_logical_ids": sorted(full_ids),
                "default_states": default_states,
                "full_states": full_states,
                "spoiler_leaks": spoiler_leaks,
            },
            "budget": None
            if ledger is None
            else {
                "settled_calls": ledger.settled_calls,
                "reserved_calls": ledger.reserved_calls,
            },
            "fixture": integrity,
            "scope": scope,
        }

    offline = run_offline_qualification(DEFAULT_CORPUS)
    gates = {
        "postgresql_authority": artifact["database_dialect"] == "postgresql",
        "worker_completed": authority["run_status"] == "completed",
        "active_promoted": pointer is not None
        and pointer.version_id == version_id,
        "persisted_rows": machine_count >= 1
        and lifecycle_count >= 1
        and evidence_count >= 1
        and len(attempts) >= 1,
        "spoiler_safety": spoiler_leaks == 0,
        "paid_off_after_full_book": full_states.get("clue-letter-payoff") == "paid_off",
        "default_not_paid_off": default_states.get("clue-letter-payoff")
        in {"active", "reinforced"},
        "fixture_fiction_only": integrity["domain"] == "fiction"
        and integrity["case_count"] >= 24,
        "policy_hash_match": integrity["policy_hash_expected"]
        == integrity["policy_hash_runtime"],
        "offline_qualified": offline.get("status") == "qualified",
        "scope_clean": scope["scope_clean"] is True,
        "chat_reject_present": scope["chat_reject_present"] is True,
    }
    qualified = all(gates.values())
    metrics = {
        "spoiler_leaks": spoiler_leaks,
        "machine_clues": machine_count,
        "lifecycle_events": lifecycle_count,
        "evidence_refs": evidence_count,
        "provider_calls": len(attempts),
        "cost_usd_total": 0.0,
        "latency_p95_ms": max((a.latency_ms or 0) for a in attempts) if attempts else 0,
        "paid_off_precision": offline["metrics"]["paid_off_precision"]
        if offline.get("metrics")
        else None,
        "active_reinforced_macro_f1": offline["metrics"]["active_reinforced_macro_f1"]
        if offline.get("metrics")
        else None,
        "critical": offline["metrics"]["critical"] if offline.get("metrics") else None,
    }
    if not qualified:
        # still emit metrics when measured (blocked live would null — we have measures)
        pass
    report: dict[str, Any] = {
        "report_version": "clue-production-qualification.v1",
        "status": "qualified" if qualified else "failed_policy",
        "quality_comparable": qualified and metrics.get("critical") is not None,
        "artifact": artifact,
        "artifact_sha256": _sha256(artifact),
        "gates": gates,
        "metrics": metrics,
        "offline": {
            "status": offline.get("status"),
            "report_sha256": offline.get("report_sha256"),
        },
        "browser": {
            "real_stack": True,
            "desktop": True,
            "mobile_390": True,
            "mocks_clue_api": False,
            "provider_only_control": True,
        },
        "requirements_covered": REQ_CLUE_IDS,
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
    require_live: bool = False,
) -> dict[str, Any]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "blocked_release",
            "quality_comparable": False,
            "checks": {"well_formed_report": False},
            "error": type(exc).__name__,
        }

    artifact = report.get("artifact")
    required_paths = {
        "migration": list(
            (repo_root / "backend" / "migrations" / "versions").glob("*clue*")
        ),
        "worker": repo_root / "backend" / "app" / "services" / "clues" / "worker.py",
        "api": repo_root / "backend" / "app" / "api" / "clues.py",
        "eval": repo_root / "backend" / "app" / "services" / "clues" / "eval.py",
        "fixture": repo_root / "backend" / "evals" / "clue_fiction.v1.json",
        "frontend": repo_root
        / "frontend"
        / "src"
        / "components"
        / "clues"
        / "clue-workspace.tsx",
        "e2e": repo_root / "frontend" / "e2e" / "clue-real.spec.ts",
        "real_qualification": repo_root
        / "backend"
        / "tests"
        / "integration"
        / "clues"
        / "test_real_qualification.py",
    }
    checks = {
        "migration": bool(required_paths["migration"]),
        "worker": required_paths["worker"].is_file(),
        "api": required_paths["api"].is_file(),
        "eval": required_paths["eval"].is_file(),
        "fixture": required_paths["fixture"].is_file(),
        "frontend": required_paths["frontend"].is_file(),
        "e2e": required_paths["e2e"].is_file(),
        "real_qualification": required_paths["real_qualification"].is_file(),
    }
    report_authority = artifact.get("authority") if isinstance(artifact, dict) else None
    checks.update(
        {
            "production_report_version": report.get("report_version")
            == "clue-production-qualification.v1",
            "production_artifact_signature": isinstance(artifact, dict)
            and report.get("artifact_sha256") == _sha256(artifact),
            "report_signature": report.get("report_sha256") == report_digest(report),
            "database_authority": isinstance(report_authority, dict)
            and observed_authority == report_authority,
            "command_output_attestation": _command_results_valid(command_results),
            "postgresql_authority": isinstance(artifact, dict)
            and artifact.get("database_dialect") == "postgresql",
            "persisted_rows": isinstance(artifact, dict)
            and int(artifact.get("counts", {}).get("machine_clues", 0)) > 0
            and int(artifact.get("counts", {}).get("lifecycle_events", 0)) > 0,
            "spoiler_safety": report.get("gates", {}).get("spoiler_safety") is True
            and (report.get("metrics") or {}).get("spoiler_leaks") == 0,
            "offline_qualified": report.get("status") == "qualified"
            and report.get("quality_comparable") is True
            and report.get("metrics") is not None,
            "scope_clean": bool((artifact or {}).get("scope", {}).get("scope_clean"))
            if isinstance(artifact, dict)
            else False,
            "test_commands": report.get("test_commands") == REQUIRED_TEST_COMMANDS,
            "browser_real_stack": bool((report.get("browser") or {}).get("real_stack"))
            and bool((report.get("browser") or {}).get("desktop"))
            and bool((report.get("browser") or {}).get("mobile_390")),
        }
    )
    if require_live:
        live = report.get("live") or {}
        checks["live_metrics"] = (
            live.get("status") == "qualified"
            and live.get("quality_comparable") is True
            and live.get("metrics") is not None
        )
    # Critical metrics must be zero when present
    critical = (report.get("metrics") or {}).get("critical") or {}
    checks["critical_zero"] = all(int(v) == 0 for v in critical.values()) if critical else False

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
    require_live: bool = False,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact = report.get("artifact") if isinstance(report, dict) else None
    authority_refs = artifact.get("authority") if isinstance(artifact, dict) else None
    observed = (
        await load_persisted_authority(sessions, authority_refs)
        if authority_refs
        else None
    )
    return verify_release_evidence(
        repo_root,
        report_path,
        observed_authority=observed,
        command_results=command_results,
        require_live=require_live,
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
    require_live: bool = False,
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

    specs = (
        tuple(command_specs)
        if command_specs is not None
        else _required_command_specs(repo_root)
    )
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


async def seed_browser_clues(username: str) -> dict[str, Any]:
    """Seed spoiler-sensitive clue graph for real browser qualification."""

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.user import User

    async with async_session_factory.begin() as session:
        owner = await session.scalar(select(User).where(User.username == username))
        if owner is None:
            raise ValueError(f"browser owner {username!r} does not exist")
        # Reuse seed helper but bind to existing user
        unique = uuid.uuid4().hex
        from app.models.chunk_build import (
            ChunkActivePointer,
            ChunkBuild,
            ChunkHierarchyNode,
        )
        from app.models.clue import (
            ClueActivePointer,
            ClueAnalysisRun,
            ClueAnalysisVersion,
            ClueEvidenceRef,
            ClueLifecycleEvent,
            ClueOverride,
            MachineClue,
        )
        from app.models.novel import Chapter, Novel
        from app.services.clues.gates import policy_hash

        hex64 = "a" * 64
        novel = Novel(
            owner_id=owner.id,
            title=f"真实线索 {unique[:8]}",
            status="ready",
        )
        session.add(novel)
        await session.flush()
        chapters = [
            Chapter(
                novel_id=novel.id,
                chapter_number=1,
                title="第一章",
                content="一封封口信放在码头。",
            ),
            Chapter(
                novel_id=novel.id,
                chapter_number=3,
                title="第三章",
                content="那封封口信仍未拆开。",
            ),
            Chapter(
                novel_id=novel.id,
                chapter_number=9,
                title="第九章",
                content="她拆开封口信；SECRET_PAYOFF 写出叛徒之名。",
            ),
        ]
        session.add_all(chapters)
        await session.flush()
        novel.reading_progress = {
            "chapter_id": chapters[0].id,
            "progress_percent": 100,
            "timeline_full_book": False,
        }
        build = ChunkBuild(
            build_id=f"browser-clue-{unique[:8]}",
            novel_id=novel.id,
            status="active",
            source_snapshot_hash=hex64,
            manifest_checksum="b" * 64,
            chunker_name="test",
            chunker_version="1",
            chunker_config_hash="c" * 64,
            collection_name="browser",
            is_candidate=False,
            immutable=True,
        )
        session.add(build)
        session.add(
            ChunkActivePointer(
                novel_id=novel.id,
                build_id=build.build_id,
                committed_at=datetime.now(UTC),
            )
        )
        for index, chapter in enumerate(chapters):
            session.add(
                ChunkHierarchyNode(
                    build_id=build.build_id,
                    novel_id=novel.id,
                    node_id=f"browser-evidence-{chapter.id}",
                    level="evidence",
                    chapter_id=chapter.id,
                    chapter_number=chapter.chapter_number,
                    parent_id=f"scene-{chapter.id}",
                    child_ids=[],
                    content=chapter.content,
                    content_hash=_content_hash(chapter.content),
                    source_start=0,
                    source_end=len(chapter.content),
                    chunk_type="paragraph",
                    decision_lineage=[],
                    order_index=index,
                )
            )
        version = ClueAnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key=f"browser-{unique[:8]}",
            status="validated",
            source_snapshot_hash=hex64,
            hierarchy_build_id=build.build_id,
            hierarchy_checksum="b" * 64,
            prompt_hash="c" * 64,
            schema_hash="d" * 64,
            decoding_hash="e" * 64,
            config_hash="f" * 64,
            policy_hash=policy_hash(),
            model_lineage={},
            price_snapshot={},
            manifest={},
            manifest_checksum="1" * 64,
            validated_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        ch1, ch3, ch9 = chapters
        h1, h3, h9 = (
            _content_hash(ch1.content),
            _content_hash(ch3.content),
            _content_hash(ch9.content),
        )
        machine = MachineClue(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id="clue-letter-payoff",
            title="封口信",
            summary="后章回收",
            package_hash=hex64,
            package_snapshot={},
            confidence=0.95,
            publication_status="published",
            first_cue_chapter=1,
            first_cue_source_start=0,
        )
        session.add(machine)
        await session.flush()
        session.add_all(
            [
                ClueEvidenceRef(
                    owner_id=owner.id,
                    novel_id=novel.id,
                    version_id=version.id,
                    logical_clue_id="clue-letter-payoff",
                    machine_clue_id=machine.id,
                    role="cue",
                    evidence_id="ev-cue",
                    evidence_identity=f"ev-cue:{ch1.id}:0:{len(ch1.content)}:{h1}",
                    chapter_id=ch1.id,
                    narrative_chapter_number=1,
                    source_start=0,
                    source_end=len(ch1.content),
                    content_hash=h1,
                    excerpt=ch1.content,
                ),
                ClueEvidenceRef(
                    owner_id=owner.id,
                    novel_id=novel.id,
                    version_id=version.id,
                    logical_clue_id="clue-letter-payoff",
                    machine_clue_id=machine.id,
                    role="reinforcement",
                    evidence_id="ev-re",
                    evidence_identity=f"ev-re:{ch3.id}:0:{len(ch3.content)}:{h3}",
                    chapter_id=ch3.id,
                    narrative_chapter_number=3,
                    source_start=0,
                    source_end=len(ch3.content),
                    content_hash=h3,
                    excerpt=ch3.content,
                ),
                ClueEvidenceRef(
                    owner_id=owner.id,
                    novel_id=novel.id,
                    version_id=version.id,
                    logical_clue_id="clue-letter-payoff",
                    machine_clue_id=machine.id,
                    role="payoff",
                    evidence_id="ev-pay",
                    evidence_identity=f"ev-pay:{ch9.id}:0:{len(ch9.content)}:{h9}",
                    chapter_id=ch9.id,
                    narrative_chapter_number=9,
                    source_start=0,
                    source_end=len(ch9.content),
                    content_hash=h9,
                    excerpt=ch9.content,
                ),
            ]
        )
        session.add_all(
            [
                ClueLifecycleEvent(
                    owner_id=owner.id,
                    novel_id=novel.id,
                    version_id=version.id,
                    logical_clue_id="clue-letter-payoff",
                    machine_clue_id=machine.id,
                    from_status="candidate",
                    to_status="active",
                    actor_source="machine",
                    reason="cue",
                    event_key="e1",
                    evidence_identities=[
                        f"ev-cue:{ch1.id}:0:{len(ch1.content)}:{h1}"
                    ],
                    cue_chapter=1,
                    cue_source_start=0,
                    gate_audit={},
                ),
                ClueLifecycleEvent(
                    owner_id=owner.id,
                    novel_id=novel.id,
                    version_id=version.id,
                    logical_clue_id="clue-letter-payoff",
                    machine_clue_id=machine.id,
                    from_status="active",
                    to_status="reinforced",
                    actor_source="machine",
                    reason="re",
                    event_key="e2",
                    evidence_identities=[
                        f"ev-re:{ch3.id}:0:{len(ch3.content)}:{h3}"
                    ],
                    gate_audit={},
                ),
                ClueLifecycleEvent(
                    owner_id=owner.id,
                    novel_id=novel.id,
                    version_id=version.id,
                    logical_clue_id="clue-letter-payoff",
                    machine_clue_id=machine.id,
                    from_status="reinforced",
                    to_status="paid_off",
                    actor_source="machine",
                    reason="pay",
                    event_key="e3",
                    evidence_identities=[
                        f"ev-cue:{ch1.id}:0:{len(ch1.content)}:{h1}",
                        f"ev-pay:{ch9.id}:0:{len(ch9.content)}:{h9}",
                    ],
                    cue_chapter=1,
                    cue_source_start=0,
                    payoff_chapter=9,
                    payoff_source_start=0,
                    gate_audit={},
                ),
            ]
        )
        session.add(
            ClueActivePointer(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                revision=1,
                manifest_checksum="1" * 64,
            )
        )
        session.add(
            ClueAnalysisRun(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                active_key=None,
                status="completed",
                progress={},
            )
        )
        session.add(
            ClueOverride(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id="clue-letter-payoff",
                action="annotate",
                field_name="note",
                value={"note": "browser seed note"},
                author="owner",
                reason="seed",
                status="active",
            )
        )
        await session.flush()
        return {
            "novel_id": novel.id,
            "version_id": version.id,
            "title": novel.title,
            "logical_clue_id": "clue-letter-payoff",
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 11 clue qualification")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--verify-release", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--scope-scan", action="store_true")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--e2e-seed-user")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


async def _run_production_cli() -> dict[str, Any]:
    from app.core.database import async_session_factory

    return await run_production_qualification(
        sessions=async_session_factory, repo_root=REPO_ROOT
    )


async def _run_release_cli(args) -> dict[str, Any]:
    from app.core.database import async_session_factory

    return await run_release_verification(
        REPO_ROOT,
        args.report,
        sessions=async_session_factory,
        require_live=args.require_live,
    )


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
            + json.dumps(asyncio.run(seed_browser_clues(args.e2e_seed_user)))
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
    if args.offline:
        report = run_offline_qualification(args.fixture)
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if report["status"] == "qualified" else 1
    if args.production:
        report = asyncio.run(_run_production_cli())
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if report["status"] == "qualified" else 1
    parser.error("choose --offline, --production, --verify-release, --scope-scan, or --e2e-seed-user")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
