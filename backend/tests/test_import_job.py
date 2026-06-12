"""
导入任务系统测试

覆盖范围:
- ImportJob 模型创建和关系
- ImportService 状态管理
- 状态机验证
- 重试逻辑
- 错误处理

注意:
- 使用内存 SQLite，每个测试独立数据库
- 测试 ImportJob 模型和 ImportService 服务
"""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.import_job import ImportJob
from app.models.novel import Novel
from app.models.user import User
from app.services.import_service import ImportService


async def _create_test_user(db: AsyncSession) -> User:
    """创建测试用户（所有测试的前置条件）"""
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _create_test_novel(db: AsyncSession, user: User) -> Novel:
    """创建测试小说"""
    novel = Novel(
        title="测试小说",
        status="importing",
        owner_id=user.id,
    )
    db.add(novel)
    await db.flush()
    return novel


# ─────────── 模型测试 ───────────


@pytest.mark.asyncio
async def test_import_job_creation(db_session: AsyncSession):
    """测试创建导入任务"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入任务
    job = ImportJob(
        novel_id=novel.id,
        status="pending",
        progress=0,
        message="等待处理",
    )
    db_session.add(job)
    await db_session.flush()

    # 验证
    assert job.id is not None
    assert job.novel_id == novel.id
    assert job.status == "pending"
    assert job.progress == 0
    assert job.message == "等待处理"
    assert job.retry_count == 0
    assert job.max_retries == 3
    assert job.error_detail is None


@pytest.mark.asyncio
async def test_import_job_relationship(db_session: AsyncSession):
    """测试导入任务与小说的关系"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入任务
    job = ImportJob(
        novel_id=novel.id,
        status="pending",
        progress=0,
    )
    db_session.add(job)
    await db_session.flush()

    # 验证关系（使用 refresh 加载关系，避免 async lazy load 问题）
    await db_session.refresh(job, ["novel"])
    assert job.novel is not None
    assert job.novel.id == novel.id

    await db_session.refresh(novel, ["import_jobs"])
    assert len(novel.import_jobs) == 1
    assert novel.import_jobs[0].id == job.id


@pytest.mark.asyncio
async def test_import_job_status_transitions(db_session: AsyncSession):
    """测试状态机转换"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入任务
    job = ImportJob(
        novel_id=novel.id,
        status="pending",
        progress=0,
    )
    db_session.add(job)
    await db_session.flush()

    # 测试允许的状态转换
    assert job.can_transition_to("uploading") is True
    assert job.can_transition_to("failed") is False  # pending 不能直接到 failed

    # 更新状态
    job.status = "uploading"
    assert job.can_transition_to("detecting") is True
    assert job.can_transition_to("failed") is True

    job.status = "detecting"
    assert job.can_transition_to("parsing") is True
    assert job.can_transition_to("failed") is True

    job.status = "parsing"
    assert job.can_transition_to("chunking") is True
    assert job.can_transition_to("saving") is True
    assert job.can_transition_to("failed") is True

    job.status = "chunking"
    assert job.can_transition_to("embedding") is True
    assert job.can_transition_to("saving") is True
    assert job.can_transition_to("failed") is True

    job.status = "embedding"
    assert job.can_transition_to("ready") is True
    assert job.can_transition_to("failed") is True

    job.status = "saving"
    assert job.can_transition_to("ready") is True
    assert job.can_transition_to("failed") is True

    # 终态测试
    job.status = "ready"
    assert job.can_transition_to("pending") is False
    assert job.can_transition_to("uploading") is False

    # 失败状态可以重试
    job.status = "failed"
    assert job.can_transition_to("pending") is True
    assert job.can_transition_to("uploading") is False


@pytest.mark.asyncio
async def test_import_job_retry_logic(db_session: AsyncSession):
    """测试重试逻辑"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建失败的任务
    job = ImportJob(
        novel_id=novel.id,
        status="failed",
        progress=0,
        message="导入失败",
        retry_count=0,
        max_retries=3,
    )
    db_session.add(job)
    await db_session.flush()

    # 测试可以重试
    assert job.can_retry() is True

    # 测试重试次数限制
    job.retry_count = 3
    assert job.can_retry() is False

    # 测试非失败状态不能重试
    job.status = "pending"
    job.retry_count = 0
    assert job.can_retry() is False


