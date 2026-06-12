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
import pytest_asyncio
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
