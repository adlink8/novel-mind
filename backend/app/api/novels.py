"""小说管理 API — 接入 novel_service + 数据库"""

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.dependencies import require_owned_novel
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.novel import (
    NovelResponse,
    NovelListResponse,
    NovelUploadResponse,
    ChapterSummaryResponse,
    ChapterResponse,
    ReadingProgressUpdate,
    ReadingProgressResponse,
    ImportStatusResponse,
    ImportJobResponse,
)
from app.services.novel_service import novel_service
from app.services.import_service import import_service

router = APIRouter()


@router.get("")
async def list_novels(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="搜索标题或作者"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """获取小说列表（分页 + 搜索）"""
    owner_id = None if current_user.is_superuser else current_user.id
    novels, total = await novel_service.get_novels(
        db, skip=skip, limit=limit, search=search, owner_id=owner_id
    )
    items = [NovelListResponse.model_validate(n).model_dump() for n in novels]
    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post("/upload", response_model=NovelUploadResponse)
async def upload_novel(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """
    上传并解析小说 TXT 文件（支持多编码自动检测和大文件）。

    需要登录认证。

    处理流程:
    1. 创建导入任务（ImportJob）
    2. 后台处理：接收文件、检测编码、解析章节、保存到数据库
    3. 返回任务 ID 和状态
    """
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    # 创建导入任务（novel_id 为空，后续在 process_import 中关联）
    job = await import_service.create_import_job(db, novel_id=None)
    await db.commit()

    # 后台处理导入任务
    background_tasks.add_task(
        import_service.process_import,
        db,
        job.id,
        job.novel_id,
        file,
        current_user.id,
    )

    return NovelUploadResponse(
        id=job.id,
        title=file.filename.rsplit(".", 1)[0] if file.filename else "未知标题",
        status="pending",
        message="导入任务已创建，正在后台处理",
        chapter_count=0,
        word_count=0,
    )


@router.get("/{novel_id}", response_model=NovelResponse)
async def get_novel(novel: Novel = Depends(require_owned_novel)):
    """获取小说详情（含章节列表）"""
    return NovelResponse.model_validate(novel)


@router.delete("/{novel_id}")
async def delete_novel(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """删除小说（仅限所有者或超级用户）"""
    success = await novel_service.delete_novel(db, novel.id)
    if not success:
        raise HTTPException(status_code=404, detail="小说不存在")
    return {"message": "已删除"}


@router.get("/{novel_id}/chapters", response_model=list[ChapterSummaryResponse])
async def list_chapters(novel: Novel = Depends(require_owned_novel)):
    """获取小说章节列表（不含完整正文，避免大 payload）"""
    return [ChapterSummaryResponse.model_validate(ch) for ch in novel.chapters]


@router.get("/{novel_id}/chapters/{chapter_id}", response_model=ChapterResponse)
async def get_chapter(
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """获取章节内容"""
    chapter = await novel_service.get_chapter(db, chapter_id)
    if not chapter or chapter.novel_id != novel.id:
        raise HTTPException(status_code=404, detail="章节不存在")
    return ChapterResponse.model_validate(chapter)


@router.patch("/{novel_id}/progress", response_model=ReadingProgressResponse)
async def update_progress(
    data: ReadingProgressUpdate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """更新阅读进度"""
    result = await novel_service.update_reading_progress(
        db, novel.id, data.chapter_id, data.progress_percent
    )
    if not result:
        raise HTTPException(status_code=404, detail="小说或章节不存在")
    return ReadingProgressResponse(**result)


@router.get("/{novel_id}/import-status", response_model=ImportStatusResponse)
async def get_import_status(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """获取小说导入进度状态（前端轮询用）"""
    job = await import_service.get_job_by_novel(db, novel.id)
    if not job:
        # 如果数据库中没有任务记录，返回默认状态
        return ImportStatusResponse(
            novel_id=novel.id,
            stage="unknown",
            percent=0,
            message="暂无导入状态信息",
        )
    return ImportStatusResponse(
        novel_id=novel.id,
        stage=job.status,
        percent=job.progress,
        message=job.message,
    )


@router.post("/{novel_id}/import-retry", response_model=ImportJobResponse)
async def retry_import(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """重试失败的导入任务"""
    job = await import_service.get_job_by_novel(db, novel.id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到导入任务")

    # 权限检查：只有小说所有者或超级用户可以重试
    if not current_user.is_superuser and novel.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="没有权限重试此任务")

    try:
        job = await import_service.retry_job(db, job.id)
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ImportJobResponse(
        job_id=job.id,
        novel_id=job.novel_id,
        status=job.status,
        progress=job.progress,
        message=job.message,
        error_detail=job.error_detail,
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/{novel_id}/import-cancel")
async def cancel_import(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """取消正在运行的导入任务"""
    # 权限检查：只有小说所有者或超级用户可以取消
    novel = await novel_service.get_novel(db, novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    if not current_user.is_superuser and novel.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="没有权限取消此导入任务")

    job = await import_service.get_job_by_novel(db, novel.id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到导入任务")

    success = await import_service.cancel_job(db, job.id)
    if not success:
        raise HTTPException(status_code=400, detail="无法取消该导入任务（已处于终态）")

    await db.commit()
    return {"message": "已取消导入任务", "job_id": job.id}
