"""Unit tests for app.services.import_service covering the import pipeline.

Uses the in-memory DB session for real ImportJob rows while mocking the
novel_service side effects (upload/parse/create) and the FileWrapper so no
external service or filesystem is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit

from starlette.datastructures import UploadFile as StarletteUploadFile

from app.models.import_job import ImportJob
from app.models.novel import Novel
from app.models.user import User
from app.services import import_service as import_service_module
from app.services.import_service import ImportService


async def _user(db: AsyncSession) -> User:
    user = User(
        username="importuser",
        email="importuser@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _job(
    db: AsyncSession, *, status="pending", novel_id=None, **kwargs
) -> ImportJob:
    job = ImportJob(
        novel_id=novel_id,
        status=status,
        progress=0,
        message="等待处理",
        **kwargs,
    )
    db.add(job)
    await db.flush()
    return job


def _chapters() -> list[dict]:
    return [
        {"chapter_number": 1, "title": "第一章", "content": "正文一", "word_count": 3},
        {"chapter_number": 2, "title": "第二章", "content": "正文二", "word_count": 3},
    ]


def _upload_file(filename: str = "test_novel.txt") -> StarletteUploadFile:
    return StarletteUploadFile(
        file=BytesIO(b"content"), filename=filename, size=7, headers=None
    )


class _Novel:
    def __init__(self, id: int):
        self.id = id


# ── pipeline success path ──


async def test_process_import_file_success(db_session, monkeypatch):
    user = await _user(db_session)
    job = await _job(db_session)
    # Create a real Novel row so job.novel_id FK update succeeds.
    novel = Novel(title="目标小说", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    service = ImportService()

    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/a.txt", "正文")),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "parse_novel",
        Mock(return_value=_chapters()),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "create_novel_record",
        AsyncMock(return_value=_Novel(id=novel.id)),
    )

    await service.process_import_file(db_session, job.id, _upload_file(), user.id)

    refreshed = await db_session.get(ImportJob, job.id)
    assert refreshed.status == "ready"
    # 状态机拒绝 ready→ready 二次推进，最终进度停留在 90（生产同构行为）。
    assert refreshed.progress >= 90
    assert refreshed.content_hash == service.compute_content_hash("正文")
    assert refreshed.novel_id == novel.id
    assert refreshed.lease_id is None  # released in finally


async def test_process_import_file_parse_cancelled_cleanup(db_session, monkeypatch):
    """Cancelled right after lease acquired → release and return early."""
    user = await _user(db_session)
    job = await _job(db_session, status="cancelled")
    service = ImportService()

    release = AsyncMock()
    monkeypatch.setattr(service, "release_lease", release)
    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    # cancelled 分支显式 release + finally 中的兜底 release 都会走到。
    assert release.await_count >= 1


async def test_process_import_file_error_marks_failed_and_raises(
    db_session, monkeypatch
):
    user = await _user(db_session)
    job = await _job(db_session)
    # Commit setup so a later db.rollback() inside process_import_file does not
    # wipe the job row.
    await db_session.commit()
    service = ImportService()

    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    remove = Mock()
    monkeypatch.setattr(
        import_service_module.novel_service, "remove_uploaded_file", remove
    )

    with pytest.raises(RuntimeError):
        await service.process_import_file(db_session, job.id, _upload_file(), user.id)

    refreshed = await db_session.get(ImportJob, job.id)
    assert refreshed.status == "failed"
    assert refreshed.error_detail == "boom"


async def test_process_import_file_fails_closed_when_lease_unavailable(
    db_session, monkeypatch
):
    """acquire_lease returns False → job untouched."""
    user = await _user(db_session)
    job = await _job(
        db_session,
        status="uploading",
        lease_id="held",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    service = ImportService()
    monkeypatch.setattr(
        import_service_module.novel_service, "upload_novel", AsyncMock()
    )
    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    assert (await db_session.get(ImportJob, job.id)).status == "uploading"


async def test_process_import_file_cancelled_after_upload_cleans_up(
    db_session, monkeypatch
):
    """Job cancelled during upload → temp file removed, lease released."""
    user = await _user(db_session)
    job = await _job(db_session)
    service = ImportService()

    async def fake_upload(file):
        job.status = "cancelled"
        await db_session.flush()
        return "/tmp/uploaded.txt", "正文"

    monkeypatch.setattr(
        import_service_module.novel_service, "upload_novel", fake_upload
    )
    remove = Mock()
    monkeypatch.setattr(
        import_service_module.novel_service, "remove_uploaded_file", remove
    )

    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    remove.assert_called_once_with("/tmp/uploaded.txt")


async def test_process_import_file_cancelled_after_parse_cleans_up(
    db_session, monkeypatch
):
    user = await _user(db_session)
    job = await _job(db_session)
    service = ImportService()

    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/p.txt", "正文")),
    )

    def fake_parse(content):
        job.status = "cancelled"
        return _chapters()

    monkeypatch.setattr(import_service_module.novel_service, "parse_novel", fake_parse)
    remove = Mock()
    monkeypatch.setattr(
        import_service_module.novel_service, "remove_uploaded_file", remove
    )

    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    remove.assert_called_once_with("/tmp/p.txt")


async def test_process_import_file_cancelled_after_create_cleans_up(
    db_session, monkeypatch
):
    user = await _user(db_session)
    novel = Novel(title="cancelled novel", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    job = await _job(db_session)
    service = ImportService()

    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/c.txt", "正文")),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "parse_novel",
        Mock(return_value=_chapters()),
    )

    async def fake_create(db, **kwargs):
        job.status = "cancelled"
        await db_session.flush()
        return _Novel(id=novel.id)

    monkeypatch.setattr(
        import_service_module.novel_service, "create_novel_record", fake_create
    )
    remove = Mock()
    monkeypatch.setattr(
        import_service_module.novel_service, "remove_uploaded_file", remove
    )

    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    remove.assert_called_once_with("/tmp/c.txt")


async def test_process_import_file_indexing_success_path(db_session, monkeypatch):
    """PYTEST_CURRENT_TEST 为空时走 embedding→ready 索引路径。"""
    user = await _user(db_session)
    job = await _job(db_session)
    novel = Novel(title="index novel", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    service = ImportService()

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/i.txt", "正文")),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "parse_novel",
        Mock(return_value=_chapters()),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "create_novel_record",
        AsyncMock(return_value=_Novel(id=novel.id)),
    )
    from app.services import indexing_service as indexing_module

    monkeypatch.setattr(
        indexing_module.indexing_service,
        "index_novel",
        AsyncMock(return_value={"embedded_chunks": 2, "total_chunks": 2}),
    )

    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    assert (await db_session.get(ImportJob, job.id)).status == "ready"


async def test_process_import_file_indexing_failure_path(db_session, monkeypatch):
    """索引失败 → 小说标记 ready、任务仍 ready，错误被吞并继续。"""
    user = await _user(db_session)
    job = await _job(db_session)
    novel = Novel(title="index fail novel", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    service = ImportService()

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/f.txt", "正文")),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "parse_novel",
        Mock(return_value=_chapters()),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "create_novel_record",
        AsyncMock(return_value=_Novel(id=novel.id)),
    )
    from app.services import indexing_service as indexing_module

    monkeypatch.setattr(
        indexing_module.indexing_service,
        "index_novel",
        AsyncMock(side_effect=RuntimeError("index down")),
    )

    await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    refreshed = await db_session.get(ImportJob, job.id)
    assert refreshed.status == "ready"
    await db_session.refresh(novel)
    assert novel.status == "ready"


class _FakeSessionCtx:
    """async context manager yielding a stub session (no real DB connection)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


