"""
导入任务服务

本模块负责:
1. 创建和管理导入任务（ImportJob）
2. 更新任务状态和进度
3. 处理任务重试逻辑
4. 执行实际的导入流程（后台任务）

状态机:
  pending → uploading → detecting → parsing → chunking → embedding → ready / failed

重试机制:
  - 失败任务可重试，retry_count 记录重试次数
  - max_retries 限制最大重试次数（默认 3 次）
  - 超过重试次数后不可再重试
"""

import logging
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.import_job import ImportJob
from app.models.novel import Novel
from app.services.novel_service import novel_service

logger = logging.getLogger(__name__)


class ImportService:
    """导入任务服务（全局单例模式）"""

    async def create_import_job(
        self, db: AsyncSession, novel_id: int, max_retries: int = 3
    ) -> ImportJob:
        """
        创建导入任务。

        Args:
            db: 数据库会话
            novel_id: 关联的小说 ID
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
            logger.warning(
                f"非法状态转换: {job.status} -> {status} (job_id={job_id})"
            )
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
        logger.info(
            f"重试导入任务: job_id={job_id}, retry_count={job.retry_count}"
        )
        return job

    async def process_import(
        self,
        db: AsyncSession,
        job_id: int,
        novel_id: int,
        file: UploadFile,
        owner_id: int,
    ) -> None:
        """
        处理导入任务（后台执行）。

        这是一个后台任务，由 FastAPI 的 BackgroundTasks 调用。

        Args:
            db: 数据库会话
            job_id: 导入任务 ID
            novel_id: 关联的小说 ID
            file: 上传的文件
            owner_id: 上传者用户 ID
        """
        save_path = None
        try:
            # 1. uploading
            await self.update_job_status(
                db, job_id, "uploading", 10, "正在接收文件..."
            )
            save_path, content = await novel_service.upload_novel(file)

            # 2. detecting + parsing
            await self.update_job_status(
                db, job_id, "detecting", 20, "正在检测文件编码..."
            )
            await self.update_job_status(
                db, job_id, "parsing", 40, "正在解析章节..."
            )
            chapters = novel_service.parse_novel(content)

            # 3. saving
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

            # 更新 novel_id 关联
            job = await db.get(ImportJob, job_id)
            if job:
                job.novel_id = novel.id
                await db.flush()

            # 4. ready
            await self.update_job_status(
                db,
                job_id,
                "ready",
                100,
                f"导入完成：{len(chapters)} 章，{sum(c['word_count'] for c in chapters)} 字",
            )
            await db.commit()

            logger.info(
                f"导入任务完成: job_id={job_id}, novel_id={novel.id}, "
                f"章节数={len(chapters)}, 总字数={sum(c['word_count'] for c in chapters)}"
            )

        except Exception as e:
            await db.rollback()
            logger.exception(f"导入任务失败: job_id={job_id}, error={str(e)}")

            # 尝试更新任务状态为 failed
            try:
                # 重新获取会话（因为可能已经 rollback）
                job = await db.get(ImportJob, job_id)
                if job:
                    job.status = "failed"
                    job.progress = 0
                    job.message = f"导入失败: {str(e)}"
                    job.error_detail = str(e)
                    await db.commit()
            except Exception:
                logger.exception(f"更新失败状态时出错: job_id={job_id}")

            # 清理上传的文件
            if save_path:
                novel_service.remove_uploaded_file(save_path)

            raise


# 全局单例
import_service = ImportService()
