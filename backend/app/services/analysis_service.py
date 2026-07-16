"""Phase 07–aware novel analysis.

Uses hierarchical scene/evidence structure (when present) as the primary
analysis unit; falls back to raw chapters. LLM enrichment is optional —
structural analysis always succeeds without external models.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.analysis import AnalysisResult
from app.models.chunk_build import ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.services.ai_service import ai_service
from app.services.chunking.pg_store import (
    create_and_persist_hierarchy_build,
    get_active_build_id,
)

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = frozenset(
    {
        "plot_summary",
        "character_analysis",
        "theme",
        "style",
        "chapter_summary",
        "hierarchy_map",  # Phase 07 pure structural map
    }
)


class AnalysisError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def ensure_hierarchy(
    db: AsyncSession, novel: Novel, *, force: bool = False
) -> str | None:
    """Ensure an active hierarchy build exists; return build_id or None."""
    if not force:
        active = await get_active_build_id(db, novel.id)
        if active:
            return active

    chapters_result = await db.execute(
        select(Chapter)
        .options(undefer(Chapter.content))
        .where(Chapter.novel_id == novel.id)
        .order_by(Chapter.chapter_number)
    )
    chapters = list(chapters_result.scalars().all())
    if not chapters:
        return None

    payload = [
        {
            "chapter_id": ch.id,
            "id": ch.id,
            "chapter_number": ch.chapter_number,
            "content": ch.content or "",
        }
        for ch in chapters
    ]
    rec = await create_and_persist_hierarchy_build(
        db,
        novel_id=novel.id,
        chapters=payload,
        promote_active=True,
        force_full=True,
    )
    await db.commit()
    return rec.build_id


async def load_scene_units(
    db: AsyncSession, *, novel_id: int, build_id: str, chapter_id: int | None = None
) -> list[dict[str, Any]]:
    """Load scene nodes with child evidence counts for analysis context."""
    q = select(ChunkHierarchyNode).where(
        ChunkHierarchyNode.novel_id == novel_id,
        ChunkHierarchyNode.build_id == build_id,
        ChunkHierarchyNode.level == "scene",
    )
    if chapter_id is not None:
        q = q.where(ChunkHierarchyNode.chapter_id == chapter_id)
    q = q.order_by(
        ChunkHierarchyNode.chapter_number,
        ChunkHierarchyNode.order_index,
    )
    scenes = list((await db.execute(q)).scalars().all())
    units = []
    for s in scenes:
        preview = (s.content or "")[:400]
        units.append(
            {
                "scene_id": s.node_id,
                "chapter_id": s.chapter_id,
                "chapter_number": s.chapter_number,
                "order_index": s.order_index,
                "char_count": len(s.content or ""),
                "evidence_count": len(s.child_ids or []),
                "preview": preview,
            }
        )
    return units


def _extract_name_candidates(text: str, limit: int = 30) -> list[str]:
    """Very light Chinese name-ish token heuristic for offline character map."""
    # 2–3 char sequences that appear multiple times (rough character names)
    counts: dict[str, int] = {}
    for m in re.finditer(r"[\u4e00-\u9fff]{2,3}", text):
        tok = m.group(0)
        # skip common function words / connectors
        if tok in {
            "一个",
            "没有",
            "已经",
            "因为",
            "所以",
            "但是",
            "什么",
            "自己",
            "他们",
            "我们",
            "这个",
            "那个",
            "可以",
            "不是",
            "就是",
            "还是",
            "如果",
            "然后",
            "开始",
            "知道",
            "觉得",
            "时候",
            "地方",
            "东西",
        }:
            continue
        counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [n for n, c in ranked if c >= 3][:limit]


def build_structural_result(
    *,
    novel: Novel,
    analysis_type: str,
    scenes: list[dict[str, Any]],
    chapter_id: int | None,
    build_id: str | None,
) -> dict[str, Any]:
    """Deterministic analysis from Phase 07 hierarchy (no LLM)."""
    total_chars = sum(s["char_count"] for s in scenes)
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for s in scenes:
        by_chapter.setdefault(s["chapter_number"], []).append(s)

    full_preview = "\n".join(s["preview"] for s in scenes[:40])
    names = _extract_name_candidates(full_preview)

    chapter_outline = [
        {
            "chapter_number": cn,
            "scene_count": len(items),
            "char_count": sum(i["char_count"] for i in items),
            "first_scene_preview": (items[0]["preview"][:120] if items else ""),
        }
        for cn, items in sorted(by_chapter.items())
    ]

    base = {
        "source": "phase07_hierarchy",
        "build_id": build_id,
        "chapter_id": chapter_id,
        "scene_count": len(scenes),
        "chapter_count": len(by_chapter),
        "total_chars_in_scenes": total_chars,
        "llm_enriched": False,
        "llm_status": "skipped",
    }

    if analysis_type == "hierarchy_map":
        return {
            **base,
            "title": f"《{novel.title}》层级结构图",
            "chapters": chapter_outline,
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "chapter_number": s["chapter_number"],
                    "order_index": s["order_index"],
                    "evidence_count": s["evidence_count"],
                    "char_count": s["char_count"],
                    "preview": s["preview"][:160],
                }
                for s in scenes[:200]
            ],
        }

    if analysis_type == "character_analysis":
        return {
            **base,
            "title": f"《{novel.title}》人物线索（结构推断）",
            "candidates": [
                {"name": n, "note": "基于场景预览高频词粗提取，供后续 LLM 精炼"}
                for n in names
            ],
            "method": "frequency_heuristic_on_scene_previews",
        }

    if analysis_type == "chapter_summary":
        return {
            **base,
            "title": f"《{novel.title}》分章结构摘要",
            "chapters": [
                {
                    **c,
                    "structural_summary": (
                        f"第{c['chapter_number']}章含 {c['scene_count']} 个场景，"
                        f"约 {c['char_count']} 字。开场：{c['first_scene_preview']}"
                    ),
                }
                for c in chapter_outline
            ],
        }

    if analysis_type == "theme":
        return {
            **base,
            "title": f"《{novel.title}》主题线索（结构）",
            "notes": [
                "主题精炼需要 LLM；此处提供场景密度与人物线索作为输入。",
                f"共 {len(scenes)} 场景 / {len(by_chapter)} 章。",
            ],
            "name_signals": names[:15],
            "density": {
                "avg_scene_chars": (total_chars // max(len(scenes), 1)),
                "scenes_per_chapter": round(len(scenes) / max(len(by_chapter), 1), 2),
            },
        }

    if analysis_type == "style":
        avg = total_chars // max(len(scenes), 1)
        return {
            **base,
            "title": f"《{novel.title}》叙事节奏（结构）",
            "metrics": {
                "avg_scene_length": avg,
                "scene_count": len(scenes),
                "pace_hint": (
                    "短场景快切" if avg < 400 else "中等场景" if avg < 900 else "长场景铺陈"
                ),
            },
        }

    # plot_summary default
    beats = []
    if scenes:
        n = len(scenes)
        picks = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1})
        labels = ["开端", "发展", "中段", "转折", "收束"]
        for i, idx in enumerate(picks):
            s = scenes[idx]
            beats.append(
                {
                    "beat": labels[min(i, len(labels) - 1)],
                    "chapter_number": s["chapter_number"],
                    "scene_id": s["scene_id"],
                    "preview": s["preview"][:200],
                }
            )
    return {
        **base,
        "title": f"《{novel.title}》剧情结构摘要",
        "beats": beats,
        "chapter_outline": chapter_outline[:50],
        "summary_text": (
            f"全书共 {len(by_chapter)} 章、{len(scenes)} 个场景（Phase 07 层级）。"
            f"以下为关键场景节拍预览，可用于后续 LLM 精炼。"
        ),
    }


async def resolve_chat_model(
    db: AsyncSession,
    *,
    model: str | None = None,
    owner_id: int | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    解析分析用聊天模型。

    返回 (litellm_model, api_key, api_base)。
    优先调用方指定 model；否则取用户默认 AIModelConfig；再回落 settings/env。
    """
    if model:
        return model, None, None

    from app.models.ai_model import AIModelConfig

    q = select(AIModelConfig).where(
        AIModelConfig.is_active.is_(True),
        AIModelConfig.is_default.is_(True),
    )
    if owner_id is not None:
        q = q.where(AIModelConfig.owner_id == owner_id)
    result = await db.execute(q.limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None and owner_id is not None:
        # 无用户默认时尝试任意默认
        result = await db.execute(
            select(AIModelConfig)
            .where(
                AIModelConfig.is_active.is_(True),
                AIModelConfig.is_default.is_(True),
            )
            .limit(1)
        )
        cfg = result.scalar_one_or_none()
    if cfg is not None:
        name = ai_service.litellm_model_name(cfg.provider, cfg.model_id)
        return name, cfg.api_key, cfg.base_url
    return ai_service.default_model, None, None


async def try_llm_enrich(
    structural: dict[str, Any],
    *,
    analysis_type: str,
    novel_title: str,
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> dict[str, Any]:
    """Optional LLM polish; never raises — returns structural on failure."""
    system = (
        "你是网文/小说分析助手。根据给定的 Phase07 场景结构 JSON，"
        "输出简洁中文分析。只输出 JSON 对象，键包括："
        "summary (string), key_points (string[]), characters (string[]), risks (string[])。"
        "不要编造未在输入中出现的硬事实。"
    )
    user = json.dumps(
        {
            "novel": novel_title,
            "analysis_type": analysis_type,
            "structure": structural,
        },
        ensure_ascii=False,
    )[:12000]

    try:
        resp = await ai_service.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=0.3,
            max_tokens=1200,
            api_key=api_key,
            api_base=api_base,
        )
        text = resp.choices[0].message.content or ""
        # strip fences
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("llm output not object")
        enriched = dict(structural)
        enriched["llm_enriched"] = True
        enriched["llm_status"] = "ok"
        enriched["llm"] = data
        usage = getattr(resp, "usage", None)
        if usage:
            enriched["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
            enriched["completion_tokens"] = getattr(usage, "completion_tokens", None)
        return enriched
    except Exception as e:
        logger.warning("LLM enrich skipped: %s", e)
        out = dict(structural)
        out["llm_enriched"] = False
        out["llm_status"] = f"unavailable:{type(e).__name__}"
        out["llm_error"] = str(e)[:200]
        return out


class AnalysisService:
    async def analyze(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        analysis_type: str = "plot_summary",
        chapter_id: int | None = None,
        model: str | None = None,
        use_llm: bool = True,
        rebuild_hierarchy: bool = False,
    ) -> AnalysisResult:
        if analysis_type not in SUPPORTED_TYPES:
            raise AnalysisError(
                f"不支持的分析类型: {analysis_type}，可选: {sorted(SUPPORTED_TYPES)}"
            )

        novel = await db.get(Novel, novel_id)
        if not novel:
            raise AnalysisError("小说不存在", status_code=404)

        novel.status = "analyzing"
        await db.commit()

        try:
            build_id = await ensure_hierarchy(
                db, novel, force=rebuild_hierarchy
            )
            scenes = []
            if build_id:
                scenes = await load_scene_units(
                    db,
                    novel_id=novel_id,
                    build_id=build_id,
                    chapter_id=chapter_id,
                )

            if not scenes:
                # Fallback: chapter-level pseudo-scenes
                chapters_result = await db.execute(
                    select(Chapter)
                    .options(undefer(Chapter.content))
                    .where(Chapter.novel_id == novel_id)
                    .order_by(Chapter.chapter_number)
                )
                chapters = list(chapters_result.scalars().all())
                if chapter_id is not None:
                    chapters = [c for c in chapters if c.id == chapter_id]
                scenes = [
                    {
                        "scene_id": f"raw_ch_{c.id}",
                        "chapter_id": c.id,
                        "chapter_number": c.chapter_number,
                        "order_index": 0,
                        "char_count": len(c.content or ""),
                        "evidence_count": 0,
                        "preview": (c.content or "")[:400],
                    }
                    for c in chapters
                ]

            structural = build_structural_result(
                novel=novel,
                analysis_type=analysis_type,
                scenes=scenes,
                chapter_id=chapter_id,
                build_id=build_id,
            )

            result_data = structural
            model_used = "phase07-structural"
            prompt_tokens = None
            completion_tokens = None

            if use_llm and analysis_type != "hierarchy_map":
                resolved_model, resolved_key, resolved_base = await resolve_chat_model(
                    db,
                    model=model,
                    owner_id=getattr(novel, "owner_id", None),
                )
                result_data = await try_llm_enrich(
                    structural,
                    analysis_type=analysis_type,
                    novel_title=novel.title or str(novel_id),
                    model=resolved_model,
                    api_key=resolved_key,
                    api_base=resolved_base,
                )
                if result_data.get("llm_enriched"):
                    model_used = resolved_model or ai_service.default_model
                    prompt_tokens = result_data.get("prompt_tokens")
                    completion_tokens = result_data.get("completion_tokens")

            row = AnalysisResult(
                novel_id=novel_id,
                chapter_id=chapter_id,
                analysis_type=analysis_type,
                result_data=result_data,
                model_used=model_used,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            db.add(row)
            novel.status = "analyzed"
            await db.commit()
            await db.refresh(row)
            return row
        except AnalysisError:
            novel.status = "ready"
            await db.commit()
            raise
        except Exception:
            novel.status = "ready"
            await db.commit()
            raise

    async def get_latest(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        analysis_type: str | None = None,
        chapter_id: int | None = None,
    ) -> list[AnalysisResult]:
        q = select(AnalysisResult).where(AnalysisResult.novel_id == novel_id)
        if analysis_type:
            q = q.where(AnalysisResult.analysis_type == analysis_type)
        if chapter_id is not None:
            q = q.where(AnalysisResult.chapter_id == chapter_id)
        q = q.order_by(AnalysisResult.created_at.desc()).limit(20)
        return list((await db.execute(q)).scalars().all())

    async def hierarchy_status(
        self, db: AsyncSession, *, novel_id: int
    ) -> dict[str, Any]:
        build_id = await get_active_build_id(db, novel_id)
        if not build_id:
            return {
                "novel_id": novel_id,
                "active_build_id": None,
                "ready": False,
                "scene_count": 0,
                "chapter_count": 0,
                "evidence_count": 0,
            }
        nodes = (
            await db.execute(
                select(ChunkHierarchyNode).where(
                    ChunkHierarchyNode.novel_id == novel_id,
                    ChunkHierarchyNode.build_id == build_id,
                )
            )
        ).scalars().all()
        scenes = [n for n in nodes if n.level == "scene"]
        chapters = {n.chapter_id for n in nodes if n.level == "chapter"}
        evidence = [n for n in nodes if n.level == "evidence"]
        return {
            "novel_id": novel_id,
            "active_build_id": build_id,
            "ready": True,
            "scene_count": len(scenes),
            "chapter_count": len(chapters),
            "evidence_count": len(evidence),
            "sample_scenes": [
                {
                    "scene_id": s.node_id,
                    "chapter_number": s.chapter_number,
                    "preview": (s.content or "")[:120],
                    "char_count": len(s.content or ""),
                }
                for s in sorted(
                    scenes, key=lambda x: (x.chapter_number, x.order_index)
                )[:12]
            ],
        }


analysis_service = AnalysisService()
