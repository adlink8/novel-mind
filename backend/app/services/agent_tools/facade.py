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
  4. 门面对既有领域**只读**（D-22）：不 import 任何领域写入/变异模块。Phase 33
     （33-05）新增唯一 action 工具 ``generate_image_candidate``——它只创建
     **候选**生成作业（服务端 generation gate + 确定性 idempotency key，
     D-33-01..D-33-03），绝不写 Canon / 域表 / ApprovalRequest / published
     状态；候选资产由 durable worker 产出，审批/发布属于 Phase 34。
     ``generate_image_candidate`` 的作业创建复用 illustrations 域确定性服务，
     不越出候选边界。

拆分说明（refactor split）：本模块保留门面本体 —— 冻结工具名表、budget hook
类型/默认实现、``ToolFacade`` 统一执行门面（字节上限/超时/budget/错误码映射）
与全局单例。23 个 ``_default_*`` 服务入口按功能域拆到同目录模块
（``_defaults_reading`` / ``_defaults_analysis`` / ``_defaults_world`` /
``_defaults_visual`` / ``_defaults_derivative`` / ``_defaults_export``），
JSON-safe 视图助手在 ``_tool_views`` 叶模块；本模块显式 re-export 全部同名
符号，``from app.services.agent_tools.facade import X`` 的 import surface 不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

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
from app.services.narrative_memory.structure_query import StructureQueryError
from app.services.timeline.query import resolve_chapter_cutoff

# ────────────────────────── 按功能域的默认服务入口（拆分后 re-export） ──────────────────────────
from ._defaults_analysis import (
    _default_get_clues,
    _default_get_narrative_memory,
    _default_get_relationships,
    _default_get_timeline,
)
from ._defaults_derivative import (
    _default_allow_divergence,
    _default_apply_derivative_edit,
    _default_create_canon_fork,
    _default_publish_derivative_revision,
    _default_publish_derivative_visual,
)
from ._defaults_export import _default_approve_export, _default_materialize_export
from ._defaults_reading import (
    _default_get_chapter,
    _default_get_novel,
    _default_search_novel_text,
)
from ._defaults_visual import (
    _default_attach_illustration_to_text,
    _default_generate_image_candidate,
    _default_get_visual_bible,
    _default_publish_illustration,
)
from ._defaults_world import (
    _default_get_character_knowledge,
    _default_get_character_state,
    _default_get_events,
    _default_get_evidence_span,
    _default_get_world_rules,
    _resolve_world_model_version,
)

logger = logging.getLogger(__name__)

