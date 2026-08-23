"""Derivative-domain default tool services for the agent-tools facade.

Extracted from the agent-tools facade (Phase 35 canon fork proposal, Phase 36
derivative edit proposal, Phase 37 divergence / publish-revision proposal,
Phase 38 derivative visual publish proposal): this module owns the default
service entry for the candidate-only derivative action seams. Every seam
delegates to its deterministic boundary service and only ever creates a
candidate artifact + pending Web ApprovalRequest — none of them publish, and
none write Original Canon / Visual Bible / domain tables.
"""

from __future__ import annotations

from typing import Any

from app.services.agent_tools.errors import InvalidInputError

from ._tool_views import (
    _agent_edit_proposal_view_for_tool,
    _fork_proposal_view_for_tool,
)


async def _default_create_canon_fork(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 35 action 工具默认服务：创建**一个**候选 canon fork（D-35-03）。

    服务端 proposal gate 只接受冻结 fork manifest（server-derived cutoff +
    精确 source snapshot）+ delta 意图；创建候选 CanonFork（status=candidate）+
    pending Web ApprovalRequest（action=create_canon_fork，payload_hash 确定性
    重放，D-11/D-15）。绝不物化 fork——确定性 Fork materializer 在用户 Web 批准后
    原子校验 approval + payload + fork manifest + snapshot 重放 + delta 血缘 +
    owner/novel/branch/fork scope 才把 fork 物化为 approved；Original Canon
    不可变、active pointer 恒 false。
    """
    from app.services.canon_fork.materializer import (
        ForkProposalError,
        create_fork_proposal,
    )

    try:
        result = await create_fork_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=params,
        )
    except ForkProposalError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None
    await db.flush()
    return _fork_proposal_view_for_tool(result)


async def _default_apply_derivative_edit(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 36 action 工具默认服务：创建**一个**候选 derivative edit（D-36-02）。

    服务端 proposal gate 只接受冻结 source snapshot 血缘 + 有效 project/chapter
    scope + base_revision CAS 锚；创建候选 DerivativeEditProposal
    （proposal_status=proposed）+ pending Web ApprovalRequest
    （action=apply_derivative_edit，payload_hash 确定性重放，D-11/D-15）。
    绝不直接应用——确定性 Revision Service（apply_agent_edit）在用户 Web 批准后
    原子校验 approval + payload + 冻结 proposal artifact 血缘 +
    owner/novel/branch/fork scope + 同一 base_revision CAS 才把 approved proposal
    应用为 append-only agent_proposal 修订；Original Canon / user draft
    （autosave）revisions / published 状态绝不被 Agent 触碰。
    """
    from app.services.derivative_editor.revisions import (
        DerivativeEditApplyError,
        create_agent_edit_proposal,
    )

    try:
        result = await create_agent_edit_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            chapter_id=int(params["chapter_id"]),
            content=str(params["content"]),
            base_revision=int(params["base_revision"]),
            proposal_key=str(params["proposal_key"]),
            branch=params.get("branch"),
            fork=params.get("fork"),
            source_snapshot_hash=params.get("source_snapshot_hash"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except DerivativeEditApplyError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None
    await db.flush()
    return _agent_edit_proposal_view_for_tool(result)


async def _default_allow_divergence(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 37 action 工具默认服务：创建**一个**显式 divergence override（D-37-03）。

    委托 `overrides.request_divergence_override`：只为 blocked / ``needs_override``
    候选创建 pending ``DerivativeOverride`` + pending Web ApprovalRequest
    （action=allow_divergence，payload_hash 绑定 exact draft_hash +
    canon_delta_hash，D-11/D-15）。**绝不发布、绝不写 Original Canon**——只有独立
    ``publish_derivative_revision`` approval 被批准后由确定性 revision
    publisher 物化。
    """
    from app.services.derivative_generation.agent_boundary import (
        OverrideError,
        request_divergence_override,
    )

    try:
        return await request_divergence_override(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            chapter_id=int(params["chapter_id"]),
            candidate_id=int(params["candidate_id"]),
            reason=str(params["reason"]),
            affected_evidence=list(params.get("affected_evidence") or []),
            kind=params.get("kind"),
            draft_hash=str(params["draft_hash"]),
            canon_delta_hash=str(params["canon_delta_hash"]),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except OverrideError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_publish_derivative_revision(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 37 action 工具默认服务：创建**一个**独立 publish ApprovalRequest。

    委托 `overrides.request_publish_derivative_revision`：只在 **allow_divergence
    approval 已批准 + 完整 revalidation 通过** 后才为同一候选创建独立 pending
    Web ApprovalRequest（action=publish_derivative_revision），绑定**与
    allow_divergence approval 完全相同**的 draft_hash / canon_delta_hash。
    **绝不发布、绝不写 Original Canon**。
    """
    from app.services.derivative_generation.agent_boundary import (
        OverrideError,
        request_publish_derivative_revision,
    )

    try:
        return await request_publish_derivative_revision(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            override_id=int(params["override_id"]),
            draft_hash=str(params["draft_hash"]),
            canon_delta_hash=str(params["canon_delta_hash"]),
            approval_note=params.get("approval_note"),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except OverrideError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_publish_derivative_visual(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 38 action 工具默认服务：为已存储 candidate 创建**一个** pending
    ApprovalRequest（D-38-03/D-38-04）。

    委托 `derivative_visual.agent_boundary.request_publish_derivative_visual`：
    只接受 owner/novel/fork scope 内可批准（candidate/needs_review）的候选
    （blocked candidate / wrong owner/branch/fork / scene_spec_hash drift →
    fail closed）；创建 pending Web ApprovalRequest
    （action=publish_derivative_visual，payload_hash 绑定候选冻结血缘，
    D-11/D-15）。**绝不发布、绝不写 Original Visual Bible**——只有独立 approval
    被用户批准后由确定性 review seam（review_candidate_asset）物化为 approved
    published asset。
    """
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
        request_publish_derivative_visual,
    )

    try:
        return await request_publish_derivative_visual(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_asset_id=int(params["candidate_asset_id"]),
            scene_spec_hash=str(params["scene_spec_hash"]),
            actor_id=owner_id,
            approval_note=params.get("approval_note"),
            branch=params.get("branch"),
            fork=params.get("fork"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except DerivativeVisualBoundaryError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None