async def test_run_import_job_background_forwards(db_session, monkeypatch):
    """BackgroundTasks 入口：独立会话内转发到 process_import_file。"""
    service = ImportService()
    processed = AsyncMock()
    monkeypatch.setattr(service, "process_import_file", processed)
    fake_session = MagicMock()
    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        lambda: _FakeSessionCtx(fake_session),
    )
    await service.run_import_job_background(1, b"content", "novel.txt", 7)
    assert processed.await_count == 1
    args = processed.await_args.args
    assert args[0] is fake_session
    assert args[1] == 1
    assert args[2].filename == "novel.txt"
    assert args[3] == 7


async def test_run_import_job_background_defaults_filename(db_session, monkeypatch):
    service = ImportService()
    processed = AsyncMock()
    monkeypatch.setattr(service, "process_import_file", processed)
    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        lambda: _FakeSessionCtx(MagicMock()),
    )
    await service.run_import_job_background(1, b"content", None, 7)
    assert processed.await_args.args[2].filename == "upload.txt"


async def test_run_import_job_background_swallows_errors(db_session, monkeypatch):
    """后台入口：process_import_file 抛异常被吞并记录，不向上传播。"""
    service = ImportService()
    monkeypatch.setattr(
        service,
        "process_import_file",
        AsyncMock(side_effect=RuntimeError("import failed")),
    )
    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        lambda: _FakeSessionCtx(MagicMock()),
    )
    # 不应抛出异常（except 分支吞掉）。
    await service.run_import_job_background(1, b"content", "novel.txt", 7)


