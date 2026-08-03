"""
智能体工具门面契约测试（25.2-02 Domain Tool Contract）。

覆盖：
  - 冻结错误码表完整性（唯一事实源 errors.py，任何子类 code 必须 ∈ 表）
  - 每工具类型化 Schema 校验（StrictPydantic，未知字段 / 非法值 → invalid_input）
  - 每工具 × 领域错误的稳定错误码映射（stub 服务）
  - beyond_cutoff / budget_exceeded / timeout / output_too_large 四个关键码
  - get_narrative_memory 候选标注（release_status="candidate"，ADR-0002）
  - full_book 只从持久化开关读取
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.agent_tools import (
    GetChapterRequest,
    GetNarrativeMemoryRequest,
    GetTimelineRequest,
    SearchNovelTextRequest,
)
from app.services.agent_tools.errors import (
    AGENT_TOOL_ERROR_CODES,
    AgentToolError,
    BeyondCutoffError,
    BudgetExceededError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    OutputTooLargeError,
    ToolTimeoutError,
    UpstreamError,
)
from app.services.agent_tools.facade import TOOL_NAMES, ToolFacade

pytestmark = pytest.mark.contract

# 全部冻结错误码对应的异常类（errors.py 为唯一事实源）。
ERROR_CLASSES = (
    ForbiddenError,
    NotFoundError,
    BeyondCutoffError,
    BudgetExceededError,
    ToolTimeoutError,
    OutputTooLargeError,
    InvalidInputError,
    UpstreamError,
)

_PARAMS_BY_TOOL = {
    "get_novel": {},
    "get_chapter": {"chapter_id": 1},
    "search_novel_text": {"query": "测试"},
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
}


def _novel(reading_progress: dict | None = None) -> "object":
    from app.models.novel import Novel

    return Novel(
        id=1,
        owner_id=1,
        title="契约测试小说",
        chapter_count=10,
        reading_progress=reading_progress or {},
    )


async def _fake_cutoff(db, novel) -> int:
    return 3


def _facade(**kwargs) -> ToolFacade:
    return ToolFacade(cutoff_resolver=_fake_cutoff, **kwargs)


# ────────────────────────── 错误码表完整性 ──────────────────────────


def test_error_code_table_is_frozen_and_complete():
    """冻结表必须精确包含 8 个契约错误码。"""
    assert AGENT_TOOL_ERROR_CODES == (
        "forbidden",
        "not_found",
        "beyond_cutoff",
        "budget_exceeded",
        "timeout",
        "output_too_large",
        "invalid_input",
        "upstream_error",
    )


def test_every_error_class_code_is_in_frozen_table():
    """每个 AgentToolError 子类的 code 必须 ∈ 冻结表（单一事实源）。"""
    for cls in ERROR_CLASSES:
        assert cls.code in AGENT_TOOL_ERROR_CODES
        assert isinstance(cls(  # noqa: E1120 - 仅验证可实例化
            "msg"
        ), AgentToolError)


def test_tool_names_are_exactly_the_12_contract_tools():
    assert set(TOOL_NAMES) == {
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
    }


# ────────────────────────── 每工具 Schema 校验 ──────────────────────────


def test_search_request_requires_query_and_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        SearchNovelTextRequest.model_validate({})
    with pytest.raises(ValidationError):
        SearchNovelTextRequest.model_validate(
            {"query": "q", "secret_param": "injected"}
        )


def test_chapter_request_requires_positive_chapter_id():
    with pytest.raises(ValidationError):
        GetChapterRequest.model_validate({"chapter_id": 0})
    assert GetChapterRequest.model_validate({"chapter_id": 5}).chapter_id == 5


def test_timeline_request_rejects_bare_full_book_param():
    """full_book 裸参数必须被 Schema 拒绝（防剧透；只读持久化开关）。"""
    with pytest.raises(ValidationError):
        GetTimelineRequest.model_validate({"full_book": True})


def test_narrative_memory_request_validates_view():
    with pytest.raises(ValidationError):
        GetNarrativeMemoryRequest.model_validate({"view": "promote"})
    assert (
        GetNarrativeMemoryRequest.model_validate({"view": "tree"}).view == "tree"
    )


# ────────────────────────── 每工具 × 领域错误 → 冻结码 ──────────────────────────


@pytest.mark.parametrize("tool_name", TOOL_NAMES)
@pytest.mark.parametrize("error_cls", ERROR_CLASSES)
async def test_facade_maps_domain_errors_to_frozen_codes(tool_name, error_cls):
    """每个工具 × 每个领域错误都必须映射到冻结错误码表。"""

    async def boom(*args, **kwargs):
        raise error_cls("boom")

    facade = _facade(service_overrides={tool_name: boom})
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            tool_name,
            db=object(),
            novel=_novel(),
            owner_id=1,
            params=_PARAMS_BY_TOOL[tool_name],
        )
    assert excinfo.value.code == error_cls.code
    assert excinfo.value.code in AGENT_TOOL_ERROR_CODES


async def test_facade_unknown_tool_maps_to_invalid_input():
    facade = _facade()
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            "delete_novel", db=object(), novel=_novel(), owner_id=1, params={}
        )
    assert excinfo.value.code == "invalid_input"


# ────────────────────────── 关键 fail-closed 码（stub 服务） ──────────────────────────


async def test_beyond_cutoff_returns_stable_code_and_no_content_leak():
    """get_chapter 请求截止点后的章节：beyond_cutoff + 响应体零正文。"""

    async def fake_get_chapter(db, chapter_id):
        from app.models.novel import Chapter

        return Chapter(
            id=chapter_id,
            novel_id=1,
            chapter_number=9,  # 超过 fake_cutoff=3
            title="受保护章节",
            content="这段受保护内容绝不允许出现在响应中",
            word_count=10,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    facade = _facade(service_overrides={"get_chapter": fake_get_chapter})
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            "get_chapter",
            db=object(),
            novel=_novel(),
            owner_id=1,
            params={"chapter_id": 9},
        )
    assert excinfo.value.code == "beyond_cutoff"
    assert "受保护内容" not in str(excinfo.value)  # 错误体不携带正文


async def test_budget_hook_fail_closed_before_service_call():
    """超预算：budget_exceeded 且底层服务【从未】被调用（fail closed）。"""
    calls: list[str] = []

    async def budget_hook(tool_name, params):
        raise BudgetExceededError("per-run 预算已用尽")

    async def fake_service(*args, **kwargs):
        calls.append("called")

    facade = ToolFacade(
        budget_hook=budget_hook,
        service_overrides={"get_novel": fake_service},
    )
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            "get_novel", db=object(), novel=_novel(), owner_id=1, params={}
        )
    assert excinfo.value.code == "budget_exceeded"
    assert calls == [], "budget hook 必须在校验/执行服务之前拦截"


async def test_timeout_returns_stable_code():
    """慢上游：超过 per-tool 超时 → timeout。"""

    async def slow_service(*args, **kwargs):
        await asyncio.sleep(0.5)

    facade = ToolFacade(
        timeout=0.05, service_overrides={"get_novel": slow_service}
    )
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            "get_novel", db=object(), novel=_novel(), owner_id=1, params={}
        )
    assert excinfo.value.code == "timeout"


async def test_output_too_large_returns_stable_code():
    """超大的 stub 响应：超过 64 KiB → output_too_large。"""

    async def huge_service(*args, **kwargs):
        from app.models.novel import Chapter

        return Chapter(
            id=1,
            novel_id=1,
            chapter_number=1,
            title="巨章",
            content="甲" * 70000,
            word_count=70000,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    facade = _facade(
        byte_cap=64 * 1024, service_overrides={"get_chapter": huge_service}
    )
    with pytest.raises(AgentToolError) as excinfo:
        await facade.execute(
            "get_chapter",
            db=object(),
            novel=_novel(),
            owner_id=1,
            params={"chapter_id": 1},
        )
    assert excinfo.value.code == "output_too_large"


# ────────────────────────── NM 候选标注（ADR-0002） ──────────────────────────


async def test_narrative_memory_envelope_is_candidate_labeled():
    """get_narrative_memory 响应必须带 release_status="candidate"。"""

    async def fake_nm(db, *, owner_id, novel_id, version_id, view, through_chapter):
        return {"versions": []}

    facade = _facade(
        service_overrides={"get_narrative_memory": fake_nm}
    )
    payload = await facade.execute(
        "get_narrative_memory",
        db=object(),
        novel=_novel(),
        owner_id=1,
        params={},
    )
    assert payload["release_status"] == "candidate"
    assert payload["publication_status"] == "candidate_preview"
    assert payload["data"] == {"versions": []}


# ────────────────────────── full_book 只读持久化开关 ──────────────────────────


async def test_full_book_only_from_persisted_switch():
    """full_book 授权只来自持久化开关，绝不来自请求参数。"""
    received: list[bool] = []

    async def record_timeline(db, *, novel, owner_id, source, ordering, person,
                              include_causal, request_full_book,
                              chapter_start, chapter_end):
        received.append(request_full_book)
        return None

    # 开关关闭（默认）→ request_full_book 必须为 False。
    facade = _facade(service_overrides={"get_timeline": record_timeline})
    await facade.execute(
        "get_timeline",
        db=object(),
        novel=_novel(reading_progress={"chapter_id": 1}),
        owner_id=1,
        params={},
    )
    assert received == [False, False]

    # 开关打开（reading_progress.timeline_full_book=True）→ True。
    facade = _facade(service_overrides={"get_timeline": record_timeline})
    await facade.execute(
        "get_timeline",
        db=object(),
        novel=_novel(reading_progress={"timeline_full_book": True}),
        owner_id=1,
        params={},
    )
    assert received == [False, False, True, True]
