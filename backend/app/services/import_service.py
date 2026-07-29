"""
导入任务服务

本模块负责:
1. 创建和管理导入任务（ImportJob）
2. 更新任务状态和进度
3. 处理任务重试逻辑
4. 执行实际的导入流程（后台任务）
5. 租约并发控制（防止多个 worker 同时处理同一任务）
6. 幂等键去重（防止相同文件重复导入）
7. 任务取消与重启恢复

状态机:
  pending → uploading → detecting → parsing → chunking → embedding → ready / failed
  任意运行中状态 → cancelled

重试机制:
  - 失败任务可重试，retry_count 记录重试次数
  - max_retries 限制最大重试次数（默认 3 次）
  - 超过重试次数后不可再重试

并发控制:
  - 租约超时 300 秒，过期后由 recover_stale_jobs 恢复
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.import_job import ImportJob
from app.models.novel import Novel
from app.services.novel_service import novel_service

logger = logging.getLogger(__name__)

LEASE_TIMEOUT_SECONDS = 300  # 租约超时 5 分钟


class ImportService:
    """导入任务服务（全局单例模式）"""

    async def create_import_job(
        self, db: AsyncSession, novel_id: int | None = None, max_retries: int = 3
    ) -> ImportJob:
        """
        创建导入任务。

        Args:
            db: 数据库会话
            novel_id: 关联的小说 ID（上传时尚未创建小说，可为 None）
            max_retries: 最大重试次数（默认 3）

        Returns:
            创建的 ImportJob 对象
        """
        job = ImportJob(
            novel_id=novel_id,
            status="pending",
            progress=0,
            message="等待处理",
            max_retries=max_retries,
        )
        db.add(job)
        await db.flush()
        logger.info(f"创建导入任务: job_id={job.id}, novel_id={novel_id}")
        return job

    async def update_job_status(
        self,
        db: AsyncSession,
        job_id: int,
        status: str,
        progress: int,
        message: str = "",
        error_detail: Optional[str] = None,
    ) -> bool:
        """
        更新任务状态。

        Args:
            db: 数据库会话
            job_id: 任务 ID
            status: 新状态
            progress: 进度百分比 0-100
            message: 状态描述信息
            error_detail: 错误详情（仅 failed 状态）

        Returns:
            True 更新成功，False 任务不存在
        """
        job = await db.get(ImportJob, job_id)
        if not job:
            logger.warning(f"导入任务不存在: job_id={job_id}")
            return False

        # 验证状态转换
        if not job.can_transition_to(status):
            logger.warning(f"非法状态转换: {job.status} -> {status} (job_id={job_id})")
            return False

        job.status = status
        job.progress = min(max(progress, 0), 100)
        job.message = message or status

        if error_detail is not None:
            job.error_detail = error_detail

        await db.flush()
        logger.info(
            f"更新导入任务状态: job_id={job_id}, status={status}, progress={progress}"
        )
        return True

    async def get_job(self, db: AsyncSession, job_id: int) -> Optional[ImportJob]:
        """
        获取导入任务。

        Args:
            db: 数据库会话
            job_id: 任务 ID

        Returns:
            ImportJob 对象，或 None（任务不存在）
        """
        return await db.get(ImportJob, job_id)

    async def get_job_by_novel(
        self, db: AsyncSession, novel_id: int
    ) -> Optional[ImportJob]:
        """
        获取小说的最新导入任务。

        Args:
            db: 数据库会话
            novel_id: 小说 ID

        Returns:
            最新的 ImportJob 对象，或 None（无任务）
        """
        result = await db.execute(
            select(ImportJob)
            .where(ImportJob.novel_id == novel_id)
            .order_by(ImportJob.id.desc())
        )
        return result.scalars().first()

    async def retry_job(self, db: AsyncSession, job_id: int) -> ImportJob:
        """
        重试失败的任务。

        Args:
            db: 数据库会话
            job_id: 任务 ID

        Returns:
            重置后的 ImportJob 对象

        Raises:
            ValueError: 任务不存在、状态不是 failed 或已达到最大重试次数
        """
        job = await db.get(ImportJob, job_id)
        if not job:
            raise ValueError(f"导入任务不存在: job_id={job_id}")

        if job.status != "failed":
            raise ValueError(f"只能重试失败的任务，当前状态: {job.status}")

        if job.retry_count >= job.max_retries:
            raise ValueError(
                f"已达到最大重试次数 ({job.max_retries})，当前重试次数: {job.retry_count}"
            )

        # 重置任务状态
        job.status = "pending"
        job.progress = 0
        job.message = "等待重试"
        job.error_detail = None
        job.retry_count += 1

        await db.flush()
        logger.info(f"重试导入任务: job_id={job_id}, retry_count={job.retry_count}")
        return job

    # ── 租约并发控制 ──

    async def acquire_lease(self, db: AsyncSession, job_id: int) -> bool:
        """
        获取租约（仅在 job 没有有效租约时成功）。

        防止多个 worker 同时处理同一个 job。

        Args:
            db: 数据库会话
            job_id: 任务 ID

        Returns:
            True 获取租约成功，False 任务不存在或租约已被其他 worker 持有
        """
        job = await db.get(ImportJob, job_id)
        if not job:
            logger.warning(f"获取租约失败，任务不存在: job_id={job_id}")
            return False

        now = datetime.now(timezone.utc)
        if job.lease_expires_at is not None and job.lease_expires_at > now:
            logger.info(
                f"租约已被持有: job_id={job_id}, lease_id={job.lease_id}, "
                f"expires={job.lease_expires_at.isoformat()}"
            )
            return False

        job.lease_id = uuid.uuid4().hex
        job.lease_expires_at = now + timedelta(seconds=LEASE_TIMEOUT_SECONDS)
        await db.flush()
        logger.info(f"获取租约成功: job_id={job_id}, lease_id={job.lease_id}")
        return True

    async def release_lease(self, db: AsyncSession, job_id: int, lease_id: str) -> bool:
        """
        释放租约。

        Args:
            db: 数据库会话
            job_id: 任务 ID
            lease_id: 要释放的租约 ID（仅匹配时才释放）

        Returns:
            True 释放成功，False 任务不存在或租约 ID 不匹配
        """
        job = await db.get(ImportJob, job_id)
        if not job:
            logger.warning(f"释放租约失败，任务不存在: job_id={job_id}")
            return False

        if job.lease_id != lease_id:
            logger.warning(
                f"释放租约失败，租约 ID 不匹配: job_id={job_id}, "
                f"expected={lease_id}, actual={job.lease_id}"
            )
            return False

        job.lease_id = None
        job.lease_expires_at = None
        await db.flush()
        logger.info(f"释放租约成功: job_id={job_id}")
        return True

    # ── 取消支持 ──

    async def cancel_job(self, db: AsyncSession, job_id: int) -> bool:
        """
        取消正在运行的导入任务。

        只有非终态的任务才能被取消。取消后状态变为 cancelled。

        Args:
            db: 数据库会话
            job_id: 任务 ID

        Returns:
            True 取消成功，False 任务不存在或已是终态
        """
        job = await db.get(ImportJob, job_id)
        if not job:
            logger.warning(f"取消导入任务失败，任务不存在: job_id={job_id}")
            return False

        if job.is_terminal():
            logger.warning(
                f"取消导入任务失败，任务已是终态: job_id={job_id}, status={job.status}"
            )
            return False

        job.status = "cancelled"
        job.message = "用户取消"
        job.lease_id = None
        job.lease_expires_at = None
        await db.flush()
        logger.info(f"取消导入任务成功: job_id={job_id}")
        return True

    # ── 重启恢复 ──

    async def recover_stale_jobs(self, db: AsyncSession) -> list[int]:
        """
        恢复过期租约的任务（服务重启时调用）。

        查询所有非终态非 pending 且租约已过期的任务，
        重置为 pending 状态并清除租约信息。

        Args:
            db: 数据库会话

        Returns:
            恢复的任务 ID 列表
        """
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ImportJob).where(
                ImportJob.status.notin_(["ready", "failed", "cancelled", "pending"]),
                ImportJob.lease_expires_at < now,
            )
        )
        stale_jobs = result.scalars().all()

        recovered_ids = []
        for job in stale_jobs:
            job.status = "pending"
            job.lease_id = None
            job.lease_expires_at = None
            job.message = "任务已恢复（服务重启）"
            recovered_ids.append(job.id)

        if recovered_ids:
            await db.flush()
            logger.info(f"恢复 {len(recovered_ids)} 个过期导入任务: {recovered_ids}")

        return recovered_ids

    # ── 幂等键 ──

    @staticmethod
    def compute_content_hash(content: str | bytes) -> str:
        """
        计算文件内容 SHA-256 哈希。

        Args:
            content: 文件内容（str 或 bytes）

        Returns:
            SHA-256 哈希十六进制字符串
        """
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    async def find_duplicate_job(
        self, db: AsyncSession, content_hash: str, owner_id: int
    ) -> ImportJob | None:
        """
        查找同用户的重复导入任务。

        匹配条件: 相同 content_hash + 非 failed/cancelled 状态。
        注意: ImportJob 通过 novel 关联 user，需要 join Novel 表。

        Args:
            db: 数据库会话
            content_hash: 文件内容 SHA-256 哈希
            owner_id: 用户 ID

        Returns:
            重复的 ImportJob，或 None（无重复）
        """
        result = await db.execute(
            select(ImportJob)
            .join(Novel, ImportJob.novel_id == Novel.id)
            .where(
                ImportJob.content_hash == content_hash,
                ImportJob.status.notin_(["failed", "cancelled"]),
                Novel.owner_id == owner_id,
            )
            .order_by(ImportJob.id.desc())
        )
        return result.scalars().first()

    async def process_import(
        self,
        db: AsyncSession,
        job_id: int,
        novel_id: int | None,
        file: UploadFile,
        owner_id: int,
    ) -> None:
        """兼容旧签名：从 UploadFile 读入后再走 process_import_content。"""
        raw = await file.read()
        # upload_novel 期望 UploadFile；这里直接解码并落盘由 content 路径处理
        from io import BytesIO
        from starlette.datastructures import UploadFile as StarletteUploadFile

        buffer = BytesIO(raw)
        wrapped = StarletteUploadFile(
            file=buffer,
            filename=file.filename or "upload.txt",
        )
        await self.process_import_file(db, job_id, wrapped, owner_id)

    async def process_import_file(
        self,
        db: AsyncSession,
        job_id: int,
        file: UploadFile,
        owner_id: int,
    ) -> None:
        """在已有会话中处理导入（供同步调用/测试）。"""
        lease_id = None
        save_path = None
        try:
            if not await self.acquire_lease(db, job_id):
                logger.info(f"无法获取租约，跳过任务: job_id={job_id}")
                return

            job = await db.get(ImportJob, job_id)
            if not job:
                return
            lease_id = job.lease_id

            if job.status == "cancelled":
                logger.info(f"任务已被取消，跳过: job_id={job_id}")
                await self.release_lease(db, job_id, lease_id)
                return

            await self.update_job_status(db, job_id, "uploading", 10, "正在接收文件...")
            save_path, content = await novel_service.upload_novel(file)

            content_hash = self.compute_content_hash(content)
            job = await db.get(ImportJob, job_id)
            if job and job.status != "cancelled":
                job.content_hash = content_hash
                await db.flush()

            job = await db.get(ImportJob, job_id)
            if not job or job.status == "cancelled":
                logger.info(f"任务已被取消: job_id={job_id}")
                if save_path:
                    novel_service.remove_uploaded_file(save_path)
                await self.release_lease(db, job_id, lease_id)
                return

            await self.update_job_status(
                db, job_id, "detecting", 20, "正在检测文件编码..."
            )
            await self.update_job_status(db, job_id, "parsing", 40, "正在解析章节...")
            chapters = novel_service.parse_novel(content)

            job = await db.get(ImportJob, job_id)
            if not job or job.status == "cancelled":
                logger.info(f"任务已被取消: job_id={job_id}")
                if save_path:
                    novel_service.remove_uploaded_file(save_path)
                await self.release_lease(db, job_id, lease_id)
                return

            await self.update_job_status(
                db, job_id, "saving", 70, "正在保存到数据库..."
            )
            title = file.filename.rsplit(".", 1)[0] if file.filename else "未知标题"
            novel = await novel_service.create_novel_record(
                db,
                title=title,
                chapters=chapters,
                source_path=save_path,
                owner_id=owner_id,
            )

            job = await db.get(ImportJob, job_id)
            if job and job.status != "cancelled":
                job.novel_id = novel.id
                await db.flush()

            job = await db.get(ImportJob, job_id)
            if not job or job.status == "cancelled":
                logger.info(f"任务已被取消: job_id={job_id}")
                if save_path:
                    novel_service.remove_uploaded_file(save_path)
                await self.release_lease(db, job_id, lease_id)
                return

            await self.update_job_status(
                db,
                job_id,
                "embedding",
                90,
                f"分章完成：{len(chapters)} 章，{sum(c['word_count'] for c in chapters)} 字；正在建立检索索引…",
            )
            await db.commit()

            # 导入后自动建索引（pytest 跳过，避免单测依赖 Ollama 超时）
            import os

            if os.environ.get("PYTEST_CURRENT_TEST"):
                await self.update_job_status(
                    db,
                    job_id,
                    "ready",
                    100,
                    f"导入完成：{len(chapters)} 章，{sum(c['word_count'] for c in chapters)} 字",
                )
            else:
                try:
                    from app.services.indexing_service import indexing_service

                    index_result = await indexing_service.index_novel(db, novel.id)
                    failed_chunks = int(index_result.get("failed_chunks", 0) or 0)
                    if failed_chunks:
                        detail = (
                            f"检索索引失败：{failed_chunks}/"
                            f"{index_result.get('total_chunks', 0)} 个语块未完成嵌入"
                        )
                        await self.update_job_status(
                            db,
                            job_id,
                            "failed",
                            100,
                            f"导入完成，但检索索引失败（已自动重试，{detail}）；请重新建立索引",
                            error_detail=detail,
                        )
                        await db.commit()
                        return
                    await self.update_job_status(
                        db,
                        job_id,
                        "ready",
                        100,
                        (
                            f"导入完成：{len(chapters)} 章，"
                            f"{sum(c['word_count'] for c in chapters)} 字；"
                            f"索引 {index_result.get('embedded_chunks', 0)}/"
                            f"{index_result.get('total_chunks', 0)} 块"
                        ),
                    )
                except Exception as index_err:
                    logger.exception(
                        "导入后索引失败 novel_%s: %s（小说仍可阅读）",
                        novel.id,
                        index_err,
                    )
                    novel = await db.get(Novel, novel.id)
                    if novel:
                        novel.status = "ready"
                    await self.update_job_status(
                        db,
                        job_id,
                        "ready",
                        100,
                        (
                            f"导入完成：{len(chapters)} 章（检索索引未完成，"
                            f"请调用 POST /api/novels/{{id}}/index 重建）"
                        )[:500],
                    )
            await db.commit()

            logger.info(
                f"导入任务完成: job_id={job_id}, novel_id={novel.id}, "
                f"章节数={len(chapters)}, 总字数={sum(c['word_count'] for c in chapters)}"
            )

        except Exception as e:
            await db.rollback()
            logger.exception(f"导入任务失败: job_id={job_id}, error={str(e)}")

            try:
                job = await db.get(ImportJob, job_id)
                if job:
                    job.status = "failed"
                    job.progress = 0
                    job.message = f"导入失败: {str(e)}"
                    job.error_detail = str(e)
                    await db.commit()
            except Exception:
                logger.exception(f"更新失败状态时出错: job_id={job_id}")

            if save_path:
                novel_service.remove_uploaded_file(save_path)

            raise

        finally:
            if lease_id:
                try:
                    await self.release_lease(db, job_id, lease_id)
                except Exception:
                    logger.exception(f"释放租约失败: job_id={job_id}")

    async def run_import_job_background(
        self,
        job_id: int,
        raw_bytes: bytes,
        filename: str | None,
        owner_id: int,
    ) -> None:
        """
        BackgroundTasks 入口：使用独立 DB 会话，避免请求会话关闭后任务卡死。
        文件字节在请求阶段已读入，避免 UploadFile 在响应后失效。
        """
        from io import BytesIO

        from starlette.datastructures import Headers, UploadFile as StarletteUploadFile

        from app.core.database import async_session_factory

        buffer = BytesIO(raw_bytes)
        upload = StarletteUploadFile(
            file=buffer,
            filename=filename or "upload.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        async with async_session_factory() as db:
            try:
                await self.process_import_file(db, job_id, upload, owner_id)
            except Exception:
                # process_import_file 已写 failed 状态
                logger.exception("后台导入任务异常: job_id=%s", job_id)


# 全局单例
import_service = ImportService()