# ── compat process_import ──


async def test_process_import_reads_file_and_forwards(db_session, monkeypatch):
    user = await _user(db_session)
    job = await _job(db_session)
    service = ImportService()

    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/a.txt", "正文")),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "parse_novel",
        Mock(return_value=_chapters()),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "create_novel_record",
        AsyncMock(return_value=_Novel(id=1)),
    )
    forwarded = AsyncMock()
    monkeypatch.setattr(service, "process_import_file", forwarded)

    await service.process_import(db_session, job.id, None, _upload_file(), user.id)
    assert forwarded.await_count == 1
    _, _, forwarded_file, forwarded_owner = forwarded.await_args.args
    assert forwarded_file.filename == "test_novel.txt"
    assert forwarded_owner == user.id


async def test_process_import_defaults_filename(db_session, monkeypatch):
    user = await _user(db_session)
    job = await _job(db_session)
    service = ImportService()
    forwarded = AsyncMock()
    monkeypatch.setattr(service, "process_import_file", forwarded)
    no_name = StarletteUploadFile(file=BytesIO(b"x"), filename=None, size=1)
    await service.process_import(db_session, job.id, None, no_name, user.id)
    assert forwarded.await_args.args[2].filename == "upload.txt"


# ── state helpers not covered by the legacy test module ──


async def test_get_job_returns_existing_job(db_session):
    await _user(db_session)
    job = await _job(db_session, novel_id=None)
    service = ImportService()
    assert (await service.get_job(db_session, job.id)).id == job.id


async def test_update_job_status_with_error_detail(db_session):
    await _user(db_session)
    job = await _job(db_session, status="uploading")
    service = ImportService()
    ok = await service.update_job_status(
        db_session, job.id, "failed", 0, "导入失败", error_detail="编码错误"
    )
    assert ok is True
    assert job.status == "failed"
    assert job.error_detail == "编码错误"


async def test_update_job_status_clamps_progress(db_session):
    await _user(db_session)
    job = await _job(db_session, status="uploading")
    service = ImportService()
    assert await service.update_job_status(db_session, job.id, "detecting", 150) is True
    assert job.progress == 100
    assert await service.update_job_status(db_session, job.id, "parsing", -5) is True
    assert job.progress == 0


async def test_update_job_status_message_defaults_to_status(db_session):
    await _user(db_session)
    job = await _job(db_session, status="pending")
    service = ImportService()
    assert await service.update_job_status(db_session, job.id, "uploading", 10) is True
    assert job.message == "uploading"


async def test_acquire_lease_missing_job_false(db_session):
    service = ImportService()
    assert await service.acquire_lease(db_session, 999999) is False


async def test_release_lease_missing_job_false(db_session):
    service = ImportService()
    assert await service.release_lease(db_session, 999999, "lease") is False


async def test_release_lease_id_mismatch_false(db_session):
    await _user(db_session)
    job = await _job(db_session, status="uploading")
    service = ImportService()
    await service.acquire_lease(db_session, job.id)
    assert await service.release_lease(db_session, job.id, "wrong-lease") is False
    assert job.lease_id is not None