# 冻结的 16 个域工具名（25.2-03 skill.yaml 的 allowed_tools 白名单镜像此表；
# 27-05 起加入 Phase 27 世界模型工具 get_events / get_character_state /
# get_character_knowledge / get_world_rules / get_evidence_span；31-04 起加入
# Phase 30 Visual Bible 只读工具 get_visual_bible；33-05 起加入 Phase 33
# 候选生成 action 工具 generate_image_candidate——它只创建候选生成作业，
# 绝不写 Canon / 域表 / ApprovalRequest / published 状态（D-33-01..D-33-03）；
# 34-05 起加入 Phase 34 锚点提议 action 工具 publish_illustration /
# attach_illustration_to_text——它们只创建候选 proposal + pending Web
# ApprovalRequest（D-11/D-15），确定性 publisher 拥有 approved publication；
# 35-05 起加入 Phase 35 canon fork 提议 action 工具 create_canon_fork——它只
# 创建候选 fork + pending Web ApprovalRequest（D-11/D-15），确定性 Fork
# materializer 拥有 approved fork 物化。
# 36-05 起加入 Phase 36 derivative 编辑提议 action 工具 apply_derivative_edit——
# 它只创建候选 DerivativeEditProposal + pending Web ApprovalRequest（D-11/D-15），
# 确定性 Revision Service（apply_agent_edit）拥有 approved proposal 应用。
# 37-05 起加入 Phase 37 derivative generation action 工具 allow_divergence /
# publish_derivative_revision——前者只为 blocked/needs_override 候选创建显式
# divergence override + pending Web ApprovalRequest；后者只在 allow_divergence
# approval 批准 + 完整 revalidation 通过后为同一候选创建**独立** publish
# ApprovalRequest（绑定相同 draft_hash + canon_delta_hash）。两者都绝不发布——
# 确定性 revision publisher（consume_publish_approval -> approve_override）拥有
# approved Fanfiction Canon 物化，绝不写 Original Canon。
# 38-05 起加入 Phase 38 branch-aware derivative visual action 工具
# publish_derivative_visual——它只为已存储 candidate asset 创建 pending Web
# ApprovalRequest（payload_hash 绑定候选冻结血缘：asset_id/content_hash/
# scene_spec_hash/divergence_manifest_hash/consistency_verdict/source_snapshot_hash/
# fork_id；blocked candidate / wrong owner/branch/fork → fail closed）。绝不发布——
# 确定性 review seam（review_candidate_asset -> apply_derivative_asset_review）拥有
# approved published asset 物化，绝不写 Original Visual Bible。
# 39-05 起加入 Phase 39 derivative export action 工具 approve_export /
# materialize_export——前者为已 finalize 候选 ExportPreparationArtifact 创建
# pending Web ApprovalRequest（payload_hash 绑定 artifact revision + 确定性
# preparation_hash；wrong owner/branch/fork/stale hash → fail closed）；后者是
# 确定性 materializer：只接受 approved artifact + preparation_hash 匹配的
# approve_export ApprovalRequest，把候选 artifact 推进为 approved 并产出可复现
# bundle（frozen manifest 复算），绝不写 Original Canon / 域表 / Artifact 状态 /
# bundle（download 只读）。
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
    "generate_image_candidate",
    "publish_illustration",
    "attach_illustration_to_text",
    "create_canon_fork",
    "apply_derivative_edit",
    "allow_divergence",
    "publish_derivative_revision",
    "publish_derivative_visual",
    "approve_export",
    "materialize_export",
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


# ────────────────────────── 门面本体 ──────────────────────────


