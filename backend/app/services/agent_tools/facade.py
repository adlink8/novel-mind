"""
智能体工具门面（25.2-02 Domain Tool Contract / D-06 / D-07）。

本门面把 7 个只读工具暴露给 agent-service：
  get_novel / get_chapter / search_novel_text / get_timeline /
  get_relationships / get_clues / get_narrative_memory

设计原则（对齐 D-07「服务端强制，绝不放提示侧」）：
  1. **不重实现** owner / cutoff / budget 逻辑 —— 只复用现有服务入口
     （novel_service、resolve_chapter_cutoff、build_version_view、
     relationship_graph_query_service、build_clue_envelope、structure_query）。
  2. **门面新增的强制点**：
     - 冻结错误码映射（errors.py，唯一事实源）；
     - per-tool 64 KiB 字节上限（输出超限 → ``output_too_large``）；
     - per-tool ``asyncio.wait_for`` 30s 超时（→ ``timeout``）；
     - budget hook（fail closed：超预算在调用服务**之前**拦截 → ``budget_exceeded``）；
     - ``get_narrative_memory`` 响应带 ``release_status: "candidate"``（ADR-0002）。
  3. **``full_book`` 只从持久化的每本小说开关读取**
     （``novel.reading_progress["timeline_full_book"]``），绝不接受裸请求参数。
  4. 门面**只读**（D-22）：不 import 任何领域写入/变异模块，也不构造 LLM 调用
     （由 adversarial 静态 gate 强制）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.models import Novel
from app.schemas.novel import ChapterResponse, NovelResponse
from app.schemas.relationship import RelationshipVersionSource
from app.schemas.timeline import TimelineEnvelope, TimelineOrdering, TimelineVersionSource
from app.services.agent_tools.errors import (
    AgentToolError,
    BeyondCutoffError,
    BudgetExceededError,
    InvalidInputError,
    NotFoundError,
    OutputTooLargeError,
    ToolTimeoutError,
    UpstreamError,
)
from app.services.clues.query import build_clue_envelope
from app.services.narrative_memory.structure_query import (
    StructureQueryError,
    list_versions,
    load_structure_tree,
)
from app.services.novel_service import novel_service
from app.services.relationships.query import relationship_graph_query_service
from app.services.timeline.query import build_version_view, resolve_chapter_cutoff

logger = logging.getLogger(__name__)

# 冻结的 7 个只读工具名（25.2-03 skill.yaml 的 allowed_tools 白名单镜像此表）。
TOOL_NAMES: tuple[str, ...] = (
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
)

# per-tool 默认字节上限（agent-service 侧同样硬编码 64 KiB，见 RESEARCH Code Examples）。
DEFAULT_BYTE_CAP = 64 * 1024
# per-tool 默认超时（秒）。
DEFAULT_TOOL_TIMEOUT = 30.0

# 预算钩子类型：在服务执行前被调用；超预算应抛出 BudgetExceededError。
BudgetHook = Callable[[str, dict[str, Any]], Awaitable[None]]


async def default_budget_hook(tool_name: str, params: dict[str, Any]) -> None:
    """默认预算钩子：无策略配置时放行。

    25.2-03 skill runtime 会注入 per-run 的调用/Token 上限钩子
    （skill.yaml ``budget`` 字段，BudgetPolicy 语义），本门面只保证
    fail-closed 的拦截位置在服务调用**之前**。
    """


def _persisted_full_book(novel: Novel) -> bool:
    """从持久化的每本小说开关读取 full_book 授权（绝不接受裸请求参数）。"""
    return bool((novel.reading_progress or {}).get("timeline_full_book", False))


# ────────────────────────── 默认服务入口（按工具） ──────────────────────────


async def _default_get_novel(db, novel_id: int):
    return await novel_service.get_novel(db, novel_id)


async def _default_get_chapter(db, chapter_id: int):
    return await novel_service.get_chapter(db, chapter_id)


async def _default_search_novel_text(
    db,
    *,
    owner_id: int,
    novel_id: int,
    query: str,
    mode: str,
    top_k: int,
) -> Any:
    from app.services.knowledge_units.search import production_retrieval_strategy

    strategy = production_retrieval_strategy()
    outcome = await strategy.resolve_novel(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        domain_profile="fiction",
        query=query,
        mode=mode,
        top_k=top_k,
    )
    return {
        "results": outcome.rows,
        "resolved_mode": outcome.resolved_mode,
        "fallback_reason": outcome.fallback_reason,
    }


async def _default_get_timeline(
    db,
    *,
    novel: Novel,
    owner_id: int,
    source: TimelineVersionSource,
    ordering: TimelineOrdering,
    person: str | None,
    include_causal: bool,
    request_full_book: bool,
    chapter_start: int | None,
    chapter_end: int | None,
):
    return await build_version_view(
        db,
        novel=novel,
        owner_id=owner_id,
        source=source,
        ordering=ordering,
        person=person,
        include_causal=include_causal,
        request_full_book=request_full_book,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )


async def _default_get_relationships(
    db,
    *,
    novel: Novel,
    owner_id: int,
    source: RelationshipVersionSource,
    version_id: int | None,
    through_chapter: int | None,
    request_full_book: bool,
    character_id: int | None,
    relation_type: str | None,
    include_provisional: bool,
):
    return await relationship_graph_query_service.build_graph(
        db,
        novel=novel,
        owner_id=owner_id,
        source=source,
        version_id=version_id,
        through_chapter=through_chapter,
        request_full_book=request_full_book,
        character_id=character_id,
        relation_type=relation_type,
        include_provisional=include_provisional,
    )


async def _default_get_clues(
    db,
    *,
    novel: Novel,
    owner_id: int,
    request_full_book: bool,
    character_id: int | None,
    status_filter: str | None,
) -> dict[str, Any]:
    return await build_clue_envelope(
        db,
        novel=novel,
        owner_id=owner_id,
        request_full_book=request_full_book,
        character_id=character_id,
        status_filter=status_filter,
    )


async def _default_get_narrative_memory(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    view: str,
    through_chapter: int | None,
) -> Any:
    if view == "versions":
        return await list_versions(db, owner_id=owner_id, novel_id=novel_id)
    if view == "tree":
        if version_id is None:
            raise InvalidInputError("narrative_memory tree 视图需要 version_id")
        return await load_structure_tree(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            through_chapter=through_chapter,
        )
    raise InvalidInputError(f"不支持的 narrative_memory 视图: {view!r}")


# ────────────────────────── 门面本体 ──────────────────────────


class ToolFacade:
    """7 个只读工具的统一执行门面。

    所有强制点（字节上限 / 超时 / budget hook / 错误码映射）都在
    ``execute`` 内完成；owner / cutoff 逻辑复用现有服务。
    """

    def __init__(
        self,
        *,
        byte_cap: int = DEFAULT_BYTE_CAP,
        timeout: float = DEFAULT_TOOL_TIMEOUT,
        budget_hook: BudgetHook | None = None,
        cutoff_resolver: Callable | None = None,
        service_overrides: dict[str, Callable] | None = None,
    ) -> None:
        self.byte_cap = byte_cap
        self.timeout = timeout
        self.budget_hook = budget_hook or default_budget_hook
        # cutoff 解析器可注入（测试用 stub）；默认复用现有 resolve_chapter_cutoff。
        self.cutoff_resolver = cutoff_resolver or resolve_chapter_cutoff
        # 服务入口可注入（adversarial/contract 测试用 stub；默认走真实服务）。
        self._overrides = dict(service_overrides or {})
        self._handlers = {
            "get_novel": self._get_novel,
            "get_chapter": self._get_chapter,
            "search_novel_text": self._search_novel_text,
            "get_timeline": self._get_timeline,
            "get_relationships": self._get_relationships,
            "get_clues": self._get_clues,
            "get_narrative_memory": self._get_narrative_memory,
        }

    # ── 公共入口 ──

    async def execute(
        self,
        tool_name: str,
        *,
        db,
        novel: Novel,
        owner_id: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """执行一个工具；返回 JSON 安全的 payload（已通过字节上限检查）。

        抛出的异常均为 AgentToolError 子类（冻结错误码）。
        """
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise InvalidInputError(f"未知工具: {tool_name!r}")

        # budget hook：fail closed，在服务调用之前拦截。
        await self.budget_hook(tool_name, params)

        try:
            raw = await asyncio.wait_for(
                handler(db=db, novel=novel, owner_id=owner_id, params=params),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(
                f"工具 {tool_name} 执行超过 {self.timeout:.1f}s 超时"
            ) from exc
        except AgentToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为上游错误
            logger.exception("工具 %s 执行失败: %s", tool_name, exc)
            raise UpstreamError(f"工具 {tool_name} 上游执行失败") from exc

        payload = self._to_json_safe(raw)
        size = len(
            json.dumps(payload, ensure_ascii=False, default=_json_default).encode(
                "utf-8"
            )
        )
        if size > self.byte_cap:
            raise OutputTooLargeError(
                f"工具 {tool_name} 响应 {size} 字节超过 {self.byte_cap} 字节上限"
            )
        return payload

    @staticmethod
    def _to_json_safe(raw: Any) -> Any:
        """把服务返回值归一化为 JSON 安全结构（pydantic → dict，ORM → schema）。"""
        if raw is None:
            return None
        if hasattr(raw, "model_dump"):
            return raw.model_dump(mode="json")
        if isinstance(raw, dict):
            return {
                key: ToolFacade._to_json_safe(value) for key, value in raw.items()
            }
        if isinstance(raw, (list, tuple)):
            return [ToolFacade._to_json_safe(item) for item in raw]
        return raw

    def _svc(self, key: str, default: Callable) -> Callable:
        return self._overrides.get(key, default)

    # ── 各工具处理函数 ──

    async def _get_novel(self, *, db, novel: Novel, owner_id: int, params: dict):
        svc = self._svc("get_novel", _default_get_novel)
        row = await svc(db, novel.id)
        if row is None:
            raise NotFoundError("小说不存在")
        return NovelResponse.model_validate(row)

    async def _get_chapter(self, *, db, novel: Novel, owner_id: int, params: dict):
        chapter_id = int(params["chapter_id"])
        svc = self._svc("get_chapter", _default_get_chapter)
        chapter = await svc(db, chapter_id)
        if chapter is None or chapter.novel_id != novel.id:
            raise NotFoundError("章节不存在")
        cutoff = await self.cutoff_resolver(db, novel)
        if cutoff is not None and int(chapter.chapter_number) > int(cutoff):
            raise BeyondCutoffError(
                f"章节 {chapter.chapter_number} 超出当前阅读进度截止点 {cutoff}"
            )
        return ChapterResponse.model_validate(chapter)

    async def _search_novel_text(self, *, db, novel: Novel, owner_id: int, params: dict):
        svc = self._svc("search_novel_text", _default_search_novel_text)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            query=params["query"],
            mode=params.get("mode", "auto"),
            top_k=int(params.get("top_k", 10)),
        )

    async def _get_timeline(self, *, db, novel: Novel, owner_id: int, params: dict):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = (
            None
            if persisted_full_book
            else await self.cutoff_resolver(db, novel)
        )
        chapter_start = params.get("chapter_start")
        chapter_end = params.get("chapter_end")
        if not persisted_full_book and cutoff is not None:
            if chapter_end is not None and int(chapter_end) > int(cutoff):
                raise BeyondCutoffError(
                    f"章节范围结束点 {chapter_end} 超出服务端截止点 {cutoff}"
                )

        ordering = (
            TimelineOrdering.STORY
            if params.get("ordering") == "story"
            else TimelineOrdering.NARRATIVE
        )
        svc = self._svc("get_timeline", _default_get_timeline)
        common = dict(
            db=db,
            novel=novel,
            owner_id=owner_id,
            ordering=ordering,
            person=params.get("person"),
            include_causal=bool(params.get("causal", False)),
            request_full_book=persisted_full_book,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
        )
        active = await svc(
            source=TimelineVersionSource.ACTIVE, **common
        )
        running = await svc(
            source=TimelineVersionSource.RUNNING_CANDIDATE, **common
        )
        return TimelineEnvelope(active=active, running_candidate=running)

    async def _get_relationships(self, *, db, novel: Novel, owner_id: int, params: dict):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = (
            None
            if persisted_full_book
            else await self.cutoff_resolver(db, novel)
        )
        through_chapter = params.get("through_chapter")
        if not persisted_full_book and cutoff is not None:
            if through_chapter is not None and int(through_chapter) > int(cutoff):
                raise BeyondCutoffError(
                    f"through_chapter {through_chapter} 超出服务端截止点 {cutoff}"
                )

        source_name = params.get("source", "active")
        source = (
            RelationshipVersionSource.RUNNING_CANDIDATE
            if source_name == "running_candidate"
            else RelationshipVersionSource.ACTIVE
        )
        svc = self._svc("get_relationships", _default_get_relationships)
        return await svc(
            db,
            novel=novel,
            owner_id=owner_id,
            source=source,
            version_id=params.get("version_id"),
            through_chapter=through_chapter,
            request_full_book=persisted_full_book,
            character_id=params.get("character_id"),
            relation_type=params.get("relation_type"),
            include_provisional=bool(params.get("include_provisional", False)),
        )

    async def _get_clues(self, *, db, novel: Novel, owner_id: int, params: dict):
        persisted_full_book = _persisted_full_book(novel)
        svc = self._svc("get_clues", _default_get_clues)
        return await svc(
            db,
            novel=novel,
            owner_id=owner_id,
            request_full_book=persisted_full_book,
            character_id=params.get("character_id"),
            status_filter=params.get("status"),
        )

    async def _get_narrative_memory(self, *, db, novel: Novel, owner_id: int, params: dict):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = (
            None
            if persisted_full_book
            else await self.cutoff_resolver(db, novel)
        )
        through_chapter = params.get("through_chapter")
        if not persisted_full_book and cutoff is not None:
            if through_chapter is not None and int(through_chapter) > int(cutoff):
                raise BeyondCutoffError(
                    f"through_chapter {through_chapter} 超出服务端截止点 {cutoff}"
                )

        svc = self._svc("get_narrative_memory", _default_get_narrative_memory)
        view = params.get("view", "versions")
        try:
            data = await svc(
                db,
                owner_id=owner_id,
                novel_id=novel.id,
                version_id=params.get("version_id"),
                view=view,
                through_chapter=through_chapter,
            )
        except StructureQueryError as exc:
            # 映射叙事记忆领域错误：404 → not_found；其余 → invalid_input。
            if exc.status_code == 404:
                raise NotFoundError(str(exc.detail)) from exc
            raise InvalidInputError(str(exc.detail)) from exc

        # ADR-0002：叙事记忆仅候选发布，响应必须显式标注 release_status。
        return {
            "release_status": "candidate",
            "publication_status": "candidate_preview",
            "view": view,
            "data": data,
        }


def _json_default(obj: Any) -> str:
    """兜底序列化：datetime / Decimal 等非 JSON 原生类型转字符串。"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


# 全局单例：API 路由与测试共用；测试可用独立实例注入 stub。
tool_facade = ToolFacade()
