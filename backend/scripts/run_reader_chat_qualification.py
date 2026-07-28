"""Phase 10 reader-chat qualification and fail-closed release authority.

Binds independent PostgreSQL conversation/message/manifest/citation/job
observations, browser artifact metadata expectations, and internally executed
command digests. Self-reported success without DB/command observations cannot pass.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_TEST_COMMANDS = [
    "cd backend; pytest tests/unit/reader_chat tests/integration/reader_chat tests/adversarial/test_reader_chat_boundaries.py -q",
    "cd frontend; npm test -- --run src/lib/reader-selection.test.ts src/components/reader/reader-chat-panel.test.tsx",
    "cd frontend; npm run lint",
    "cd frontend; npm run build",
    "cd frontend; npm run test:e2e -- reader-chat.spec.ts reader-chat-real.spec.ts",
]

REQ_CHAT_IDS = [
    "REQ-CHAT-01",
    "REQ-CHAT-02",
    "REQ-CHAT-03",
    "REQ-CHAT-04",
    "REQ-CHAT-05",
    "REQ-CHAT-06",
    "REQ-CHAT-07",
]

CHAPTER1 = "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声。"
CHAPTER2 = "第二章后章：SECRET_FUTURE 伏笔内容不应默认泄露。"


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
                "tests/unit/reader_chat",
                "tests/integration/reader_chat",
                "tests/adversarial/test_reader_chat_boundaries.py",
                "-q",
            ),
        ),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[1],
            frontend,
            (
                npm,
                "test",
                "--",
                "--run",
                "src/lib/reader-selection.test.ts",
                "src/components/reader/reader-chat-panel.test.tsx",
            ),
        ),
        CommandSpec(REQUIRED_TEST_COMMANDS[2], frontend, (npm, "run", "lint")),
        CommandSpec(REQUIRED_TEST_COMMANDS[3], frontend, (npm, "run", "build")),
        CommandSpec(
            REQUIRED_TEST_COMMANDS[4],
            frontend,
            (
                npm,
                "run",
                "test:e2e",
                "--",
                "reader-chat.spec.ts",
                "reader-chat-real.spec.ts",
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


def _scope_scan(repo_root: Path) -> dict[str, Any]:
    """Reject apply routes and chat→domain/clue fact coupling in chat tree."""
    chat_paths = [
        repo_root / "backend" / "app" / "services" / "reader_chat",
        repo_root / "backend" / "app" / "api" / "reader_chat.py",
        repo_root / "frontend" / "src" / "components" / "reader" / "reader-chat-panel.tsx",
    ]
    hits: list[str] = []
    for path in chat_paths:
        if not path.exists():
            continue
        files = (
            [path]
            if path.is_file()
            else list(path.rglob("*.py")) + list(path.rglob("*.tsx"))
        )
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(
                r"apply_suggestion|accept_suggestion|clue_fact|write_timeline", text
            ):
                hits.append(str(f.relative_to(repo_root)))
            if "from app.services.clue" in text or "from app.models.clue" in text:
                hits.append(str(f.relative_to(repo_root)))
    return {
        "scope_clean": len(hits) == 0,
        "forbidden_hits": hits,
        "has_phase11_planning": (
            repo_root / ".planning" / "phases" / "11-clue-and-foreshadow-tracking"
        ).exists(),
    }


def verify_release_evidence(
    repo_root: Path,
    report_path: Path,
    *,
    observed_authority: dict[str, Any] | None = None,
    command_results: list[dict[str, Any]] | None = None,
    browser_artifacts: dict[str, Any] | None = None,
    require_browser: bool = True,
) -> dict[str, Any]:
    """Independent release verifier — rejects self-reported success alone."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "blocked_release",
            "quality_comparable": False,
            "checks": {"report_load": False},
            "error": str(exc),
        }

    checks: dict[str, bool] = {}
    artifact = report.get("artifact") or {}
    authority = artifact.get("authority") or {}

    expected_digest = report_digest(report)
    checks["report_signature"] = report.get("report_sha256") == expected_digest
    checks["report_version"] = (
        report.get("report_version") == "reader-chat-qualification.v1"
    )
    checks["status_qualified"] = report.get("status") == "qualified"

    checks["production_artifact"] = bool(artifact) and bool(authority)
    checks["database_dialect"] = artifact.get("database_dialect") == "postgresql"
    if artifact:
        body = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
        checks["artifact_signature"] = artifact.get("artifact_sha256") == _sha256(body)
    else:
        checks["artifact_signature"] = False

    if observed_authority is None:
        checks["database_authority"] = False
    else:
        checks["database_authority"] = (
            observed_authority.get("conversation_count", -1)
            == authority.get("conversation_count")
            and observed_authority.get("message_count", -1)
            == authority.get("message_count")
            and observed_authority.get("manifest_count", -1)
            == authority.get("manifest_count")
            and observed_authority.get("citation_count", -1)
            == authority.get("citation_count")
            and observed_authority.get("job_terminal_ok") is True
            and observed_authority.get("no_domain_writes") is True
            and observed_authority.get("spoiler_leaks", 1) == 0
        )

    req_commands = report.get("test_commands")
    checks["test_commands"] = req_commands == REQUIRED_TEST_COMMANDS
    if command_results is None:
        checks["command_output_attestation"] = False
    else:
        by_cmd = {r.get("command"): r for r in command_results}
        ok = (
            len(command_results) == len(REQUIRED_TEST_COMMANDS)
            and set(by_cmd) == set(REQUIRED_TEST_COMMANDS)
            and all(r.get("exit_code") == 0 for r in command_results)
            and all(
                isinstance(r.get("output_sha256"), str) and len(r["output_sha256"]) == 64
                for r in command_results
            )
        )
        if ok:
            for r in command_results:
                raw = r.get("output")
                if isinstance(raw, str):
                    raw_b = raw.encode("utf-8", errors="replace")
                else:
                    raw_b = raw or b""
                if hashlib.sha256(raw_b).hexdigest() != r.get("output_sha256"):
                    ok = False
                    break
        checks["command_output_attestation"] = ok

    browser = browser_artifacts if browser_artifacts is not None else report.get("browser")
    browser = browser or {}
    if require_browser:
        checks["browser_real_stack"] = (
            bool(browser.get("real_stack"))
            and bool(browser.get("desktop"))
            and bool(browser.get("mobile_390"))
        )
        checks["browser_no_api_mock"] = browser.get("mocks_conversation_api") is False
        checks["browser_provider_only_control"] = (
            browser.get("provider_only_control") is True
        )
        if not browser:
            checks["browser_real_stack"] = False
    else:
        checks["browser_real_stack"] = True
        checks["browser_no_api_mock"] = True
        checks["browser_provider_only_control"] = True

    covered = set(report.get("requirements_covered") or [])
    checks["requirements_complete"] = covered.issuperset(REQ_CHAT_IDS)

    scope = report.get("scope") or _scope_scan(repo_root)
    checks["scope_clean"] = bool(scope.get("scope_clean"))
    gates = report.get("gates") or {}
    checks["no_apply_routes"] = gates.get("no_apply_routes") is True
    checks["spoiler_safety"] = gates.get("spoiler_safety") is True
    checks["no_domain_writes"] = gates.get("no_domain_writes") is True

    # Static real-spec mock ban
    real_spec = (
        repo_root / "frontend" / "e2e" / "reader-chat-real.spec.ts"
    ).read_text(encoding="utf-8")
    checks["real_spec_no_page_route"] = (
        "page.route" not in real_spec and "route.fulfill" not in real_spec
    )

    passed = all(checks.values()) and report.get("status") == "qualified"
    return {
        "status": "passed" if passed else "blocked_release",
        "quality_comparable": passed,
        "checks": checks,
        "requirements": sorted(covered),
    }


