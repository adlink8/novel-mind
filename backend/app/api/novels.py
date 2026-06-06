"""小说管理 API"""

import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException

router = APIRouter()


@router.get("/")
async def list_novels():
    """获取小说列表"""
    # TODO: 查询数据库
    return []


@router.post("/upload")
async def upload_novel(file: UploadFile = File(...)):
    """上传小说 TXT 文件"""
    if not file.filename or not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="仅支持 .txt 格式文件")

    content = await file.read()
    # TODO: 文本清洗 → 章节分割 → 存入数据库 → 生成 Embedding
    novel_id = str(uuid.uuid4())

    return {
        "id": novel_id,
        "title": file.filename.replace(".txt", ""),
        "status": "importing",
        "message": "文件已上传，正在处理中...",
    }


@router.get("/{novel_id}")
async def get_novel(novel_id: str):
    """获取小说详情"""
    # TODO: 查询数据库
    raise HTTPException(status_code=404, detail="小说不存在")


@router.delete("/{novel_id}")
async def delete_novel(novel_id: str):
    """删除小说"""
    # TODO: 删除数据库记录和文件
    return {"message": "已删除"}


@router.get("/{novel_id}/chapters")
async def list_chapters(novel_id: str):
    """获取小说章节列表"""
    # TODO: 查询数据库
    return []


@router.get("/{novel_id}/chapters/{chapter_id}")
async def get_chapter(novel_id: str, chapter_id: str):
    """获取章节内容"""
    # TODO: 查询数据库
    raise HTTPException(status_code=404, detail="章节不存在")
