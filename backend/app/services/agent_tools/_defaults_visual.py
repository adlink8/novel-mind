"""Visual domain default tool services for the agent-tools facade.

Extracted from the agent-tools facade (Phase 30 Visual Bible read tool 31-04,
Phase 33 candidate generation action 33-05, Phase 34 anchor proposal actions
34-05): this module owns the default service entry for the read-only visual
bible tool plus the candidate-only action seams that create a generation job /
anchor proposal. Every action seam is candidate-only by construction — it never
writes Canon / domain tables / published state; the durable worker or the
deterministic publisher owns approved materialization.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.services.agent_tools.errors import InvalidInputError, NotFoundError
from app.services.visual_bible.authority import (
    CandidateNotFoundError,
    list_versions as list_visual_bible_versions,
    load_version_view as load_visual_bible_version_view,
)

from ._tool_views import _anchor_proposal_view_for_tool, _job_view_for_tool


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
    views = await list_visual_bible_versions(db, owner_id=owner_id, novel_id=novel_id)
    if approved_only:
        views = [view for view in views if view.review_state == "approved"]
    return {
        "items": [view.model_dump(mode="json") for view in views],
        "total": len(views),
    }


async def _default_generate_image_candidate(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 33 action 工具默认服务：创建**一个**候选生成作业（D-33-01..D-33-03）。

    只创建 durable idempotent job（queued）——服务端 generation gate 只接受
    **已批准且非 stale** 的 PromptRevision（``check_generation_prompt_gate``），
    作业 idempotency key 从 owner/novel/SceneSpec/prompt/model/config 血缘
    确定性重放。绝不写 Canon / 域表 / ApprovalRequest / published 状态；候选
    资产由 durable worker 在作业成功时产出，审批/发布属于 Phase 34。
    """
    from app.models.illustration_job import (
        ILLUSTRATION_JOB_NONTERMINAL_STATUSES,
        IllustrationJob,
    )
    from app.schemas.illustration import (
        IllustrationJobContract,
        PriceSnapshot,
        build_illustration_idempotency_key,
        validate_illustration_job_contract,
    )
    from app.services.illustrations.gateway import (
        GenerationGateError,
        build_illustration_lineage,
        check_generation_prompt_gate,
    )
    from app.services.illustrations.worker import (
        DEFAULT_MAX_INPUT_TOKENS,
        DEFAULT_MAX_OUTPUT_TOKENS,
        MOCK_ILLUSTRATION_MODEL,
        MOCK_ILLUSTRATION_PROVIDER,
    )

    provider = str(params.get("provider") or MOCK_ILLUSTRATION_PROVIDER)
    model = str(params.get("model") or MOCK_ILLUSTRATION_MODEL)
    if provider != MOCK_ILLUSTRATION_PROVIDER:
        raise InvalidInputError(
            f"illustration provider {provider!r} is not configured; supported: {MOCK_ILLUSTRATION_PROVIDER!r}"
        )
    prompt_revision_id = int(params["prompt_revision_id"])
    try:
        prompt_row = await check_generation_prompt_gate(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            prompt_revision_id=prompt_revision_id,
        )
    except GenerationGateError as exc:
        if exc.reason_code == "prompt_revision_not_found":
            raise NotFoundError(str(exc)) from None
        raise InvalidInputError(str(exc)) from exc

    lineage = build_illustration_lineage(
        prompt_revision=prompt_row,
        provider=provider,
        model=model,
        width=int(params.get("width", 1024)),
        height=int(params.get("height", 1024)),
        max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    idempotency_key = build_illustration_idempotency_key(owner_id, novel_id, lineage)
    price_snapshot = PriceSnapshot(
        provider=provider,
        model=model,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.10"),
        image_price_per_image=Decimal("0.04"),
    )
    job_contract = IllustrationJobContract(
        schema_version="illustration.v1",
        artifact_kind="illustration_job",
        owner_id=owner_id,
        novel_id=novel_id,
        job_key=str(params.get("job_key") or f"agent-{uuid.uuid4().hex[:8]}"),
        lineage=lineage,
        price_snapshot=price_snapshot.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    validate_illustration_job_contract(job_contract)

    existing = await db.scalar(
        select(IllustrationJob).where(
            IllustrationJob.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.status in ILLUSTRATION_JOB_NONTERMINAL_STATUSES
            or existing.status == "succeeded"
        ):
            return _job_view_for_tool(existing)
        raise InvalidInputError(
            "a terminal illustration job with this lineage already exists; retry it explicitly"
        )

    row = IllustrationJob(
        owner_id=owner_id,
        novel_id=novel_id,
        job_key=job_contract.job_key,
        idempotency_key=idempotency_key,
        status="queued",
        status_reason=None,
        error_code=None,
        lease_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        cancel_requested=False,
        retry_count=0,
        scene_spec_hash=lineage.scene_spec_hash,
        prompt_revision_id=lineage.prompt_revision_id,
        prompt_revision_hash=lineage.prompt_revision_hash,
        visual_bible_revision_id=lineage.visual_bible_revision_id,
        visual_bible_revision_hash=lineage.visual_bible_revision_hash,
        source_snapshot_id=lineage.source_snapshot_id,
        source_snapshot_hash=lineage.source_snapshot_hash,
        cutoff_chapter=lineage.cutoff_chapter,
        model_lineage=dict(lineage.model_lineage),
        config_hash=lineage.config_hash,
        price_snapshot=job_contract.price_snapshot,
        response_hash=None,
        schema_version="illustration.v1",
    )
    db.add(row)
    await db.flush()
    return _job_view_for_tool(row)


async def _default_publish_illustration(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 34 action 工具默认服务：创建**一个**候选锚点 proposal（D-34-01）。

    服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision
    （Phase 33 handoff）与精确 source span（excerpt + anchor_hash +
    chapter_content_hash + source snapshot）；创建候选 IllustrationAnchorProposal
    + pending Web ApprovalRequest（action=publish_illustration，payload_hash
    确定性重放，D-11/D-15）。绝不发布——确定性 publisher 在用户 Web 批准后原子
    校验 approval + payload + scope 才创建 valid anchor；Agent/浏览器绝不发布。
    """
    from app.services.illustration_anchors.publish import (
        AnchorProposalError,
        create_anchor_proposal,
    )

    try:
        result = await create_anchor_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=params,
            action="publish_illustration",
        )
    except AnchorProposalError as exc:
        raise InvalidInputError(str(exc)) from None
    await db.flush()
    return _anchor_proposal_view_for_tool(result)


async def _default_attach_illustration_to_text(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 34 action 工具默认服务：把锚点绑定到精确文本跨度（candidate-only）。

    与 publish_illustration 同 gate（proposal-ready asset + 精确 source span），
    但 ApprovalRequest action 为 attach_illustration_to_text。绝不发布。
    """
    from app.services.illustration_anchors.publish import (
        AnchorProposalError,
        create_anchor_proposal,
    )

    try:
        result = await create_anchor_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=params,
            action="attach_illustration_to_text",
        )
    except AnchorProposalError as exc:
        raise InvalidInputError(str(exc)) from None
    await db.flush()
    return _anchor_proposal_view_for_tool(result)