class ToolFacade:
    """12 个只读工具 + 11 个候选 action 工具的统一执行门面。

    所有强制点（字节上限 / 超时 / budget hook / 错误码映射）都在
    ``execute`` 内完成；owner / cutoff 逻辑复用现有服务。Phase 33 的
    ``generate_image_candidate`` 只创建候选作业（candidate-only）；Phase 34
    ``publish_illustration`` / ``attach_illustration_to_text`` 只创建候选
    proposal + pending Web ApprovalRequest；Phase 35 ``create_canon_fork`` 只
    创建候选 fork + pending Web ApprovalRequest；Phase 36
    ``apply_derivative_edit`` 只创建候选 DerivativeEditProposal + pending Web
    ApprovalRequest——确定性 Revision Service 拥有 approved proposal 应用。
    Phase 37 ``allow_divergence`` / ``publish_derivative_revision`` 只创建
    divergence override / 独立 publish ApprovalRequest（相同 hash 绑定）——
    确定性 revision publisher（``consume_publish_approval``）拥有 approved
    Fanfiction Canon 物化。Phase 38 ``publish_derivative_visual`` 只为已存储
    candidate 创建 pending publish ApprovalRequest（绑定候选冻结血缘）——
    确定性 review seam 拥有 approved published asset 物化，绝不写 Original
    Visual Bible。Phase 39 ``approve_export`` 只为已 finalize 候选
    ExportPreparationArtifact 创建 pending approve_export ApprovalRequest
    （绑定 artifact revision + preparation_hash）；``materialize_export`` 是
    确定性 materializer——只接受 approved artifact + preparation_hash 匹配的
    approve_export approval，把候选 artifact 推进为 approved 并产出可复现
    bundle（frozen manifest 复算），绝不写 Original Canon / 域表 / approval
    lineage（download 只读）。
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
            # Phase 33 候选生成 action 工具（33-05）：只创建候选作业。
            "generate_image_candidate": self._generate_image_candidate,
            # Phase 34 锚点提议 action 工具（34-05）：只创建候选 proposal +
            # pending Web ApprovalRequest；确定性 publisher 拥有 publication。
            "publish_illustration": self._publish_illustration,
            "attach_illustration_to_text": self._attach_illustration_to_text,
            # Phase 35 canon fork 提议 action 工具（35-05）：只创建候选 fork +
            # pending Web ApprovalRequest；确定性 Fork materializer 拥有 approved
            # fork 物化。
            "create_canon_fork": self._create_canon_fork,
            # Phase 36 derivative 编辑提议 action 工具（36-05）：只创建候选
            # proposal + pending Web ApprovalRequest；确定性 Revision Service
            # 拥有 approved proposal 应用。
            "apply_derivative_edit": self._apply_derivative_edit,
            # Phase 37 derivative generation action 工具（37-05）：只创建
            # divergence override / 独立 publish ApprovalRequest（相同 hash 绑定）；
            # 确定性 revision publisher 拥有 approved Fanfiction Canon 物化。
            "allow_divergence": self._allow_divergence,
            "publish_derivative_revision": self._publish_derivative_revision,
            # Phase 38 branch-aware derivative visual action 工具（38-05）：只创建
            # pending publish ApprovalRequest（绑定候选冻结血缘）；确定性 review
            # seam 拥有 approved published asset 物化，绝不写 Original Visual Bible。
            "publish_derivative_visual": self._publish_derivative_visual,
            # Phase 39 derivative export action 工具（39-05）：approve_export 只
            # 创建 pending approve_export ApprovalRequest（绑定 artifact revision +
            # preparation_hash）；materialize_export 是确定性 materializer——只
            # 接受 approved artifact + preparation_hash 匹配的 approval，把候选
            # artifact 推进为 approved 并产出可复现 bundle，绝不写 Original Canon。
            "approve_export": self._approve_export,
            "materialize_export": self._materialize_export,
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
        outcome = await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            query=params["query"],
            mode=params.get("mode", "auto"),
            top_k=int(params.get("top_k", 10)),
        )
        # D-05 剧透边界：命中行必须按阅读 cutoff 过滤（与 get_timeline 同一
        # 纪律；持久化 full_book 开关除外）。未过滤的超 cutoff 命中既泄露原文
        # 片段进 transcript，又让模型拿去物化 span 时全部 beyond_cutoff 撞墙。
        if _persisted_full_book(novel):
            return outcome
        cutoff = await self.cutoff_resolver(db, novel)
        rows = list(outcome.get("results") or [])
        if cutoff is None or not rows:
            return outcome
        chapter_ids = [r.get("chapter_id") for r in rows if r.get("chapter_id")]
        if not chapter_ids:
            return outcome
        from app.models.novel import Chapter

        numbers = (
            await db.execute(
                select(Chapter.id, Chapter.chapter_number).where(
                    Chapter.id.in_(chapter_ids)
                )
            )
        ).all()
        chapter_number = {cid: num for cid, num in numbers}
        filtered = [
            row
            for row in rows
            if row.get("chapter_id") is not None
            and chapter_number.get(row["chapter_id"], 0) <= int(cutoff)
        ]
        return {**outcome, "results": filtered}

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
        raw_hash = params.get("content_hash")
        raw_start = params.get("source_start")
        raw_end = params.get("source_end")
        raw_chunk = params.get("chunk_id")
        span = await svc(
            db,
            chapter_id=int(params["chapter_id"]),
            source_start=int(raw_start) if raw_start is not None else None,
            source_end=int(raw_end) if raw_end is not None else None,
            content_hash=str(raw_hash) if raw_hash is not None else None,
            chunk_id=int(raw_chunk) if raw_chunk is not None else None,
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

    async def _generate_image_candidate(self, *, db, novel, owner_id, params):
        """创建候选生成作业（服务端 generation gate，candidate-only，D-33-01）。"""
        svc = self._svc("generate_image_candidate", _default_generate_image_candidate)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _publish_illustration(self, *, db, novel, owner_id, params):
        """创建候选锚点 proposal + pending Web ApprovalRequest（candidate-only，D-34-01）。"""
        svc = self._svc("publish_illustration", _default_publish_illustration)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _attach_illustration_to_text(self, *, db, novel, owner_id, params):
        """把锚点绑定到精确文本跨度（candidate-only；attach action 也要求 Web Approval）。"""
        svc = self._svc(
            "attach_illustration_to_text", _default_attach_illustration_to_text
        )
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _create_canon_fork(self, *, db, novel, owner_id, params):
        """创建候选 canon fork + pending Web ApprovalRequest（candidate-only，D-35-03）。"""
        svc = self._svc("create_canon_fork", _default_create_canon_fork)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _apply_derivative_edit(self, *, db, novel, owner_id, params):
        """创建候选 derivative edit + pending Web ApprovalRequest（candidate-only，D-36-02）。"""
        svc = self._svc("apply_derivative_edit", _default_apply_derivative_edit)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _allow_divergence(self, *, db, novel, owner_id, params):
        """创建显式 divergence override + pending Web ApprovalRequest（candidate-only，D-37-03）。"""
        svc = self._svc("allow_divergence", _default_allow_divergence)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _publish_derivative_revision(self, *, db, novel, owner_id, params):
        """创建独立 publish ApprovalRequest（相同 hash 绑定；candidate-only，37-05）。"""
        svc = self._svc(
            "publish_derivative_revision", _default_publish_derivative_revision
        )
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _publish_derivative_visual(self, *, db, novel, owner_id, params):
        """创建独立 publish ApprovalRequest（绑定候选冻结血缘；candidate-only，38-05）。"""
        svc = self._svc("publish_derivative_visual", _default_publish_derivative_visual)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _approve_export(self, *, db, novel, owner_id, params):
        """创建独立 approve_export ApprovalRequest（绑定 artifact revision +
        preparation_hash；candidate-only，39-05）。"""
        svc = self._svc("approve_export", _default_approve_export)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _materialize_export(self, *, db, novel, owner_id, params):
        """确定性 materializer：只接受 approved artifact + preparation_hash 匹配的
        approve_export approval，产出可复现 bundle（approved-only，39-05）。"""
        svc = self._svc("materialize_export", _default_materialize_export)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )


def _json_default(obj: Any) -> str:
    """兜底序列化：datetime / Decimal 等非 JSON 原生类型转字符串。"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


