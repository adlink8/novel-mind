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
from app.schemas.timeline import (
    TimelineEnvelope,
    TimelineOrdering,
    TimelineVersionSource,
)
from app.services.agent_tools.errors import (
    AgentToolError,
    BeyondCutoffError,
    InvalidInputError,
    NotFoundError,
    OutputTooLargeError,
    ToolTimeoutError,
    UpstreamError,
)
from app.services.visual_bible.authority import (
    CandidateNotFoundError,
    list_versions as list_visual_bible_versions,
    load_version_view as load_visual_bible_version_view,
)
from app.services.clues.query import build_clue_envelope
from app.services.narrative_memory.structure_query import (
    StructureQueryError,
    list_versions,
    load_structure_tree,
)
from app.services.novel_service import novel_service
from app.services.queryplan.adapters import chapter_content_hash
from app.services.queryplan.contracts import leaf_evidence_key
from app.services.relationships.query import relationship_graph_query_service
from app.services.timeline.query import build_version_view, resolve_chapter_cutoff
from app.services.world_model.entity_queries import WorldEntityQueries
from app.services.world_model.event_queries import WorldModelEventQueries
from app.services.world_model.knowledge import EpistemicAspect, KnowledgeResultStatus
from app.services.world_model.knowledge_queries import KnowledgeQueries

logger = logging.getLogger(__name__)

# 冻结的 13 个只读域工具名（25.2-03 skill.yaml 的 allowed_tools 白名单镜像此表；
# 27-05 起加入 Phase 27 世界模型工具 get_events / get_character_state /
# get_character_knowledge / get_world_rules / get_evidence_span；31-04 起加入
# Phase 30 Visual Bible 只读工具 get_visual_bible）。
TOOL_NAMES: tuple[str, ...] = (
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "get_events",
    "get_character_state",
    "get_character_knowledge",
    "get_world_rules",
    "get_evidence_span",
    "get_visual_bible",
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


# ────────────────────────── Phase 27 世界模型默认服务入口（27-05） ──────────────────────────


def _epistemic_answer_to_json(answer) -> dict[str, Any]:
    """把 EpistemicAnswer 序列化为 JSON 安全 payload（claims/evidence 是 pydantic）。"""
    return {
        "status": answer.status.value,
        "subject": answer.subject,
        "claims": [claim.model_dump(mode="json") for claim in answer.claims],
        "evidence": [ref.model_dump(mode="json") for ref in answer.evidence],
        "has_approval": answer.has_approval,
        "message": answer.message,
    }


def _merge_state_answers(
    *, subject: str, answers: list[Any], message: str
) -> dict[str, Any]:
    """合并 state/goal/motivation 三个 aspect 的查询结果（无编造，abstain 优先）。"""
    claims = tuple(claim for answer in answers for claim in answer.claims)
    evidence = tuple(ref for answer in answers for ref in answer.evidence)
    approved = any(answer.has_approval for answer in answers)
    if not claims:
        status = KnowledgeResultStatus.ABSTAINED
    elif approved:
        status = KnowledgeResultStatus.ANSWERED
    else:
        status = KnowledgeResultStatus.CANDIDATE_ONLY
    return {
        "status": status.value,
        "subject": subject,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "evidence": [ref.model_dump(mode="json") for ref in evidence],
        "has_approval": approved,
        "message": message,
    }


async def _default_get_events(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff: int,
) -> dict[str, Any] | None:
    """世界模型事件/因果投影（D-05 cutoff 过滤；无投影 → None → 404-hide）。"""
    projection = await WorldModelEventQueries(db).query_cutoff_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        cutoff=cutoff,
    )
    return projection.model_dump(mode="json") if projection is not None else None


async def _default_get_character_state(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    subject: str,
    cutoff: int,
    pov: str | None,
) -> dict[str, Any]:
    """角色状态/目标/动机（aspect ∈ state/goal/motivation 合并，D-05）。"""
    queries = KnowledgeQueries(db)
    answers = [
        await queries.query_character_knowledge(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            subject=subject,
            cutoff=cutoff,
            pov=pov,
            aspect=aspect,
        )
        for aspect in (EpistemicAspect.STATE, EpistemicAspect.GOAL, EpistemicAspect.MOTIVATION)
    ]
    return _merge_state_answers(
        subject=subject,
        answers=answers,
        message="character state merged across state/goal/motivation (D-05)",
    )


