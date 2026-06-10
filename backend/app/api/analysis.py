"""
剧情分析 API 路由（Phase 3 实现）

当前状态: 占位实现，生成类端点返回 HTTP 501。
查询类端点返回空状态。

端点列表:
  POST /api/analysis/{novel_id}/analyze            - 整本小说 AI 分析 -> 501
  GET  /api/analysis/{novel_id}                    - 获取已有分析结果 -> 空状态
  POST /api/analysis/{novel_id}/chapters/{id}/analyze - 章节级分析 -> 501
  POST /api/analysis/{novel_id}/analyze/stream     - 流式分析（SSE）-> 501
"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/{novel_id}/analyze")
async def analyze_novel(novel_id: str):
    """对整本小说进行 AI 分析（摘要、人物、伏笔、叙事结构）"""
    raise HTTPException(status_code=501, detail="AI 剧情分析尚未实现")


@router.get("/{novel_id}")
async def get_analysis(novel_id: str):
    """获取已有分析结果"""
    # TODO: 从 analysis_results 表查询
    return {"novel_id": novel_id, "status": "not_analyzed"}


@router.post("/{novel_id}/chapters/{chapter_id}/analyze")
async def analyze_chapter(novel_id: str, chapter_id: str):
    """分析单个章节（摘要、情感、人物出场）"""
    raise HTTPException(status_code=501, detail="章节级分析尚未实现")


@router.post("/{novel_id}/analyze/stream")
async def analyze_novel_stream(novel_id: str):
    """流式输出分析过程（SSE，实时展示 AI 分析进度）"""
    raise HTTPException(status_code=501, detail="流式剧情分析尚未实现")