async def test_retry_job_missing_job_raises(db_session):
    service = ImportService()
    with pytest.raises(ValueError, match="不存在"):
        await service.retry_job(db_session, 999999)


async def test_cancel_job_missing_job_false(db_session):
    service = ImportService()
    assert await service.cancel_job(db_session, 999999) is False


async def test_compute_content_hash_str_and_bytes_consistent():
    assert ImportService.compute_content_hash(
        "abc"
    ) == ImportService.compute_content_hash(b"abc")


async def test_create_import_job_with_default_retries(db_session):
    service = ImportService()
    job = await service.create_import_job(db_session)
    assert job.status == "pending"
    assert job.max_retries == 3


async def test_create_import_job_custom_retries(db_session):
    service = ImportService()
    job = await service.create_import_job(db_session, max_retries=5)
    assert job.max_retries == 5


# ── 覆盖率补充：缺失分支 ──


async def test_update_job_status_missing_job_returns_false(db_session):
    service = ImportService()
    assert await service.update_job_status(db_session, 999999, "uploading", 10) is False


async def test_update_job_status_illegal_transition_returns_false(db_session):
    await _user(db_session)
    job = await _job(db_session, status="ready")  # ready 是终态
    service = ImportService()
    assert await service.update_job_status(db_session, job.id, "uploading", 10) is False
    assert (await db_session.get(ImportJob, job.id)).status == "ready"