# ─────────── 服务测试 ───────────


@pytest.mark.asyncio
async def test_import_service_create_job(db_session: AsyncSession):
    """测试创建导入任务服务"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入服务
    service = ImportService()

    # 创建导入任务
    job = await service.create_import_job(db_session, novel.id)

    # 验证
    assert job.id is not None
    assert job.novel_id == novel.id
    assert job.status == "pending"
    assert job.progress == 0
    assert job.message == "等待处理"


@pytest.mark.asyncio
async def test_import_service_update_job_status(db_session: AsyncSession):
    """测试更新任务状态服务"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入任务
    job = ImportJob(
        novel_id=novel.id,
        status="pending",
        progress=0,
    )
    db_session.add(job)
    await db_session.flush()

    # 创建导入服务
    service = ImportService()

    # 更新状态
    result = await service.update_job_status(
        db_session, job.id, "uploading", 10, "正在上传文件..."
    )

    # 验证
    assert result is True
    assert job.status == "uploading"
    assert job.progress == 10
    assert job.message == "正在上传文件..."


@pytest.mark.asyncio
async def test_import_service_get_job_by_novel(db_session: AsyncSession):
    """测试获取小说的最新导入任务"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建第一个导入任务
    job1 = ImportJob(
        novel_id=novel.id,
        status="pending",
        progress=0,
    )
    db_session.add(job1)
    await db_session.flush()

    # 创建导入服务
    service = ImportService()

    # 验证能获取到任务
    latest_job = await service.get_job_by_novel(db_session, novel.id)
    assert latest_job is not None
    assert latest_job.id == job1.id
    assert latest_job.status == "pending"

    # 更新任务状态为失败
    job1.status = "failed"
    await db_session.flush()

    # 创建第二个任务（模拟重试）
    job2 = ImportJob(
        novel_id=novel.id,
        status="uploading",
        progress=10,
    )
    db_session.add(job2)
    await db_session.flush()

    # 获取最新任务（按 id 降序，job2 的 id 更大）
    latest_job = await service.get_job_by_novel(db_session, novel.id)
    assert latest_job is not None
    assert latest_job.id == job2.id
    assert latest_job.status == "uploading"


@pytest.mark.asyncio
async def test_import_service_retry_job(db_session: AsyncSession):
    """测试重试任务服务"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建失败的任务
    job = ImportJob(
        novel_id=novel.id,
        status="failed",
        progress=0,
        message="导入失败",
        error_detail="文件编码错误",
        retry_count=0,
        max_retries=3,
    )
    db_session.add(job)
    await db_session.flush()

    # 创建导入服务
    service = ImportService()

    # 重试任务
    retried_job = await service.retry_job(db_session, job.id)

    # 验证
    assert retried_job.status == "pending"
    assert retried_job.progress == 0
    assert retried_job.message == "等待重试"
    assert retried_job.error_detail is None
    assert retried_job.retry_count == 1


