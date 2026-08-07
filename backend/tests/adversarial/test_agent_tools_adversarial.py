"""
智能体工具门面对抗测试（25.2-02 / D-07 / REQ-AGENT-02）。

按 RESEARCH Validation Architecture 采样：**每个工具 × 每个 fail-closed 轴**，
而不是只测一个代表性工具。两层：

1. 运行时对抗：
   - cross-owner：他人小说 ID → 404-hide（无 403 oracle），7 个路由全查；
   - beyond_cutoff：请求超截止点 → 冻结码 beyond_cutoff + 响应零受保护内容；
   - budget_exceeded：超预算 → fail closed，底层服务【从未】被调用；
   - output_too_large：超大响应 → 冻结码；
   - timeout：慢上游 → 冻结码；
   - full_book 伪造：请求带 full_book=true 但无持久化开关 → 视为 cutoff-limited。
2. 静态 gate（对齐 test_reader_chat_boundaries.py）：扫描 services/agent_tools/
   与 api/agent_tools.py，禁止 import 领域变异模块 / 禁止构造 LLM 调用 ——
   工具面只读（D-22），强制留在服务端（D-07）。
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from httpx import AsyncClient

from app.services.agent_tools.errors import AgentToolError
from app.services.agent_tools.facade import ToolFacade

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

ROOT = Path(__file__).resolve().parents[2]
AGENT_TOOLS_SERVICE = ROOT / "app" / "services" / "agent_tools"
AGENT_TOOLS_API = ROOT / "app" / "api" / "agent_tools.py"

# 14 个域工具名（与 facade.TOOL_NAMES 一致性由测试断言）。
ALL_TOOLS = (
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    # Phase 27 世界模型只读工具（27-05）。
    "get_events",
    "get_character_state",
    "get_character_knowledge",
    "get_world_rules",
    "get_evidence_span",
    # Phase 30 Visual Bible 只读工具（31-04）。
    "get_visual_bible",
    # Phase 33 候选生成 action 工具（33-05）：只创建候选作业。
    "generate_image_candidate",
)

# Phase 34 锚点 action 工具（34-05）：创建候选 proposal + pending ApprovalRequest。
# 由 test_phase_34_skill.py 专项覆盖（伪造/过期/取消/拒绝审批等对抗路径），
# 不参与本文件纯读 adversarial 轴。
ACTION_TOOLS = (
    "publish_illustration",
    "attach_illustration_to_text",
    # Phase 35 fork 创建 action 工具（35-05）：创建候选 proposal + pending ApprovalRequest。
    "create_canon_fork",
    # Phase 36 derivative 编辑 action 工具（36-05）：创建候选 proposal + pending
    # ApprovalRequest；确定性 Revision Service 拥有 approved proposal 应用。
    "apply_derivative_edit",
    # Phase 37 derivative generation action 工具（37-05）：创建 divergence
    # override / 独立 publish ApprovalRequest；确定性 revision publisher 拥有
    # approved Fanfiction Canon 物化。
    "allow_divergence",
    "publish_derivative_revision",
    # Phase 38 branch-aware derivative visual action 工具（38-05）：创建
    # pending publish ApprovalRequest（绑定候选冻结血缘）；确定性 review seam
    # 拥有 approved published asset 物化，绝不写 Original Visual Bible。
    "publish_derivative_visual",
    # Phase 39 derivative export action 工具（39-05）：approve_export 创建
    # pending approve_export ApprovalRequest（绑定 artifact revision +
    # preparation_hash）；materialize_export 是确定性 materializer（只接受
    # approved artifact + preparation_hash 匹配的 approval，产出可复现 bundle）。
    # 由 test_phase_39_skill.py 专项覆盖（伪造/过期/取消/拒绝审批等对抗路径）。
    "approve_export",
    "materialize_export",
)

# 每个工具的 HTTP 路由（cross-owner 轴使用）。
TOOL_ROUTES = {
    "get_novel": "/api/agent-tools/get_novel",
    "get_chapter": "/api/agent-tools/get_chapter",
    "search_novel_text": "/api/agent-tools/search_novel_text",
    "get_timeline": "/api/agent-tools/get_timeline",
    "get_relationships": "/api/agent-tools/get_relationships",
    "get_clues": "/api/agent-tools/get_clues",
    "get_narrative_memory": "/api/agent-tools/get_narrative_memory",
    "get_events": "/api/agent-tools/get_events",
    "get_character_state": "/api/agent-tools/get_character_state",
    "get_character_knowledge": "/api/agent-tools/get_character_knowledge",
    "get_world_rules": "/api/agent-tools/get_world_rules",
    "get_evidence_span": "/api/agent-tools/get_evidence_span",
    "get_visual_bible": "/api/agent-tools/get_visual_bible",
    "generate_image_candidate": "/api/agent-tools/generate_image_candidate",
}

# 受保护内容哨兵：断言任何响应体都不含它。
PROTECTED = "剧透保护正文内容"

# 超出 fake_cutoff=3 的章节号。
BEYOND_CHAPTER = 9


def _params(tool: str, **extra) -> dict:
    base = {
        "get_novel": {},
        "get_chapter": {"chapter_id": 1},
        "search_novel_text": {"query": "对抗"},
        "get_timeline": {},
        "get_relationships": {},
        "get_clues": {},
        "get_narrative_memory": {},
        # Phase 27 世界模型工具（27-05）。
        "get_events": {"version_id": 1},
        "get_character_state": {"version_id": 1, "subject": "林安"},
        "get_character_knowledge": {"version_id": 1, "subject": "林安"},
        "get_world_rules": {"version_id": 1},
        "get_evidence_span": {
            "chapter_id": 1,
            "source_start": 0,
            "source_end": 5,
            "content_hash": "a" * 64,
        },
        "get_visual_bible": {"version_id": 1},
        "generate_image_candidate": {
            "prompt_revision_id": 1,
            "job_key": "adversarial-job",
        },
    }[tool]
    return {**base, **extra}


def _novel(reading_progress: dict | None = None, title: str = "对抗测试小说"):
    from app.models.novel import Novel

    return Novel(
        id=1,
        owner_id=1,
        title=title,
        chapter_count=10,
        word_count=0,
        status="ready",
        reading_progress=reading_progress or {},
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


async def _fake_cutoff(db, novel) -> int:
    return 3


def _chapter(chapter_number: int, content: str):
    from app.models.novel import Chapter

    return Chapter(
        id=chapter_number,
        novel_id=1,
        chapter_number=chapter_number,
        title=f"第{chapter_number}章",
        content=content,
        word_count=len(content),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


# ────────────────────────── stub 服务构造器 ──────────────────────────


async def _call_handler(handler, **kwargs):
    """调用 handler：兼容协程与同步返回（记录型 stub 常用同步 def）。"""
    result = handler(**kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _service_stub(tool: str, handler):
    """返回一个匹配 facade handler 调用签名的 stub，委托给 ``handler``。"""

    async def stub_get_novel(db, novel_id):
        return await _call_handler(handler, db=db, novel_id=novel_id)

    async def stub_get_chapter(db, chapter_id):
        return await _call_handler(handler, db=db, chapter_id=chapter_id)

    async def stub_search(db, *, owner_id, novel_id, query, mode, top_k):
        return await _call_handler(handler, db=db, owner_id=owner_id, novel_id=novel_id)

    async def stub_timeline(
        db,
        *,
        novel,
        owner_id,
        source,
        ordering,
        person,
        include_causal,
        request_full_book,
        chapter_start,
        chapter_end,
    ):
        return await _call_handler(
            handler,
            db=db,
            novel=novel,
            owner_id=owner_id,
            source=source,
            ordering=ordering,
            request_full_book=request_full_book,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
        )

    async def stub_relationships(
        db,
        *,
        novel,
        owner_id,
        source,
        version_id,
        through_chapter,
        request_full_book,
        character_id,
        relation_type,
        include_provisional,
    ):
        return await _call_handler(
            handler,
            db=db,
            novel=novel,
            owner_id=owner_id,
            source=source,
            request_full_book=request_full_book,
            through_chapter=through_chapter,
        )

    async def stub_clues(
        db,
        *,
        novel,
        owner_id,
        request_full_book,
        character_id,
        status_filter,
    ):
        return await _call_handler(
            handler,
            db=db,
            novel=novel,
            owner_id=owner_id,
            request_full_book=request_full_book,
        )

    async def stub_nm(db, *, owner_id, novel_id, version_id, view, through_chapter):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            view=view,
            through_chapter=through_chapter,
        )

    async def stub_events(db, *, owner_id, novel_id, version_id, cutoff):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            cutoff=cutoff,
        )

    async def stub_character_state(
        db, *, owner_id, novel_id, version_id, subject, cutoff, pov
    ):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            subject=subject,
            cutoff=cutoff,
            pov=pov,
        )

    async def stub_character_knowledge(
        db, *, owner_id, novel_id, version_id, subject, cutoff, pov
    ):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            subject=subject,
            cutoff=cutoff,
            pov=pov,
        )

    async def stub_world_rules(db, *, owner_id, novel_id, version_id, cutoff):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            cutoff=cutoff,
        )

    async def stub_evidence_span(
        db, *, chapter_id, source_start, source_end, content_hash
    ):
        return await _call_handler(
            handler,
            db=db,
            chapter_id=chapter_id,
            source_start=source_start,
            source_end=source_end,
            content_hash=content_hash,
        )

    async def stub_visual_bible(db, *, owner_id, novel_id, version_id, approved_only):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            approved_only=approved_only,
        )

    async def stub_generate_image_candidate(db, *, owner_id, novel_id, params):
        return await _call_handler(
            handler,
            db=db,
            owner_id=owner_id,
            novel_id=novel_id,
            params=params,
        )

    stubs = {
        "get_novel": stub_get_novel,
        "get_chapter": stub_get_chapter,
        "search_novel_text": stub_search,
        "get_timeline": stub_timeline,
        "get_relationships": stub_relationships,
        "get_clues": stub_clues,
        "get_narrative_memory": stub_nm,
        "get_events": stub_events,
        "get_character_state": stub_character_state,
        "get_character_knowledge": stub_character_knowledge,
        "get_world_rules": stub_world_rules,
        "get_evidence_span": stub_evidence_span,
        "get_visual_bible": stub_visual_bible,
        "generate_image_candidate": stub_generate_image_candidate,
    }
    return stubs[tool]


# ────────────────────────── 轴 1：cross-owner 404-hide ──────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_cross_owner_novel_id_404_hides(tool, client: "AsyncClient", db_session):
    """他人小说 ID：7 个路由全部 404-hide，绝不 403（无 oracle）。

    注意：第一个注册用户是 bootstrap admin（is_superuser=True），会绕过
    owner 检查。因此先经 db_session 造一个他人小说，再注册第二个普通用户
    （非 superuser）作为攻击者访问。
    """
    from app.models.novel import Novel
    from app.models.user import User

    owner = User(
        username="foreign_owner",
        email="foreign_owner@example.com",
        hashed_password="x",
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(title="他人私有小说", owner_id=owner.id)
    db_session.add(novel)
    await db_session.commit()

    # 第二个注册用户（非 bootstrap）→ is_superuser=False。
    register_resp = await client.post(
        "/api/auth/register",
        json={
            "username": "intruder",
            "email": "intruder@example.com",
            "password": "testpass123",
        },
    )
    assert register_resp.status_code == 201
    login_resp = await client.post(
        "/api/auth/login",
        json={"username": "intruder", "password": "testpass123"},
    )
    assert login_resp.status_code == 200
    client.headers["Authorization"] = f"Bearer {login_resp.json()['access_token']}"

    resp = await client.post(TOOL_ROUTES[tool], params={"novel_id": novel.id}, json={})
    assert resp.status_code == 404, f"{tool} 未 404-hide: {resp.text}"
    assert resp.status_code != 403, f"{tool} 泄露了 403 oracle"


# ────────────────────────── 轴 2：beyond_cutoff ──────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_beyond_cutoff_no_content_leak(tool):
    """超截止点：稳定码 beyond_cutoff；响应体零受保护内容。"""
    facade = ToolFacade(cutoff_resolver=_fake_cutoff)
    params = {
        "get_chapter": {"chapter_id": BEYOND_CHAPTER},
        "get_timeline": {"chapter_end": BEYOND_CHAPTER},
        "get_relationships": {"through_chapter": BEYOND_CHAPTER},
        "get_narrative_memory": {
            "view": "tree",
            "version_id": 1,
            "through_chapter": BEYOND_CHAPTER,
        },
        # 无章节范围参数的工具：底层服务本就 cutoff-limited。
        "get_novel": {},
        "search_novel_text": {"query": "对抗"},
        "get_clues": {},
        # Phase 27 世界模型工具：显式 cutoff 超过服务端截止点 → beyond_cutoff。
        "get_events": {"version_id": 1, "cutoff": BEYOND_CHAPTER},
        "get_character_state": {
            "version_id": 1,
            "subject": "林安",
            "cutoff": BEYOND_CHAPTER,
        },
        "get_character_knowledge": {
            "version_id": 1,
            "subject": "林安",
            "cutoff": BEYOND_CHAPTER,
        },
        "get_world_rules": {"version_id": 1, "cutoff": BEYOND_CHAPTER},
        # get_evidence_span 按章节号执行 cutoff（handler 在服务返回后裁决）。
        "get_evidence_span": {
            "chapter_id": 1,
            "source_start": 0,
            "source_end": 5,
            "content_hash": "a" * 64,
        },
        # get_visual_bible 无章节范围参数：底层读取本就候选/只读。
        "get_visual_bible": {},
        # generate_image_candidate 无章节范围参数：只创建候选作业（服务端 gate）。
        "generate_image_candidate": {
            "prompt_revision_id": 1,
            "job_key": "adversarial-job",
        },
    }[tool]

    if tool == "get_chapter":
        service = _service_stub(
            tool, lambda db, **kw: _chapter(BEYOND_CHAPTER, PROTECTED)
        )
        facade = ToolFacade(
            cutoff_resolver=_fake_cutoff,
            service_overrides={"get_chapter": service},
        )
        with pytest.raises(AgentToolError) as excinfo:
            await facade.execute(
                tool, db=object(), novel=_novel(), owner_id=1, params=params
            )
        assert excinfo.value.code == "beyond_cutoff"
        assert PROTECTED not in str(excinfo.value)
        return

    if tool == "get_evidence_span":
        # stub 返回超截止点章节的 span → handler 在服务返回后拒绝（beyond_cutoff）。
        service = _service_stub(
            tool,
            lambda db, **kw: {
                "evidence_key": "evidence:1",
                "chapter_id": 1,
                "chapter_number": BEYOND_CHAPTER,
                "novel_id": 1,
                "source_start": 0,
                "source_end": 5,
                "content_hash": "a" * 64,
                "excerpt": PROTECTED,
            },
        )
        facade = ToolFacade(
            cutoff_resolver=_fake_cutoff,
            service_overrides={"get_evidence_span": service},
        )
        with pytest.raises(AgentToolError) as excinfo:
            await facade.execute(
                tool, db=object(), novel=_novel(), owner_id=1, params=params
            )
        assert excinfo.value.code == "beyond_cutoff"
        assert PROTECTED not in str(excinfo.value)
        return

    if tool in (
        "get_timeline",
        "get_relationships",
        "get_narrative_memory",
        "get_events",
        "get_character_state",
        "get_character_knowledge",
        "get_world_rules",
    ):
        called = []

        def mark(*a, **kw):
            called.append(1)

        facade = ToolFacade(
            cutoff_resolver=_fake_cutoff,
            service_overrides={tool: _service_stub(tool, mark)},
        )
        with pytest.raises(AgentToolError) as excinfo:
            await facade.execute(
                tool, db=object(), novel=_novel(), owner_id=1, params=params
            )
        assert excinfo.value.code == "beyond_cutoff"
        assert called == [], "超截止点的请求必须在调用服务前被拦截"
        return

    # 无范围参数的工具：服务返回结果，但响应体必须不含受保护内容。
    async def safe_get_novel(db, **kw):
        return _novel()

    async def safe_search(db, **kw):
        return {"results": [], "resolved_mode": "chunks", "fallback_reason": None}

    async def safe_clues(db, **kw):
        return {"active": None, "running_candidate": None}

    async def safe_evidence_span(db, **kw):
        return {
            "evidence_key": "evidence:1",
            "chapter_id": 1,
            "chapter_number": 1,
            "novel_id": 1,
            "source_start": 0,
            "source_end": 5,
            "content_hash": "a" * 64,
            "excerpt": "安全切片",
        }

    async def safe_visual_bible(db, **kw):
        return {"items": [], "total": 0}

    async def safe_generate_image_candidate(db, **kw):
        return {
            "id": 1,
            "owner_id": 1,
            "novel_id": 1,
            "job_key": "safe-job",
            "idempotency_key": "a" * 64,
            "status": "queued",
            "status_reason": None,
            "error_code": None,
            "retry_count": 0,
            "scene_spec_hash": "a" * 64,
            "prompt_revision_id": 1,
            "prompt_revision_hash": "a" * 64,
            "visual_bible_revision_hash": "a" * 64,
            "source_snapshot_id": "ss-1",
            "source_snapshot_hash": "a" * 64,
            "cutoff_chapter": 1,
            "config_hash": "a" * 64,
            "candidate_only": True,
        }

    safe = {
        "get_novel": safe_get_novel,
        "search_novel_text": safe_search,
        "get_clues": safe_clues,
        "get_evidence_span": safe_evidence_span,
        "get_visual_bible": safe_visual_bible,
        "generate_image_candidate": safe_generate_image_candidate,
    }[tool]
    facade = ToolFacade(
        cutoff_resolver=_fake_cutoff,
        service_overrides={tool: _service_stub(tool, safe)},
    )
    payload = await facade.execute(
        tool, db=object(), novel=_novel(), owner_id=1, params=params
    )
    assert PROTECTED not in json.dumps(payload, ensure_ascii=False)


# ────────────────────────── 轴 3：budget_exceeded fail-closed ──────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_over_budget_fail_closed_service_never_called(tool):
    """超预算：budget_exceeded 且底层服务【从未】被调用。"""
    from app.services.agent_tools.errors import BudgetExceededError

    calls: list[str] = []

    def mark(*a, **kw):
        calls.append(tool)

    async def budget_hook(tool_name, params):
        raise BudgetExceededError("对抗：预算已用尽")

    facade = ToolFacade(
        budget_hook=budget_hook,
        service_overrides={tool: _service_stub(tool, mark)},
    )
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            tool, db=object(), novel=_novel(), owner_id=1, params=_params(tool)
        )
    assert excinfo.value.code == "budget_exceeded"
    assert calls == [], f"{tool} 超预算后仍触发了底层服务"


# ────────────────────────── 轴 4：output_too_large ──────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_oversized_response_returns_output_too_large(tool):
    """超大 stub 响应：冻结码 output_too_large（不返回部分结果）。"""
    from app.schemas.relationship import (
        RelationshipGraphEnvelope,
        RelationshipVersionSource,
    )
    from app.schemas.timeline import (
        TimelineVersionSource,
        TimelineVersionView,
    )

    async def huge_get_novel(db, **kw):
        return _novel(title="巨" * 70000)

    async def huge_get_chapter(db, **kw):
        return _chapter(1, "甲" * 70000)

    async def huge_search(db, **kw):
        return {
            "results": [{"chunk_id": 1, "content_snippet": "乙" * 70000}],
            "resolved_mode": "chunks",
            "fallback_reason": None,
        }

    async def huge_timeline(db, **kw):
        return TimelineVersionView(
            source=TimelineVersionSource.ACTIVE,
            version_id=1,
            status="active",
            previews=["丙" * 70000],
        )

    async def huge_relationships(db, **kw):
        return RelationshipGraphEnvelope(
            novel_id=1,
            version_id=1,
            source=RelationshipVersionSource.ACTIVE,
            through_chapter=1,
            full_book=False,
            cutoff_chapter=1,
            available_character_ids=[1] * 70000,
        )

    async def huge_clues(db, **kw):
        return {"active": {"blob": "丁" * 70000}, "running_candidate": None}

    async def huge_nm(db, **kw):
        return {"versions": [{"blob": "戊" * 70000}]}

    async def huge_events(db, **kw):
        return {"events": [{"description": "己" * 70000}]}

    async def huge_character_state(db, **kw):
        return {"claims": [{"proposition": "庚" * 70000}]}

    async def huge_character_knowledge(db, **kw):
        return {"claims": [{"proposition": "辛" * 70000}]}

    async def huge_world_rules(db, **kw):
        return {"rules": [{"statement": "壬" * 70000}]}

    async def huge_evidence_span(db, **kw):
        return {
            "evidence_key": "evidence:1",
            "chapter_id": 1,
            "chapter_number": 1,
            "novel_id": 1,
            "source_start": 0,
            "source_end": 5,
            "content_hash": "a" * 64,
            "excerpt": "癸" * 70000,
        }

    async def huge_visual_bible(db, **kw):
        return {
            "items": [
                {
                    "version_key": "vb-main",
                    "manifest_hash": "a" * 64,
                    "entities": [{"description": "子" * 70000}],
                }
            ],
            "total": 1,
        }

    async def huge_generate_image_candidate(db, **kw):
        return {
            "id": 1,
            "owner_id": 1,
            "novel_id": 1,
            "job_key": "huge" + "子" * 70000,
            "idempotency_key": "a" * 64,
            "status": "queued",
            "status_reason": None,
            "error_code": None,
            "retry_count": 0,
            "scene_spec_hash": "a" * 64,
            "prompt_revision_id": 1,
            "prompt_revision_hash": "a" * 64,
            "visual_bible_revision_hash": "a" * 64,
            "source_snapshot_id": "ss-1",
            "source_snapshot_hash": "a" * 64,
            "cutoff_chapter": 1,
            "config_hash": "a" * 64,
            "candidate_only": True,
        }

    huge = {
        "get_novel": huge_get_novel,
        "get_chapter": huge_get_chapter,
        "search_novel_text": huge_search,
        "get_timeline": huge_timeline,
        "get_relationships": huge_relationships,
        "get_clues": huge_clues,
        "get_narrative_memory": huge_nm,
        "get_events": huge_events,
        "get_character_state": huge_character_state,
        "get_character_knowledge": huge_character_knowledge,
        "get_world_rules": huge_world_rules,
        "get_evidence_span": huge_evidence_span,
        "get_visual_bible": huge_visual_bible,
        "generate_image_candidate": huge_generate_image_candidate,
    }[tool]
    facade = ToolFacade(
        byte_cap=64 * 1024,
        cutoff_resolver=_fake_cutoff,
        service_overrides={tool: _service_stub(tool, huge)},
    )
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            tool, db=object(), novel=_novel(), owner_id=1, params=_params(tool)
        )
    assert excinfo.value.code == "output_too_large"


# ────────────────────────── 轴 5：timeout ──────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_slow_upstream_times_out_with_stable_code(tool):
    """慢上游：超过 per-tool 超时 → 冻结码 timeout。"""

    async def slow(*a, **kw):
        await asyncio.sleep(0.5)

    facade = ToolFacade(
        timeout=0.05,
        cutoff_resolver=_fake_cutoff,
        service_overrides={tool: _service_stub(tool, slow)},
    )
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            tool, db=object(), novel=_novel(), owner_id=1, params=_params(tool)
        )
    assert excinfo.value.code == "timeout"


# ────────────────────────── 轴 6：full_book 伪造 ──────────────────────────


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_full_book_spoof_without_persisted_switch_is_cutoff_limited(tool):
    """请求带 full_book=true 但无持久化开关 → 视为 cutoff-limited（防剧透）。

    - 有范围参数的工具（get_timeline / get_relationships / get_clues）：
      记录到的 request_full_book 必须为 False；
    - get_chapter：超截止点章节仍被拒绝（beyond_cutoff）；
    - 其余工具：响应零受保护内容。
    """
    received: list[bool] = []

    def record(**kw):
        received.append(bool(kw.get("request_full_book", False)))

    params = _params(tool, full_book=True)  # 伪造裸参数
    facade = ToolFacade(
        cutoff_resolver=_fake_cutoff,
        service_overrides={tool: _service_stub(tool, record)},
    )

    if tool in ("get_timeline", "get_relationships", "get_clues"):
        # 注意 get_clues 的 stub 签名不含 request_full_book 透传，改走自定义 stub。
        if tool == "get_clues":

            async def clues_record(
                db, *, novel, owner_id, request_full_book, character_id, status_filter
            ):
                received.append(bool(request_full_book))
                return {"active": None, "running_candidate": None}

            facade = ToolFacade(
                cutoff_resolver=_fake_cutoff,
                service_overrides={"get_clues": clues_record},
            )
            await facade.execute(
                tool, db=object(), novel=_novel(), owner_id=1, params=params
            )
            assert received == [False]
            return
        await facade.execute(
            tool, db=object(), novel=_novel(), owner_id=1, params=params
        )
        assert received and all(value is False for value in received), (
            f"{tool} 响应了伪造的 full_book=true（无持久化开关）"
        )
        return

    if tool in (
        "get_events",
        "get_character_state",
        "get_character_knowledge",
        "get_world_rules",
    ):
        # 世界模型工具：full_book 伪造必须被忽略——stub 收到的 cutoff 恒为服务端
        # 截止点（fake=3），绝不因 full_book=true 扩展到整本书。
        received_cutoffs: list[int] = []
        safe_payloads = {
            "get_events": {"events": []},
            "get_character_state": {"claims": [], "status": "abstained"},
            "get_character_knowledge": {"claims": [], "status": "abstained"},
            "get_world_rules": {"rules": [], "exceptions": []},
        }

        def record_world_cutoff(db, *, cutoff, **kw):
            received_cutoffs.append(int(cutoff))
            return safe_payloads[tool]

        facade = ToolFacade(
            cutoff_resolver=_fake_cutoff,
            service_overrides={tool: _service_stub(tool, record_world_cutoff)},
        )
        await facade.execute(
            tool, db=object(), novel=_novel(), owner_id=1, params=params
        )
        assert received_cutoffs == [3], (
            f"{tool} 响应了伪造的 full_book=true（无持久化开关）"
        )
        return

    if tool == "get_chapter":
        service = _service_stub(
            tool, lambda db, **kw: _chapter(BEYOND_CHAPTER, PROTECTED)
        )
        facade = ToolFacade(
            cutoff_resolver=_fake_cutoff,
            service_overrides={"get_chapter": service},
        )
        with pytest.raises(AgentToolError) as excinfo:
            await facade.execute(
                tool, db=object(), novel=_novel(), owner_id=1, params=params
            )
        assert excinfo.value.code == "beyond_cutoff"
        assert PROTECTED not in str(excinfo.value)
        return

    # get_novel / search_novel_text / get_narrative_memory / get_evidence_span：
    # 无泄漏即视为 cutoff-limited（get_evidence_span 的章节 cutoff 由 handler 裁决）。
    async def safe_novel(db, **kw):
        return _novel()

    async def safe_search(db, **kw):
        return {"results": [], "resolved_mode": "chunks", "fallback_reason": None}

    async def safe_nm(db, **kw):
        return {"versions": []}

    async def safe_evidence_span(db, **kw):
        return {
            "evidence_key": "evidence:1",
            "chapter_id": 1,
            "chapter_number": 1,
            "novel_id": 1,
            "source_start": 0,
            "source_end": 5,
            "content_hash": "a" * 64,
            "excerpt": "安全切片",
        }

    async def safe_visual_bible(db, **kw):
        return {"items": [], "total": 0}

    async def safe_generate_image_candidate(db, **kw):
        return {
            "id": 1,
            "owner_id": 1,
            "novel_id": 1,
            "job_key": "safe-job",
            "idempotency_key": "a" * 64,
            "status": "queued",
            "status_reason": None,
            "error_code": None,
            "retry_count": 0,
            "scene_spec_hash": "a" * 64,
            "prompt_revision_id": 1,
            "prompt_revision_hash": "a" * 64,
            "visual_bible_revision_hash": "a" * 64,
            "source_snapshot_id": "ss-1",
            "source_snapshot_hash": "a" * 64,
            "cutoff_chapter": 1,
            "config_hash": "a" * 64,
            "candidate_only": True,
        }

    safe = {
        "get_novel": safe_novel,
        "search_novel_text": safe_search,
        "get_narrative_memory": safe_nm,
        "get_evidence_span": safe_evidence_span,
        "get_visual_bible": safe_visual_bible,
        "generate_image_candidate": safe_generate_image_candidate,
    }[tool]
    facade = ToolFacade(
        cutoff_resolver=_fake_cutoff,
        service_overrides={tool: _service_stub(tool, safe)},
    )
    payload = await facade.execute(
        tool, db=object(), novel=_novel(), owner_id=1, params=params
    )
    assert PROTECTED not in json.dumps(payload, ensure_ascii=False)


async def test_full_book_spoof_via_http_is_rejected_by_schema(auth_client, db_session):
    """HTTP 层：裸 full_book 参数被 StrictPydantic 拒绝 → invalid_input。

    需要真实小说通过 require_owned_novel（否则先 404），才能观测到 Schema 422。
    """
    from app.models.novel import Novel
    from app.models.user import User

    from app.core.security import hash_password

    owner = User(
        username="spoof_owner",
        email="spoof_owner@example.com",
        hashed_password=hash_password("x"),
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(title="伪造 full_book 的小说", owner_id=owner.id)
    db_session.add(novel)
    await db_session.commit()

    resp = await auth_client.post(
        "/api/agent-tools/get_timeline",
        params={"novel_id": novel.id},
        json={"full_book": True},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_input"


# ────────────────────────── 静态 gate：只读 + 无 LLM ──────────────────────────


FORBIDDEN_DOMAIN_IMPORTS = (
    "app.services.illustration",  # 插图/作画任务（写入）
    "app.services.canon_space",  # canon/branch 变异
    "app.services.fanfiction",  # 同人文写入
    "app.services.reader_chat",  # reader_chat 写路径 / 事实源（V08-BUILD-05）
    "app.services.creative_projects",  # 创作项目（写入）
    "app.services.creative_generation",  # 创作生成（写入）
    "app.services.analysis_service",  # 分析触发（写入）
    "app.services.import_service",  # 导入管线（写入）
    "app.services.timeline.promotion",  # 时间线晋升（写）
    "app.services.timeline.overrides",  # 时间线人工覆盖（写）
    "app.services.timeline.worker",  # 时间线 worker（写）
    "app.services.clues.overrides",  # 线索人工操作（写）
    "app.services.clues.worker",  # 线索 worker（写）
    "app.services.relationships.overrides",  # 关系覆盖（写）
    "app.models.agent_runtime",  # 25.2-03 运行时（本阶段不可达）
)

# 门面禁止出现任何模型调用构造（强制留在服务端，D-07）。
FORBIDDEN_LLM_SUBSTRINGS = (
    "litellm",
    "acompletion",
    "stream_chat",
    "llm_judge",
)


def _agent_tool_files():
    files = sorted(AGENT_TOOLS_SERVICE.rglob("*.py")) + [AGENT_TOOLS_API]
    assert files, "scan must not be vacuous"
    return files


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    return found


def test_static_gate_tool_names_match_facade():
    from app.services.agent_tools.facade import TOOL_NAMES

    # 读工具（纯只读 adversarial 轴）必须与 facade 读工具子集完全一致，防漂移。
    assert tuple(ALL_TOOLS) == tuple(t for t in TOOL_NAMES if t not in ACTION_TOOLS)
    # action 工具（34-05）由专项集成测试覆盖，不参与纯读 adversarial 轴。
    assert set(ACTION_TOOLS) <= set(TOOL_NAMES)


def test_static_gate_tool_surface_imports_no_domain_mutation_modules():
    violations: list[str] = []
    for path in _agent_tool_files():
        for dotted in _imports_of(path):
            for forbidden in FORBIDDEN_DOMAIN_IMPORTS:
                if dotted == forbidden or dotted.startswith(forbidden + "."):
                    violations.append(f"{path}: imports {dotted}")
    assert not violations, (
        "工具面必须保持只读（D-22），禁止 import 领域变异模块:\n"
        + "\n".join(violations)
    )


def test_static_gate_facade_never_constructs_llm_calls():
    """强制点只存在于 FastAPI 服务端，门面绝不构造模型调用（D-07）。"""
    blobs = [path.read_text(encoding="utf-8").lower() for path in _agent_tool_files()]
    joined = "\n".join(blobs)
    for needle in FORBIDDEN_LLM_SUBSTRINGS:
        assert needle not in joined, f"门面出现了模型调用构造 {needle!r}"


def test_static_gate_error_codes_come_from_frozen_table():
    """错误码必须来自冻结表（单一事实源，不出现硬编码重复表）。"""
    from app.services.agent_tools.errors import AGENT_TOOL_ERROR_CODES

    facade_src = (AGENT_TOOLS_SERVICE / "facade.py").read_text(encoding="utf-8")
    errors_src = (AGENT_TOOLS_SERVICE / "errors.py").read_text(encoding="utf-8")
    # facade 不得再定义第二张错误码表。
    assert "AGENT_TOOL_ERROR_CODES" not in facade_src.split("from app")[0]
    # 冻结表定义只出现在 errors.py。
    assert "AGENT_TOOL_ERROR_CODES" in errors_src
    for code in AGENT_TOOL_ERROR_CODES:
        assert code in errors_src