async def test_get_job_by_novel_returns_latest(db_session):
    user = await _user(db_session)
    novel = Novel(title="gjb", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    await _job(db_session, novel_id=novel.id, status="failed")
    job2 = await _job(db_session, novel_id=novel.id, status="pending")
    service = ImportService()
    found = await service.get_job_by_novel(db_session, novel.id)
    assert found is not None
    assert found.id == job2.id


async def test_get_job_by_novel_missing_returns_none(db_session):
    user = await _user(db_session)
    novel = Novel(title="gjb2", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    service = ImportService()
    assert await service.get_job_by_novel(db_session, novel.id) is None


async def test_retry_job_non_failed_raises(db_session):
    await _user(db_session)
    job = await _job(db_session, status="pending")
    service = ImportService()
    with pytest.raises(ValueError, match="只能重试失败"):
        await service.retry_job(db_session, job.id)


async def test_retry_job_max_retries_raises(db_session):
    await _user(db_session)
    job = await _job(db_session, status="failed", retry_count=3, max_retries=3)
    service = ImportService()
    with pytest.raises(ValueError, match="已达到最大重试次数"):
        await service.retry_job(db_session, job.id)


async def test_retry_job_success_resets_state(db_session):
    await _user(db_session)
    job = await _job(
        db_session, status="failed", retry_count=1, max_retries=3, error_detail="boom"
    )
    service = ImportService()
    retried = await service.retry_job(db_session, job.id)
    assert retried.status == "pending"
    assert retried.progress == 0
    assert retried.message == "等待重试"
    assert retried.error_detail is None
    assert retried.retry_count == 2


async def test_cancel_job_terminal_state_returns_false(db_session):
    await _user(db_session)
    job = await _job(db_session, status="ready")
    service = ImportService()
    assert await service.cancel_job(db_session, job.id) is False
    assert (await db_session.get(ImportJob, job.id)).status == "ready"


async def test_cancel_job_success(db_session):
    await _user(db_session)
    job = await _job(db_session, status="uploading", lease_id="L1")
    service = ImportService()
    assert await service.cancel_job(db_session, job.id) is True
    refreshed = await db_session.get(ImportJob, job.id)
    assert refreshed.status == "cancelled"
    assert refreshed.message == "用户取消"
    assert refreshed.lease_id is None


async def test_recover_stale_jobs_reclaims_expired(db_session):
    await _user(db_session)
    stale = await _job(
        db_session,
        status="uploading",
        lease_id="stale",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    fresh = await _job(
        db_session,
        status="parsing",
        lease_id="fresh",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    service = ImportService()
    recovered = await service.recover_stale_jobs(db_session)
    assert stale.id in recovered
    assert fresh.id not in recovered
    refreshed = await db_session.get(ImportJob, stale.id)
    assert refreshed.status == "pending"
    assert refreshed.lease_id is None
    assert refreshed.message == "任务已恢复（服务重启）"


async def test_recover_stale_jobs_none_returns_empty(db_session):
    await _user(db_session)
    await _job(
        db_session,
        status="pending",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    await _job(
        db_session,
        status="ready",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    service = ImportService()
    assert await service.recover_stale_jobs(db_session) == []


async def test_find_duplicate_job_matches(db_session):
    user = await _user(db_session)
    novel = Novel(title="dup", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    target = await _job(
        db_session, novel_id=novel.id, status="pending", content_hash="abc123"
    )
    service = ImportService()
    found = await service.find_duplicate_job(db_session, "abc123", user.id)
    assert found is not None
    assert found.id == target.id


async def test_find_duplicate_job_excludes_failed_and_other_owner(db_session):
    user = await _user(db_session)
    other = User(
        username="otheruser",
        email="other@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    novel = Novel(title="dup2", owner_id=user.id, status="importing")
    db_session.add(novel)
    await db_session.flush()
    await _job(db_session, novel_id=novel.id, status="failed", content_hash="abc123")
    other_novel = Novel(title="dup3", owner_id=other.id, status="importing")
    db_session.add(other_novel)
    await db_session.flush()
    await _job(
        db_session, novel_id=other_novel.id, status="pending", content_hash="abc123"
    )
    service = ImportService()
    assert await service.find_duplicate_job(db_session, "abc123", user.id) is None


async def test_process_import_file_job_disappears_after_lease(db_session, monkeypatch):
    """acquire_lease 成功后 job 行被删除 → 直接返回。"""
    service = ImportService()
    monkeypatch.setattr(service, "acquire_lease", AsyncMock(return_value=True))
    await service.process_import_file(db_session, 999999, _upload_file(), 1)
    # 无异常即通过；job 不存在时 line 410 return


async def test_process_import_file_release_lease_failure_swallowed(
    db_session, monkeypatch
):
    """finally 中 release_lease 抛异常被吞掉，不影响原始异常传播。"""
    user = await _user(db_session)
    job = await _job(db_session)
    await db_session.commit()
    service = ImportService()
    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(
        service, "release_lease", AsyncMock(side_effect=RuntimeError("release down"))
    )
    with pytest.raises(RuntimeError):
        await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    assert (await db_session.get(ImportJob, job.id)).status == "failed"


async def test_process_import_file_error_update_failure_logged(db_session, monkeypatch):
    """失败后写 failed 状态时 commit 又失败 → 内层 except 吞掉。"""
    user = await _user(db_session)
    job = await _job(db_session)
    await db_session.commit()
    service = ImportService()
    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    async def failing_commit():
        raise RuntimeError("commit down")

    # 只让第二次 commit（except 内写 failed）失败；setup 已提前 commit。
    monkeypatch.setattr(db_session, "commit", failing_commit)
    monkeypatch.setattr(service, "release_lease", AsyncMock(return_value=True))
    with pytest.raises(RuntimeError):
        await service.process_import_file(db_session, job.id, _upload_file(), user.id)


async def test_process_import_file_upload_ok_parse_fails_cleans_path(
    db_session, monkeypatch
):
    """upload 成功（save_path 已设）但 parse 失败 → 清理临时文件。"""
    user = await _user(db_session)
    job = await _job(db_session)
    await db_session.commit()
    service = ImportService()
    monkeypatch.setattr(
        import_service_module.novel_service,
        "upload_novel",
        AsyncMock(return_value=("/tmp/parsed.txt", "正文")),
    )
    monkeypatch.setattr(
        import_service_module.novel_service,
        "parse_novel",
        Mock(side_effect=RuntimeError("parse boom")),
    )
    remove = Mock()
    monkeypatch.setattr(
        import_service_module.novel_service, "remove_uploaded_file", remove
    )
    with pytest.raises(RuntimeError):
        await service.process_import_file(db_session, job.id, _upload_file(), user.id)
    remove.assert_called_once_with("/tmp/parsed.txt")
    assert (await db_session.get(ImportJob, job.id)).status == "failed"