@pytest.mark.asyncio
async def test_import_service_retry_job_max_retries(db_session: AsyncSession):
    """测试重试次数限制"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建失败的任务，已达到最大重试次数
    job = ImportJob(
        novel_id=novel.id,
        status="failed",
        progress=0,
        message="导入失败",
        retry_count=3,
        max_retries=3,
    )
    db_session.add(job)
    await db_session.flush()

    # 创建导入服务
    service = ImportService()

    # 尝试重试，应该抛出异常
    with pytest.raises(ValueError) as exc_info:
        await service.retry_job(db_session, job.id)

    assert "已达到最大重试次数" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_service_retry_job_not_failed(db_session: AsyncSession):
    """测试重试非失败任务"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建进行中的任务
    job = ImportJob(
        novel_id=novel.id,
        status="uploading",
        progress=10,
        message="正在上传",
    )
    db_session.add(job)
    await db_session.flush()

    # 创建导入服务
    service = ImportService()

    # 尝试重试，应该抛出异常
    with pytest.raises(ValueError) as exc_info:
        await service.retry_job(db_session, job.id)

    assert "只能重试失败的任务" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_service_state_machine_flow(db_session: AsyncSession):
    """测试完整的状态机流转"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入服务
    service = ImportService()

    # 创建导入任务
    job = await service.create_import_job(db_session, novel.id)

    # 模拟完整的导入流程
    await service.update_job_status(db_session, job.id, "uploading", 10, "正在上传文件...")
    await service.update_job_status(db_session, job.id, "detecting", 20, "正在检测编码...")
    await service.update_job_status(db_session, job.id, "parsing", 40, "正在解析章节...")
    await service.update_job_status(db_session, job.id, "chunking", 60, "正在分块...")
    await service.update_job_status(db_session, job.id, "embedding", 80, "正在向量化...")
    await service.update_job_status(db_session, job.id, "ready", 100, "导入完成")

    # 验证最终状态
    assert job.status == "ready"
    assert job.progress == 100
    assert job.message == "导入完成"


@pytest.mark.asyncio
async def test_import_service_error_handling(db_session: AsyncSession):
    """测试错误处理"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入服务
    service = ImportService()

    # 创建导入任务
    job = await service.create_import_job(db_session, novel.id)

    # 模拟导入失败
    await service.update_job_status(db_session, job.id, "uploading", 10, "正在上传文件...")
    await service.update_job_status(
        db_session,
        job.id,
        "failed",
        0,
        "导入失败: 文件编码错误",
        error_detail="无法识别文件编码，请使用 UTF-8 或 GBK 编码",
    )

    # 验证失败状态
    assert job.status == "failed"
    assert job.progress == 0
    assert job.message == "导入失败: 文件编码错误"
    assert job.error_detail == "无法识别文件编码，请使用 UTF-8 或 GBK 编码"


@pytest.mark.asyncio
async def test_import_service_invalid_status_transition(db_session: AsyncSession):
    """测试非法状态转换"""
    user = await _create_test_user(db_session)
    novel = await _create_test_novel(db_session, user)

    # 创建导入任务
    job = ImportJob(
        novel_id=novel.id,
        status="pending",
        progress=0,
    )
    db_session.add(job)
    await db_session.flush()

    # 创建导入服务
    service = ImportService()

    # 尝试非法状态转换（pending -> ready）
    result = await service.update_job_status(db_session, job.id, "ready", 100, "导入完成")

    # 验证转换失败
    assert result is False
    assert job.status == "pending"  # 状态未改变


@pytest.mark.asyncio
async def test_import_service_nonexistent_job(db_session: AsyncSession):
    """测试获取不存在的任务"""
    # 创建导入服务
    service = ImportService()

    # 尝试获取不存在的任务
    job = await service.get_job(db_session, 999)

    # 验证返回 None
    assert job is None


@pytest.mark.asyncio
async def test_import_service_update_nonexistent_job(db_session: AsyncSession):
    """测试更新不存在的任务"""
    # 创建导入服务
    service = ImportService()

    # 尝试更新不存在的任务
    result = await service.update_job_status(db_session, 999, "uploading", 10, "正在上传...")

    # 验证返回 False
    assert result is False


# ─────────── 租约并发控制测试 ───────────