async def _default_get_character_knowledge(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    subject: str,
    cutoff: int,
    pov: str | None,
) -> dict[str, Any]:
    """角色知识（aspect=knowledge；mistaken/hidden 保持显式标签，D-05）。"""
    answer = await KnowledgeQueries(db).query_character_knowledge(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        subject=subject,
        cutoff=cutoff,
        pov=pov,
        aspect=EpistemicAspect.KNOWLEDGE,
    )
    return _epistemic_answer_to_json(answer)


async def _default_get_world_rules(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """世界规则与规则例外（D-05 cutoff 过滤；例外是 first-class，D-04）。"""
    queries = WorldEntityQueries(db)
    rules = [
        rule.model_dump(mode="json")
        for rule in await queries.query_rules(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if rule.disclosure_cutoff <= cutoff
    ]
    exceptions = [
        exc.model_dump(mode="json")
        for exc in await queries.query_rule_exceptions(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if exc.disclosure_cutoff <= cutoff
    ]
    return {"rules": rules, "exceptions": exceptions}


async def _default_get_evidence_span(
    db,
    *,
    chapter_id: int,
    source_start: int,
    source_end: int,
    content_hash: str,
) -> dict[str, Any] | None:
    """按 chapter+offsets+content_hash 物化 leaf 证据跨度（D-07/D-08）。

    chapter 缺失 → None（404-hide）；offsets 非法 / hash 与原文切片不匹配 →
    InvalidInputError（fail closed，绝不返回错误切片）。
    """
    chapter = await novel_service.get_chapter(db, chapter_id)
    if chapter is None:
        return None
    content = chapter.content
    if source_start < 0 or source_end > len(content) or source_end <= source_start:
        raise InvalidInputError(
            f"offsets [{source_start},{source_end}) 不是合法 half-open 区间"
        )
    excerpt = content[source_start:source_end]
    if chapter_content_hash(excerpt) != content_hash:
        raise InvalidInputError("evidence content hash 与原文切片不匹配")
    return {
        "evidence_key": leaf_evidence_key(
            chapter_id=chapter_id,
            source_start=source_start,
            source_end=source_end,
            content_hash=content_hash,
        ),
        "chapter_id": chapter_id,
        "chapter_number": chapter.chapter_number,
        "novel_id": chapter.novel_id,
        "source_start": source_start,
        "source_end": source_end,
        "content_hash": content_hash,
        "excerpt": excerpt,
    }


async def _default_get_visual_bible(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    approved_only: bool,
) -> dict[str, Any] | None:
    """按 owner/novel 范围读取 Visual Bible 候选版本视图（31-04 只读工具）。

    显式 ``version_id`` → 单个候选信封（owner/novel 越界 → None，404-hide）；
    缺省 → 版本列表。``approved_only=True`` 只保留 review_state=approved 的
    版本（D-30-04 approval 权威仍只在 FastAPI review API，本工具只读）。
    """
    if version_id is not None:
        try:
            view = await load_visual_bible_version_view(
                db,
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_id,
            )
        except CandidateNotFoundError:
            return None
        return view.model_dump(mode="json")
    views = await list_visual_bible_versions(
        db, owner_id=owner_id, novel_id=novel_id
    )
    if approved_only:
        views = [view for view in views if view.review_state == "approved"]
    return {
        "items": [view.model_dump(mode="json") for view in views],
        "total": len(views),
    }


async def _resolve_world_model_version(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
) -> int:
    """显式 version 直接返回；缺省取该 owner/novel 最新版本（无 → 404-hide）。"""
    if version_id is not None:
        return int(version_id)
    versions = await WorldModelEventQueries(db).list_versions(
        owner_id=owner_id, novel_id=novel_id
    )
    if not versions:
        raise NotFoundError("world-model projection not found in owner scope")
    return versions[-1]


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
            # Phase 27 世界模型只读工具（27-05）。
            "get_events": self._get_events,
            "get_character_state": self._get_character_state,
            "get_character_knowledge": self._get_character_knowledge,
            "get_world_rules": self._get_world_rules,
            "get_evidence_span": self._get_evidence_span,
            # Phase 30 Visual Bible 只读工具（31-04）。
            "get_visual_bible": self._get_visual_bible,
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
            return {key: ToolFacade._to_json_safe(value) for key, value in raw.items()}
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

    async def _search_novel_text(
        self, *, db, novel: Novel, owner_id: int, params: dict
    ):
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
        cutoff = None if persisted_full_book else await self.cutoff_resolver(db, novel)
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
        active = await svc(source=TimelineVersionSource.ACTIVE, **common)
        running = await svc(source=TimelineVersionSource.RUNNING_CANDIDATE, **common)
        return TimelineEnvelope(active=active, running_candidate=running)

    async def _get_relationships(
        self, *, db, novel: Novel, owner_id: int, params: dict
    ):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = None if persisted_full_book else await self.cutoff_resolver(db, novel)
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

    async def _get_narrative_memory(
        self, *, db, novel: Novel, owner_id: int, params: dict
    ):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = None if persisted_full_book else await self.cutoff_resolver(db, novel)
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

    # ── Phase 27 世界模型只读工具（27-05） ──

    async def _resolve_world_cutoff(self, db, novel, params) -> int:
        """服务端截止点权威（D-05/D-07）：显式 cutoff 超限 → beyond_cutoff。

        full_book 只读持久化开关（_persisted_full_book）；显式 cutoff 提供时
        超过服务端截止点被拒绝，绝不越权到整本书。
        """
        persisted_full_book = _persisted_full_book(novel)
        server_cutoff = (
            None if persisted_full_book else await self.cutoff_resolver(db, novel)
        )
        explicit = params.get("cutoff")
        if explicit is not None:
            if server_cutoff is not None and int(explicit) > int(server_cutoff):
                raise BeyondCutoffError(
                    f"cutoff {explicit} 超出服务端截止点 {server_cutoff}"
                )
            return int(explicit)
        return int(server_cutoff or 0)

    async def _get_events(self, *, db, novel, owner_id, params):
        svc = self._svc("get_events", _default_get_events)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        payload = await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            cutoff=cutoff,
        )
        if payload is None:
            raise NotFoundError("world-model events not found in scope")
        return payload

    async def _get_character_state(self, *, db, novel, owner_id, params):
        svc = self._svc("get_character_state", _default_get_character_state)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            subject=str(params["subject"]),
            cutoff=cutoff,
            pov=params.get("pov"),
        )

    async def _get_character_knowledge(self, *, db, novel, owner_id, params):
        svc = self._svc("get_character_knowledge", _default_get_character_knowledge)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            subject=str(params["subject"]),
            cutoff=cutoff,
            pov=params.get("pov"),
        )

    async def _get_world_rules(self, *, db, novel, owner_id, params):
        svc = self._svc("get_world_rules", _default_get_world_rules)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            cutoff=cutoff,
        )

    async def _get_evidence_span(self, *, db, novel, owner_id, params):
        svc = self._svc("get_evidence_span", _default_get_evidence_span)
        span = await svc(
            db,
            chapter_id=int(params["chapter_id"]),
            source_start=int(params["source_start"]),
            source_end=int(params["source_end"]),
            content_hash=str(params["content_hash"]),
        )
        if span is None:
            raise NotFoundError("章节不存在")
        if span.get("novel_id") != novel.id:
            raise NotFoundError("章节不存在")
        cutoff = await self.cutoff_resolver(db, novel)
        if cutoff is not None and int(span["chapter_number"]) > int(cutoff):
            raise BeyondCutoffError(
                f"章节 {span['chapter_number']} 超出当前阅读进度截止点 {cutoff}"
            )
        return span

    async def _get_visual_bible(self, *, db, novel, owner_id, params):
        svc = self._svc("get_visual_bible", _default_get_visual_bible)
        payload = await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
            approved_only=bool(params.get("approved_only", False)),
        )
        if payload is None:
            raise NotFoundError("visual bible version not found in scope")
        return payload


def _json_default(obj: Any) -> str:
    """兜底序列化：datetime / Decimal 等非 JSON 原生类型转字符串。"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


# 全局单例：API 路由与测试共用；测试可用独立实例注入 stub。
tool_facade = ToolFacade()
