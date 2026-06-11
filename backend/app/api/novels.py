"""小说管理 API — 接入 novel_service + 数据库"""

import secrets

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
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
)
from app.services.novel_service import novel_service

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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """
    上传并解析小说 TXT 文件（支持多编码自动检测和大文件）。

    需要登录认证。

    处理流程:
    1. 接收文件并检测/转换编码（支持 UTF-8、GBK、GB18030、Big5 等）
    2. 文本清洗与章节自动分割
    3. 保存到数据库并返回结果
    """
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    # 用临时 ID 跟踪进度（实际 novel_id 在数据库创建后才获得）
    # 这里先记录上传阶段
    temp_id = -secrets.randbelow(2_000_000_000) - 1
    novel_service.set_import_status(temp_id, "uploading", 10, "正在接收文件...")

    save_path = None
    try:
        # 1. 保存文件 + 编码检测（自动回退多编码）
        novel_service.set_import_status(temp_id, "detecting", 20, "正在检测文件编码...")
        save_path, content = await novel_service.upload_novel(file)

        # 2. 文本清洗 + 章节分割
        novel_service.set_import_status(temp_id, "parsing", 40, "正在解析章节...")
        chapters = novel_service.parse_novel(content)

        # 3. 存入数据库（create_novel_record 内部会更新进度到 saving / ready）
        title = file.filename.rsplit(".", 1)[0]
        novel = await novel_service.create_novel_record(
            db,
            title=title,
            chapters=chapters,
            source_path=save_path,
            owner_id=current_user.id,
        )
        await db.commit()

        # create_novel_record 内部已完成进度跟踪（saving → ready）
        # 清理临时进度记录
        novel_service.clear_import_status(temp_id)

        return NovelUploadResponse(
            id=novel.id,
            title=novel.title,
            status=novel.status,
            message=f"导入完成：{len(chapters)} 章，{sum(c['word_count'] for c in chapters)} 字",
            chapter_count=len(chapters),
            word_count=sum(c["word_count"] for c in chapters),
        )
    except Exception as exc:
        await db.rollback()
        if save_path:
            novel_service.remove_uploaded_file(save_path)
        novel_service.set_import_status(temp_id, "error", 0, f"导入失败: {str(exc)}")
        raise


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
async def get_import_status(novel: Novel = Depends(require_owned_novel)):
    """获取小说导入进度状态（前端轮询用）"""
    status = novel_service.get_import_status(novel.id)
    if not status:
        # 如果内存中没有状态记录，查询数据库确认
        # 这里简化处理：返回一个默认状态
        return ImportStatusResponse(
            novel_id=novel.id,
            stage="unknown",
            percent=0,
            message="暂无导入状态信息",
        )
    return ImportStatusResponse(
        novel_id=novel.id,
        stage=status["stage"],
        percent=status["percent"],
        message=status["message"],
    )