class TestImportJobLease:
    """租约并发控制测试"""

    @pytest.mark.asyncio
    async def test_acquire_lease_success(self, db_session: AsyncSession):
        """测试成功获取租约"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="pending",
            progress=0,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()
        result = await service.acquire_lease(db_session, job.id)

        assert result is True
        assert job.lease_id is not None
        assert len(job.lease_id) == 32  # uuid4().hex 长度
        assert job.lease_expires_at is not None
        # 租约过期时间应该在未来的 300 秒内
        now = datetime.now(timezone.utc)
        assert job.lease_expires_at > now
        assert job.lease_expires_at <= now + timedelta(seconds=300)

    @pytest.mark.asyncio
    async def test_acquire_lease_already_held(self, db_session: AsyncSession):
        """测试租约已被持有时无法再次获取"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=10,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 第一次获取租约成功
        result = await service.acquire_lease(db_session, job.id)
        assert result is True

        # 第二次获取租约失败（租约仍有效）
        result = await service.acquire_lease(db_session, job.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_lease_expiry(self, db_session: AsyncSession):
        """测试租约过期后可以重新获取"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=10,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 第一次获取租约
        result = await service.acquire_lease(db_session, job.id)
        assert result is True
        old_lease_id = job.lease_id

        # 手动设置租约为过期状态
        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.flush()

        # 租约过期后可以重新获取
        result = await service.acquire_lease(db_session, job.id)
        assert result is True
        assert job.lease_id != old_lease_id  # 新租约 ID 应该不同

    @pytest.mark.asyncio
    async def test_release_lease(self, db_session: AsyncSession):
        """测试释放租约"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=50,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 获取租约
        result = await service.acquire_lease(db_session, job.id)
        assert result is True
        lease_id = job.lease_id

        # 释放租约
        result = await service.release_lease(db_session, job.id, lease_id)
        assert result is True
        assert job.lease_id is None
        assert job.lease_expires_at is None

        # 释放后可以重新获取
        result = await service.acquire_lease(db_session, job.id)
        assert result is True


# ─────────── 幂等键测试 ───────────


class TestImportJobIdempotency:
    """幂等键测试"""

    def test_compute_content_hash(self):
        """测试内容哈希计算"""
        content = b"Hello, World!"
        hash_val = ImportService.compute_content_hash(content)

        # SHA-256 十六进制字符串长度应为 64
        assert len(hash_val) == 64
        # 相同内容应产生相同哈希
        assert hash_val == ImportService.compute_content_hash(content)
        # 不同内容应产生不同哈希
        assert hash_val != ImportService.compute_content_hash(b"Different content")

    @pytest.mark.asyncio
    async def test_find_duplicate_job(self, db_session: AsyncSession):
        """测试查找到重复的导入任务"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        content_hash = "a" * 64

        # 创建一个进行中的任务（带 content_hash）
        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=10,
            content_hash=content_hash,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 查找重复任务
        duplicate = await service.find_duplicate_job(db_session, content_hash, user.id)

        assert duplicate is not None
        assert duplicate.id == job.id
        assert duplicate.content_hash == content_hash

    @pytest.mark.asyncio
    async def test_duplicate_not_found_for_different_content(self, db_session: AsyncSession):
        """测试不同内容不会匹配为重复"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        # 创建一个任务（带 content_hash）
        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=10,
            content_hash="a" * 64,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 查找不同的哈希
        duplicate = await service.find_duplicate_job(db_session, "b" * 64, user.id)

        assert duplicate is None

    @pytest.mark.asyncio
    async def test_duplicate_not_found_for_failed_job(self, db_session: AsyncSession):
        """测试失败的任务不视为重复"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        content_hash = "a" * 64

        # 创建一个失败的任务
        job = ImportJob(
            novel_id=novel.id,
            status="failed",
            progress=0,
            content_hash=content_hash,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 失败的任务不应被匹配为重复
        duplicate = await service.find_duplicate_job(db_session, content_hash, user.id)

        assert duplicate is None


# ─────────── 取消测试 ───────────


class TestImportJobCancel:
    """取消测试"""

    @pytest.mark.asyncio
    async def test_cancel_running_job(self, db_session: AsyncSession):
        """测试取消正在运行的任务"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=30,
            message="正在上传文件...",
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 取消任务
        result = await service.cancel_job(db_session, job.id)

        assert result is True
        assert job.status == "cancelled"
        assert job.message == "用户取消"
        assert job.is_terminal() is True

    @pytest.mark.asyncio
    async def test_cancel_terminal_job_fails(self, db_session: AsyncSession):
        """测试取消终态任务失败"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="ready",
            progress=100,
            message="导入完成",
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 取消终态任务
        result = await service.cancel_job(db_session, job.id)

        assert result is False
        assert job.status == "ready"  # 状态不变

    @pytest.mark.asyncio
    async def test_cancel_failed_job_fails(self, db_session: AsyncSession):
        """测试取消失败的任务也失败（failed 是终态）"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="failed",
            progress=0,
            message="导入失败",
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 取消失败任务
        result = await service.cancel_job(db_session, job.id)

        assert result is False
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, db_session: AsyncSession):
        """测试取消等待中的任务"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        job = ImportJob(
            novel_id=novel.id,
            status="pending",
            progress=0,
            message="等待处理",
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 取消等待中的任务
        result = await service.cancel_job(db_session, job.id)

        assert result is True
        assert job.status == "cancelled"
        assert job.is_terminal() is True


# ─────────── 重启恢复测试 ───────────


class TestImportJobRecovery:
    """重启恢复测试"""

    @pytest.mark.asyncio
    async def test_recover_stale_jobs(self, db_session: AsyncSession):
        """测试恢复过期租约的任务"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        # 创建一个运行中但租约已过期的任务
        stale_job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=20,
            lease_id="old-lease-id",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db_session.add(stale_job)

        # 创建另一个同样过期的任务
        stale_job2 = ImportJob(
            novel_id=novel.id,
            status="parsing",
            progress=40,
            lease_id="old-lease-id-2",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        db_session.add(stale_job2)

        # 创建一个 pending 任务（不应被恢复）
        pending_job = ImportJob(
            novel_id=novel.id,
            status="pending",
            progress=0,
            lease_id=None,
            lease_expires_at=None,
        )
        db_session.add(pending_job)

        # 创建一个已完成的任务（不应被恢复）
        ready_job = ImportJob(
            novel_id=novel.id,
            status="ready",
            progress=100,
        )
        db_session.add(ready_job)

        await db_session.flush()

        service = ImportService()

        # 恢复过期租约的任务
        recovered = await service.recover_stale_jobs(db_session)

        # 验证恢复了 2 个任务
        assert len(recovered) == 2
        assert stale_job.id in recovered
        assert stale_job2.id in recovered

        # 验证恢复后的状态
        assert stale_job.status == "pending"
        assert stale_job.lease_id is None
        assert stale_job.lease_expires_at is None
        assert stale_job.message == "任务已恢复（服务重启）"

        assert stale_job2.status == "pending"
        assert stale_job2.lease_id is None
        assert stale_job2.lease_expires_at is None

        # 验证 pending 和 ready 任务未被修改
        assert pending_job.status == "pending"
        assert ready_job.status == "ready"

    @pytest.mark.asyncio
    async def test_no_recovery_for_pending(self, db_session: AsyncSession):
        """测试 pending 任务不被恢复"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        # 创建 pending 任务
        job = ImportJob(
            novel_id=novel.id,
            status="pending",
            progress=0,
            lease_id=None,
            lease_expires_at=None,
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 恢复过期租约的任务
        recovered = await service.recover_stale_jobs(db_session)

        # pending 任务不应被包含
        assert len(recovered) == 0
        assert job.status == "pending"

    @pytest.mark.asyncio
    async def test_no_recovery_for_valid_lease(self, db_session: AsyncSession):
        """测试有效租约的任务不被恢复"""
        user = await _create_test_user(db_session)
        novel = await _create_test_novel(db_session, user)

        # 创建运行中且租约有效的任务
        job = ImportJob(
            novel_id=novel.id,
            status="uploading",
            progress=20,
            lease_id="valid-lease",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(job)
        await db_session.flush()

        service = ImportService()

        # 恢复过期租约的任务
        recovered = await service.recover_stale_jobs(db_session)

        # 有效租约的任务不应被恢复
        assert len(recovered) == 0
        assert job.status == "uploading"