# 全局单例：API 路由与测试共用；测试可用独立实例注入 stub。
tool_facade = ToolFacade()


# ---------------------------------------------------------------------------
# Lazy re-export of JSON-safe helper symbols (keeps the historical module
# surface; these are no longer referenced inside facade.py itself).
# ---------------------------------------------------------------------------

_HELPER_EXPORTS = {
    # world-model serializers (moved to _defaults_world)
    "_epistemic_answer_to_json",
    "_merge_state_answers",
    # candidate proposal / job views (moved to _tool_views leaf)
    "_agent_edit_proposal_view_for_tool",
    "_anchor_proposal_view_for_tool",
    "_fork_proposal_view_for_tool",
    "_job_view_for_tool",
}


def __getattr__(name: str) -> Any:
    if name in {"_epistemic_answer_to_json", "_merge_state_answers"}:
        from ._defaults_world import (  # noqa: PLC0415
            _epistemic_answer_to_json,
            _merge_state_answers,
        )

        return {
            "_epistemic_answer_to_json": _epistemic_answer_to_json,
            "_merge_state_answers": _merge_state_answers,
        }[name]
    if name in {
        "_agent_edit_proposal_view_for_tool",
        "_anchor_proposal_view_for_tool",
        "_fork_proposal_view_for_tool",
        "_job_view_for_tool",
    }:
        from ._tool_views import (  # noqa: PLC0415
            _agent_edit_proposal_view_for_tool,
            _anchor_proposal_view_for_tool,
            _fork_proposal_view_for_tool,
            _job_view_for_tool,
        )

        return {
            "_agent_edit_proposal_view_for_tool": _agent_edit_proposal_view_for_tool,
            "_anchor_proposal_view_for_tool": _anchor_proposal_view_for_tool,
            "_fork_proposal_view_for_tool": _fork_proposal_view_for_tool,
            "_job_view_for_tool": _job_view_for_tool,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
