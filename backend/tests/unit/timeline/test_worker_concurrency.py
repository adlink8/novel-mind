"""Concurrent chapter-extraction orchestration contracts (fully faked, no DB).

Covers ``run_timeline_worker``'s parallel extraction phase:
- Semaphore-bounded concurrency (max in-flight == chapter_concurrency).
- Character registry loaded once and shared across chapters.
- First failure triggers cooperative abort: queued chapters are skipped,
  in-flight ones finish, the original exception propagates unchanged.
- run status dispatch stays aligned with the serial-era contract
  (cancelled / paused_dependency / completed).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.timeline.worker as worker_module
from app.services.timeline._worker_prepare import TimelineCancellationRequested
from app.services.timeline.budget import BudgetPolicy
from app.services.timeline.model_gateway import DependencyPaused
from app.services.timeline.worker import run_timeline_worker

pytestmark = pytest.mark.unit


class _Orch:
    """Records extraction behavior and doubles as the patch surface."""

    def __init__(
        self,
        chapters: list[SimpleNamespace],
        *,
        fail_chapter_id: int | None = None,
        failure: Exception | None = None,
        extract_delay: float = 0.02,
    ) -> None:
        self.chapters = chapters
        self.fail_chapter_id = fail_chapter_id
        self.failure = failure
        self.extract_delay = extract_delay
        self.calls: list[int] = []
        self.registry_loads = 0
        self.progress: list[tuple[int, int, str]] = []
        self.active = 0
        self.max_active = 0
        self.reconciled = False
        self.promoted = False
        self.finish: tuple[int, str, str | None] | None = None

    # -- patched collaborators -------------------------------------------------

    async def claim_run(self, sessions, run_id, lease_id) -> bool:
        return True

    async def prepare_run(self, runtime, run_id):
        run = SimpleNamespace(id=run_id, novel_id=10, owner_id=1)
        version = SimpleNamespace(id=5)
        build = SimpleNamespace(build_id="b")
        return run, version, build, self.chapters

    async def raise_if_cancel(self, sessions, run_id) -> None:
        return None

    async def load_character_registry(self, sessions, novel_id):
        self.registry_loads += 1
        return [{"name": "mira"}]

    async def extract_and_persist(
        self, runtime, budget, run, version, build, chapter, *,
        character_registry=None,
    ):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.extract_delay)
            self.calls.append(chapter.id)
            if chapter.id == self.fail_chapter_id and self.failure is not None:
                raise self.failure
        finally:
            self.active -= 1

    async def update_progress(self, sessions, run_id, completed, total, stage):
        self.progress.append((completed, total, stage))

    async def reconcile(self, runtime, budget, run, version) -> None:
        self.reconciled = True

    async def validate_and_promote(self, sessions, run, version) -> None:
        self.promoted = True

    async def finish_run(self, sessions, run_id, status, reason) -> None:
        self.finish = (run_id, status, reason)


def _runtime(concurrency: int):
    return SimpleNamespace(
        sessions=None,
        budget_policy=BudgetPolicy(
            max_calls=100,
            max_input_tokens=1_000_000,
            max_output_tokens=1_000_000,
            max_cost_usd=Decimal("10"),
        ),
        chapter_concurrency=concurrency,
        gateway=None,
        extraction_deployment=None,
        reconciliation_deployment=None,
        extraction_prompt="p",
    )


def _patch(monkeypatch, orch: _Orch) -> None:
    monkeypatch.setattr(worker_module, "_claim_run", orch.claim_run)
    monkeypatch.setattr(worker_module, "_prepare_run", orch.prepare_run)
    monkeypatch.setattr(
        worker_module, "_raise_if_cancel_requested", orch.raise_if_cancel
    )
    monkeypatch.setattr(
        worker_module, "_load_character_registry", orch.load_character_registry
    )
    monkeypatch.setattr(
        worker_module, "_extract_and_persist", orch.extract_and_persist
    )
    monkeypatch.setattr(worker_module, "_update_progress", orch.update_progress)
    monkeypatch.setattr(
        worker_module, "_reconcile_and_persist", orch.reconcile
    )
    monkeypatch.setattr(
        worker_module, "_validate_and_promote", orch.validate_and_promote
    )
    monkeypatch.setattr(worker_module, "_finish_run", orch.finish_run)


@pytest.mark.asyncio
async def test_extraction_is_bounded_concurrent_and_registry_shared(monkeypatch):
    chapters = [SimpleNamespace(id=i) for i in range(1, 7)]
    orch = _Orch(chapters)
    _patch(monkeypatch, orch)

    await run_timeline_worker(1, runtime=_runtime(concurrency=2))

    assert orch.max_active == 2, "并发路数必须被 Semaphore 收敛到 2"
    assert sorted(orch.calls) == [c.id for c in chapters]
    assert orch.registry_loads == 1, "角色注册表全书只加载一次"
    assert orch.reconciled and orch.promoted
    assert orch.finish is None, "成功路径不触发 _finish_run"
    assert orch.progress[-1] == (6, 6, "extracting")


@pytest.mark.asyncio
async def test_serial_when_concurrency_one(monkeypatch):
    chapters = [SimpleNamespace(id=i) for i in range(1, 5)]
    orch = _Orch(chapters)
    _patch(monkeypatch, orch)

    await run_timeline_worker(1, runtime=_runtime(concurrency=1))

    assert orch.max_active == 1, "concurrency=1 时必须严格串行（SQLite 路径）"
    assert sorted(orch.calls) == [c.id for c in chapters]


@pytest.mark.asyncio
async def test_first_failure_cooperatively_aborts_remaining(monkeypatch):
    chapters = [SimpleNamespace(id=i) for i in range(1, 7)]
    failure = DependencyPaused("chapter 2 has no Phase 07 evidence")
    orch = _Orch(chapters, fail_chapter_id=2, failure=failure)
    _patch(monkeypatch, orch)

    # run_timeline_worker 对确定性失败类异常吞掉并落状态（不向上抛）
    await run_timeline_worker(1, runtime=_runtime(concurrency=2))

    assert orch.finish == (1, "paused_dependency", str(failure))
    assert 1 in orch.calls and 2 in orch.calls, "失败前在途的章节已完成"
    assert len(orch.calls) < len(chapters), "熔断后排队章节必须被跳过"
    assert not orch.reconciled and not orch.promoted


@pytest.mark.asyncio
async def test_first_failure_propagates_original_exception(monkeypatch):
    """_extract_chapters_concurrently 层面：首失败原样上抛（供 except 链分发）。"""
    chapters = [SimpleNamespace(id=i) for i in range(1, 7)]
    failure = DependencyPaused("chapter 2 has no Phase 07 evidence")
    orch = _Orch(chapters, fail_chapter_id=2, failure=failure)
    _patch(monkeypatch, orch)

    runtime = _runtime(concurrency=2)
    run = SimpleNamespace(id=1, novel_id=10, owner_id=1)
    registry = await orch.load_character_registry(None, 10)
    with pytest.raises(DependencyPaused) as excinfo:
        await worker_module._extract_chapters_concurrently(
            runtime, None, run, SimpleNamespace(id=5),
            SimpleNamespace(build_id="b"), chapters, registry,
        )
    assert excinfo.value is failure, "首个失败必须原样上抛，不得包装"


@pytest.mark.asyncio
async def test_user_cancel_maps_to_cancelled_status(monkeypatch):
    chapters = [SimpleNamespace(id=i) for i in range(1, 5)]
    orch = _Orch(
        chapters,
        fail_chapter_id=1,
        failure=TimelineCancellationRequested(),
    )
    _patch(monkeypatch, orch)

    await run_timeline_worker(1, runtime=_runtime(concurrency=2))

    assert orch.finish == (1, "cancelled", "cancel requested")
    assert not orch.promoted


@pytest.mark.asyncio
async def test_abort_waits_for_in_flight_extraction_to_settle(monkeypatch):
    """熔断不得 mid-statement 强杀在途任务：失败后正在跑的章节要完整收尾。"""
    chapters = [SimpleNamespace(id=i) for i in range(1, 7)]

    class SlowOrch(_Orch):
        async def extract_and_persist(self, runtime, budget, run, version, build,
                                     chapter, *, character_registry=None):
            # ch1 慢（120ms），ch2 快失败（5ms）→ ch1 在 abort 置位后仍在途
            delay = 0.12 if chapter.id == 1 else 0.005
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(delay)
                self.calls.append(chapter.id)
                if chapter.id == self.fail_chapter_id and self.failure is not None:
                    raise self.failure
            finally:
                self.active -= 1

    orch = SlowOrch(
        chapters,
        fail_chapter_id=2,
        failure=DependencyPaused("no evidence"),
    )
    _patch(monkeypatch, orch)

    runtime = _runtime(concurrency=2)
    run = SimpleNamespace(id=1, novel_id=10, owner_id=1)
    registry = await orch.load_character_registry(None, 10)
    with pytest.raises(DependencyPaused):
        await worker_module._extract_chapters_concurrently(
            runtime, None, run, SimpleNamespace(id=5),
            SimpleNamespace(build_id="b"), chapters, registry,
        )

    assert orch.calls.count(1) == 1, "在途章节必须完整收尾（不得被强杀）"
    assert len(orch.calls) < len(chapters)
