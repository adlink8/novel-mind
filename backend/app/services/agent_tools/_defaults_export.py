"""Export-domain default tool services for the agent-tools facade.

Extracted from the agent-tools facade (Phase 39 derivative export actions
39-05): this module owns the default service entry for approve_export (creates
a single pending ApprovalRequest bound to an artifact revision +
preparation_hash) and materialize_export (the deterministic materializer that
only accepts an approved artifact whose preparation_hash matches the frozen
approve_export ApprovalRequest). Neither seam writes Original Canon / domain
tables / approval lineage — the materializer only advances the candidate
artifact and produces the reproducible bundle.
"""

from __future__ import annotations

from typing import Any

from app.services.agent_tools.errors import InvalidInputError


async def _default_approve_export(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 39 action 工具默认服务：创建**一个** pending approve_export
    ApprovalRequest（D-39-01/D-39-02）。

    委托 `derivative_export.materializer.request_approve_export`：只接受
    owner/novel/branch/fork/project scope 内已 finalize 的候选
    ExportPreparationArtifact + 确定性 preparation_hash 重放（stale/伪造 hash /
    wrong owner/branch/fork/project → fail closed）；创建 pending Web
    ApprovalRequest（action=approve_export，payload_hash 绑定 artifact
    revision + preparation_hash，D-11/D-15）。**绝不物化、绝不写 Original
    Canon / 域表 / Artifact 状态 / bundle**。
    """
    from app.services.derivative_export.materializer import (
        ExportMaterializationError,
        request_approve_export,
    )

    try:
        return await request_approve_export(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            artifact_id=int(params["artifact_id"]),
            artifact_revision_id=int(params["artifact_revision_id"]),
            preparation_hash=str(params["preparation_hash"]),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
            approval_note=params.get("approval_note"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
        )
    except ExportMaterializationError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_materialize_export(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 39 action 工具默认服务：确定性 materializer（D-39-01/D-39-02）。

    委托 `derivative_export.materializer.materialize_export`：只接受 approved
    artifact + preparation_hash 匹配的 approve_export ApprovalRequest，原子校验
    approval action + 相同 hash 绑定 + artifact revision 血缘 + owner/novel/
    branch/fork/project scope + 冻结 manifest 重放，才把候选 artifact 推进为
    approved 并产出可复现 bundle。**绝不写 Original Canon / 域表 / approval
    lineage**（download 只读）。
    """
    from app.services.derivative_export.materializer import (
        ExportMaterializationError,
        materialize_export,
    )

    try:
        return await materialize_export(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            artifact_id=int(params["artifact_id"]),
            artifact_revision_id=int(params["artifact_revision_id"]),
            approval_id=int(params["approval_id"]),
            preparation_hash=str(params["preparation_hash"]),
            reason=params.get("reason"),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
        )
    except ExportMaterializationError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None