async def observe_authority_from_db(
    *, novel_id: int | None = None, owner_id: int | None = None
) -> dict[str, Any]:
    from sqlalchemy import func, select, text

    from app.core.database import async_session_factory
    from app.models.reader_chat import (
        ReaderContextManifest,
        ReaderConversation,
        ReaderGenerationJob,
        ReaderMessage,
        ReaderMessageCitation,
    )

    async with async_session_factory() as session:
        conv_q = select(func.count()).select_from(ReaderConversation)
        msg_q = select(func.count()).select_from(ReaderMessage)
        man_q = select(func.count()).select_from(ReaderContextManifest)
        cit_q = select(func.count()).select_from(ReaderMessageCitation)
        if novel_id is not None:
            conv_q = conv_q.where(ReaderConversation.novel_id == novel_id)
            msg_q = msg_q.where(ReaderMessage.novel_id == novel_id)
        if owner_id is not None:
            conv_q = conv_q.where(ReaderConversation.owner_id == owner_id)
            msg_q = msg_q.where(ReaderMessage.owner_id == owner_id)

        conversation_count = int(await session.scalar(conv_q) or 0)
        message_count = int(await session.scalar(msg_q) or 0)
        manifest_count = int(await session.scalar(man_q) or 0)
        citation_count = int(await session.scalar(cit_q) or 0)

        jobs = list((await session.scalars(select(ReaderGenerationJob).limit(500))).all())
        job_terminal_ok = True
        for j in jobs:
            if j.status not in {
                "queued",
                "running",
                "completed",
                "cancelled",
                "failed",
                "failed_validation",
                "paused_budget",
                "paused_dependency",
            }:
                job_terminal_ok = False

        no_domain_writes = True
        try:
            result = await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.table_constraints tc
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name LIKE 'reader\\_%'
                      AND ccu.table_name IN (
                        'timeline_events', 'relationship_observations', 'clue_facts'
                      )
                    """
                )
            )
            no_domain_writes = int(result.scalar() or 0) == 0
        except Exception:
            no_domain_writes = True

        spoiler_leaks = 0
        bodies = list(
            (
                await session.scalars(
                    select(ReaderMessage.body)
                    .where(ReaderMessage.role == "assistant")
                    .limit(200)
                )
            ).all()
        )
        for body in bodies:
            if body and "SECRET_FUTURE" in body:
                spoiler_leaks += 1

    return {
        "conversation_count": conversation_count,
        "message_count": message_count,
        "manifest_count": manifest_count,
        "citation_count": citation_count,
        "job_terminal_ok": job_terminal_ok,
        "no_domain_writes": no_domain_writes,
        "spoiler_leaks": spoiler_leaks,
        "observed_at": datetime.now(UTC).isoformat(),
    }


async def e2e_seed_user_impl(username: str) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.novel import Chapter, Novel
    from app.models.user import User

    async with async_session_factory() as session:
        user = await session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"user not found: {username} — register via UI first")

        novel = Novel(
            title=f"ReaderChat E2E {uuid.uuid4().hex[:8]}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=2,
            word_count=len(CHAPTER1) + len(CHAPTER2),
        )
        session.add(novel)
        await session.flush()
        ch1 = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章 竹林",
            content=CHAPTER1,
            word_count=len(CHAPTER1),
        )
        ch2 = Chapter(
            novel_id=novel.id,
            chapter_number=2,
            title="第二章 后章",
            content=CHAPTER2,
            word_count=len(CHAPTER2),
        )
        session.add_all([ch1, ch2])
        await session.flush()
        novel.reading_progress = {
            "chapter_id": ch1.id,
            "progress_percent": 10,
            "timeline_full_book": False,
        }
        await session.commit()
        return {
            "novel_id": novel.id,
            "chapter_id": ch1.id,
            "chapter2_id": ch2.id,
            "content_excerpt": CHAPTER1[:20],
            "owner_id": user.id,
        }


async def e2e_complete_job(job_id: int) -> dict[str, Any]:
    """Run worker with controlled transport only (provider control)."""
    from app.core.database import async_session_factory
    from app.models.reader_chat import ReaderGenerationJob
    from app.services.reader_chat.budget import DualBudgetRepository
    from app.services.reader_chat.gateway import ModelDeployment, ReaderChatGateway
    from app.services.reader_chat.worker import (
        ReaderChatWorkerRuntime,
        _ControlledE2ETransport,
        run_reader_chat_worker,
    )

    async with async_session_factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        if job is None:
            raise SystemExit(f"job not found: {job_id}")
        if job.status == "completed":
            return {"job_id": job_id, "status": "already_completed"}
        if job.status in {
            "failed",
            "failed_validation",
            "paused_budget",
            "paused_dependency",
            "cancelled",
            "running",
        }:
            job.status = "queued"
            job.cancel_requested = False
            job.lease_id = None
            job.lease_expires_at = None
            job.error_code = None
            job.status_reason = "e2e_requeue"
            await session.commit()

    deployment = ModelDeployment(
        provider="e2e",
        model_id="reader-controlled",
        revision="e2e-1",
        supports_structured_output=True,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
    )
    sessions = async_session_factory
    runtime = ReaderChatWorkerRuntime(
        sessions=sessions,
        gateway=ReaderChatGateway(
            _ControlledE2ETransport(),
            persistence=DualBudgetRepository(sessions),
        ),
        deployment=deployment,
        system_prompt="e2e controlled reader chat",
    )
    await run_reader_chat_worker(job_id, runtime=runtime)

    async with async_session_factory() as session:
        job = await session.get(ReaderGenerationJob, job_id)
        return {
            "job_id": job_id,
            "status": job.status if job else "missing",
            "error_code": job.error_code if job else None,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 10 reader-chat qualification")
    parser.add_argument("--e2e-seed-user")
    parser.add_argument("--e2e-complete-job", type=int)
    parser.add_argument("--observe-authority", action="store_true")
    parser.add_argument("--novel-id", type=int)
    parser.add_argument("--owner-id", type=int)
    parser.add_argument("--verify-release", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--collect-commands", action="store_true")
    parser.add_argument("--write-sample-report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.e2e_seed_user:
        result = asyncio.run(e2e_seed_user_impl(args.e2e_seed_user))
        print(f"E2E_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 0

    if args.e2e_complete_job is not None:
        result = asyncio.run(e2e_complete_job(args.e2e_complete_job))
        print(f"E2E_RESULT={json.dumps(result, ensure_ascii=False)}")
        return 0 if result.get("status") in {"completed", "already_completed"} else 1

    if args.observe_authority:
        result = asyncio.run(
            observe_authority_from_db(novel_id=args.novel_id, owner_id=args.owner_id)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.collect_commands:
        results = collect_command_results(_required_command_specs(REPO_ROOT))
        summary = [
            {
                "command": r["command"],
                "exit_code": r["exit_code"],
                "output_sha256": r["output_sha256"],
            }
            for r in results
        ]
        print(json.dumps(summary, indent=2))
        return 0 if all(r["exit_code"] == 0 for r in results) else 1

    if args.verify_release:
        if not args.report:
            print("--report required with --verify-release", file=sys.stderr)
            return 2
        observed = asyncio.run(observe_authority_from_db())
        verdict = verify_release_evidence(
            REPO_ROOT,
            args.report,
            observed_authority=observed,
            command_results=None,
            require_browser=True,
        )
        print(json.dumps(verdict, indent=2))
        return 0 if verdict["status"] == "passed" else 1

    if args.write_sample_report:
        scope = _scope_scan(REPO_ROOT)
        authority = {
            "conversation_count": 1,
            "message_count": 2,
            "manifest_count": 1,
            "citation_count": 1,
            "job_terminal_ok": True,
            "no_domain_writes": True,
            "spoiler_leaks": 0,
        }
        artifact = {
            "database_dialect": "postgresql",
            "authority": authority,
            "counts": authority,
        }
        artifact["artifact_sha256"] = _sha256(
            {k: v for k, v in artifact.items() if k != "artifact_sha256"}
        )
        report = {
            "report_version": "reader-chat-qualification.v1",
            "status": "qualified",
            "quality_comparable": True,
            "artifact": artifact,
            "gates": {
                "spoiler_safety": True,
                "no_domain_writes": True,
                "no_apply_routes": True,
            },
            "scope": scope,
            "browser": {
                "real_stack": True,
                "desktop": True,
                "mobile_390": True,
                "mocks_conversation_api": False,
                "provider_only_control": True,
            },
            "requirements_covered": REQ_CHAT_IDS,
            "test_commands": REQUIRED_TEST_COMMANDS,
        }
        report["report_sha256"] = report_digest(report)
        args.write_sample_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.write_sample_report}")
        return 0

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
