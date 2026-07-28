"""小说管理 API — 接入 novel_service + 数据库"""

from fastapi import (
    APIRouter,
    Depends,
    UploadFile,
    File,
    HTTPException,
    Query,
    BackgroundTasks,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.dependencies import require_owned_novel
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.novel import (
    NovelResponse,
    NovelListResponse,
    NovelUploadResponse,
    NovelUpdate,
    NovelBulkDeleteRequest,
    NovelBulkDeleteResponse,
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
    from sqlalchemy import func, select

    from app.models import TextChunk

    owner_id = None if current_user.is_superuser else current_user.id
    novels, total = await novel_service.get_novels(
        db, skip=skip, limit=limit, search=search, owner_id=owner_id
    )
    novel_ids = [n.id for n in novels]
    chunk_counts: dict[int, int] = {}
    if novel_ids:
        count_rows = await db.execute(
            select(TextChunk.novel_id, func.count(TextChunk.id))
            .where(TextChunk.novel_id.in_(novel_ids))
            .group_by(TextChunk.novel_id)
        )
        chunk_counts = {int(nid): int(cnt) for nid, cnt in count_rows.all()}

    items = []
    for n in novels:
        payload = NovelListResponse.model_validate(n).model_dump()
        payload["chunk_count"] = chunk_counts.get(n.id, 0)
        items.append(payload)
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
    1. 在请求内读完文件字节（避免 BackgroundTask 时 UploadFile 已关闭）
    2. 创建导入任务（ImportJob）
    3. 独立 DB 会话后台处理
    4. 返回 job_id 供前端轮询 GET /novels/import-jobs/{job_id}
    """
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    # 先读入内存，再丢给后台任务（请求结束后 UploadFile 流会失效）
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件内容为空")

    job = await import_service.create_import_job(db, novel_id=None)
    await db.commit()

    title = file.filename.rsplit(".", 1)[0] if file.filename else "未知标题"

    # pytest 使用 SQLite 内存库并覆盖 get_db；BackgroundTasks 里的独立
    # async_session_factory 连不上测试库，会导致轮询永远 pending。测试环境同步导入。
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        from io import BytesIO

        from starlette.datastructures import Headers, UploadFile as StarletteUploadFile

        wrapped = StarletteUploadFile(
            file=BytesIO(raw_bytes),
            filename=file.filename or "upload.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        await import_service.process_import_file(db, job.id, wrapped, current_user.id)
        await db.refresh(job)
        return NovelUploadResponse(
            id=job.id,
            job_id=job.id,
            novel_id=job.novel_id,
            title=title,
            status=job.status,
            message=job.message or "导入完成",
            chapter_count=0,
            word_count=0,
        )

    background_tasks.add_task(
        import_service.run_import_job_background,
        job.id,
        raw_bytes,
        file.filename,
        current_user.id,
    )

    return NovelUploadResponse(
        id=job.id,
        job_id=job.id,
        novel_id=None,
        title=title,
        status="pending",
        message="导入任务已创建，正在后台处理",
        chapter_count=0,
        word_count=0,
    )


@router.get("/import-jobs/{job_id}", response_model=ImportStatusResponse)
async def get_import_job_status(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """按 job_id 查询导入进度（上传后应轮询此接口，而不是 novel_id）。"""
    job = await import_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="导入任务不存在")

    # 任务已关联小说时做所有权校验；未关联时仅登录用户可见（job 本身无 owner 字段）
    if job.novel_id is not None and not current_user.is_superuser:
        novel = await db.get(Novel, job.novel_id)
        if novel is None or novel.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="导入任务不存在")

    return ImportStatusResponse(
        job_id=job.id,
        novel_id=job.novel_id,
        stage=job.status,
        percent=job.progress,
        message=job.message or "",
    )


@router.delete("/bulk", response_model=NovelBulkDeleteResponse)
async def bulk_delete_novels(
    data: NovelBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """批量删除当前用户可管理的小说。"""
    owner_id = None if current_user.is_superuser else current_user.id
    deleted_ids, skipped_ids = await novel_service.delete_novels(
        db, data.novel_ids, owner_id=owner_id
    )
    return NovelBulkDeleteResponse(deleted_ids=deleted_ids, skipped_ids=skipped_ids)


@router.get("/{novel_id}", response_model=NovelResponse)
async def get_novel(novel: Novel = Depends(require_owned_novel)):
    """获取小说详情（含章节列表）"""
    return NovelResponse.model_validate(novel)


@router.patch("/{novel_id}", response_model=NovelListResponse)
async def update_novel(
    data: NovelUpdate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """更新小说名称等元信息（仅限所有者或超级用户）。"""
    values = data.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    try:
        updated = await novel_service.update_novel(db, novel, values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NovelListResponse.model_validate(updated)


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
async def list_chapters(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """获取小说章节列表（不含完整正文，避免大 payload）"""
    from sqlalchemy import select

    from app.models.novel import Chapter

    # 只取目录字段；content 为 deferred，避免加载百万字正文
    result = await db.execute(
        select(Chapter)
        .where(Chapter.novel_id == novel.id)
        .order_by(Chapter.chapter_number, Chapter.id)
    )
    chapters = result.scalars().all()
    return [ChapterSummaryResponse.model_validate(ch) for ch in chapters]


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
    """获取小说导入进度状态（前端轮询用；优先使用 /import-jobs/{job_id}）"""
    job = await import_service.get_job_by_novel(db, novel.id)
    if not job:
        # 如果数据库中没有任务记录，返回默认状态
        return ImportStatusResponse(
            job_id=None,
            novel_id=novel.id,
            stage="unknown",
            percent=0,
            message="暂无导入状态信息",
        )
    return ImportStatusResponse(
        job_id=job.id,
        novel_id=novel.id,
        stage=job.status,
        percent=job.progress,
        message=job.message or "",
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
