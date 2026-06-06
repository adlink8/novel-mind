"""小说管理 API — 接入 novel_service + 数据库"""

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.novel import (
    NovelResponse,
    NovelListResponse,
    NovelUploadResponse,
    ChapterResponse,
)
from app.services.novel_service import novel_service

router = APIRouter()


@router.get("")
async def list_novels(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="搜索标题或作者"),
    db: AsyncSession = Depends(get_db),
):
    """获取小说列表（分页 + 搜索）"""
    novels, total = await novel_service.get_novels(db, skip=skip, limit=limit, search=search)
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
):
    """上传并解析小说 TXT 文件"""
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    # 1. 保存文件 + 编码检测
    save_path, content = await novel_service.upload_novel(file)

    # 2. 文本清洗 + 章节分割
    chapters = novel_service.parse_novel(content)

    # 3. 存入数据库
    title = file.filename.rsplit(".", 1)[0]
    novel = await novel_service.create_novel_record(
        db, title=title, chapters=chapters, source_path=save_path
    )

    return NovelUploadResponse(
        id=novel.id,
        title=novel.title,
        status=novel.status,
        message=f"导入完成：{len(chapters)} 章，{sum(c['word_count'] for c in chapters)} 字",
        chapter_count=len(chapters),
        word_count=sum(c["word_count"] for c in chapters),
    )


@router.get("/{novel_id}", response_model=NovelResponse)
async def get_novel(novel_id: int, db: AsyncSession = Depends(get_db)):
    """获取小说详情（含章节列表）"""
    novel = await novel_service.get_novel(db, novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return NovelResponse.model_validate(novel)


@router.delete("/{novel_id}")
async def delete_novel(novel_id: int, db: AsyncSession = Depends(get_db)):
    """删除小说"""
    success = await novel_service.delete_novel(db, novel_id)
    if not success:
        raise HTTPException(status_code=404, detail="小说不存在")
    return {"message": "已删除"}


@router.get("/{novel_id}/chapters", response_model=list[ChapterResponse])
async def list_chapters(novel_id: int, db: AsyncSession = Depends(get_db)):
    """获取小说章节列表"""
    novel = await novel_service.get_novel(db, novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return [ChapterResponse.model_validate(ch) for ch in novel.chapters]


@router.get("/{novel_id}/chapters/{chapter_id}", response_model=ChapterResponse)
async def get_chapter(novel_id: int, chapter_id: int, db: AsyncSession = Depends(get_db)):
    """获取章节内容"""
    chapter = await novel_service.get_chapter(db, chapter_id)
    if not chapter or chapter.novel_id != novel_id:
        raise HTTPException(status_code=404, detail="章节不存在")
    return ChapterResponse.model_validate(chapter)
