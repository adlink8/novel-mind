"""确定性运行 finalizer（25.2-03 / D-01/D-11 / T-25.2-03-02/03/05）。

权威规则:
  - 产物 + 首个修订**只**在 agent_end 且 stop_reason == "stop" 时写入；
    agent loop 本身没有任何 artifact 写路径（会话永远不是事实源）。
  - cancelled run → 0 artifact 行 + 0 revision 行 + run status=cancelled
    （cancel-without-write，仿 reader_chat worker 取消检查点）。
  - 每个 evidence_ref 用 validate_answer_against_manifest 对 run 的冻结
    manifest 白名单校验；未知 ref → run failed（error_code=failed_validation）、
    什么都不写。
  - 预算从技能版本 budget 策略 fail-closed（复用 reader_chat BudgetPolicy 语义）；
    超限 → run failed（error_code=budget_exceeded）、什么都不写。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_runtime import SkillRun, SkillVersion
from app.schemas.reader_chat import (
    ReaderAnswerEnvelope,
    validate_answer_against_manifest,
)
from app.services.agent_runtime import artifacts as artifact_service
from app.services.agent_runtime.registry import canonical_input_hash
from app.services.agent_runtime.structured_output_integrity import evaluate_integrity
from app.services.reader_chat.budget import BudgetPolicy

# agent_end 的取消 stop reason 集合 → cancelled 分支（0 写）。
CANCEL_STOP_REASONS: frozenset[str] = frozenset({"aborted", "cancel", "cancelled"})
# 冻结错误码（run 级）：稳定错误码，agent-service 侧镜像。
ERROR_CODE_FAILED_VALIDATION = "failed_validation"
ERROR_CODE_BUDGET_EXCEEDED = "budget_exceeded"
ERROR_CODE_INVALID_STOP_REASON = "invalid_stop_reason"
ERROR_CODE_UPSTREAM_ERROR = "upstream_error"
ERROR_CODE_UNKNOWN = "failed"


class RunFinalizeError(RuntimeError):
    """finalize 输入不合法（调用方缺陷），与 run 失败（记录在 run 上）区分。"""


@dataclass(frozen=True)
class FinalizeOutcome:
    """finalize 结果：run 终态 + 写出的 artifact/revision（若写了）。"""

    status: str
    error_code: str | None = None
    status_reason: str | None = None
    artifact_id: int | None = None
    artifact_revision_id: int | None = None


def skill_budget_policy(budget: dict[str, Any]) -> BudgetPolicy:
    """把 skill.yaml 的 budget 声明解析为 BudgetPolicy（缺字段 → 0，fail closed）。"""
    return BudgetPolicy(
        max_calls=int(budget.get("max_calls", 0)),
        max_input_tokens=int(budget.get("max_input_tokens", 0)),
        max_output_tokens=int(budget.get("max_output_tokens", 0)),
        max_cost_usd=Decimal(str(budget.get("max_cost_usd", 0))),
    )


def check_run_budget(policy: BudgetPolicy, usage: dict[str, Any]) -> None:
    """按 per-run call/token 上限校验实际用量；超限抛 ValueError（fail closed）。"""
    calls = int(usage.get("calls", 0))
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    cost = Decimal(str(usage.get("cost_usd", 0)))
    if calls > policy.max_calls:
        raise ValueError(
            f"budget exceeded: calls {calls} > max_calls {policy.max_calls}"
        )
    if input_tokens > policy.max_input_tokens:
        raise ValueError(
            f"budget exceeded: input_tokens {input_tokens} > "
            f"max_input_tokens {policy.max_input_tokens}"
        )
    if output_tokens > policy.max_output_tokens:
        raise ValueError(
            f"budget exceeded: output_tokens {output_tokens} > "
            f"max_output_tokens {policy.max_output_tokens}"
        )
    if cost > policy.max_cost_usd:
        raise ValueError(
            f"budget exceeded: cost_usd {cost} > max_cost_usd {policy.max_cost_usd}"
        )


async def finalize_skill_run(
    sessions: async_sessionmaker[AsyncSession],
    *,
    run_id: int,
    stop_reason: str,
    envelope: dict[str, Any],
    model_lineage: dict[str, Any],
    source_versions: dict[str, Any],
    usage: dict[str, Any],
    frozen_manifest: dict[str, Any] | None = None,
) -> FinalizeOutcome:
    """确定性 finalizer：唯一写 artifact/revision 的入口（其余分支 0 写）。

    frozen_manifest 由 agent 循环在 tool calls 后物化并冻结；finalize 把它
    落库到 run 上，再对白名单校验 citations（T-25.2-03-02）。
    """
    async with sessions.begin() as session:
        run = await session.get(SkillRun, run_id, with_for_update=True)
        if run is None:
            raise RunFinalizeError(f"skill run {run_id} does not exist")
        if run.status == "completed":
            # 幂等：已完成运行不重复写。
            return FinalizeOutcome(status="completed")

        # 取消分支：0 artifact + 0 revision + cancelled。
        if run.cancel_requested or stop_reason in CANCEL_STOP_REASONS:
            run.status = "cancelled"
            run.stop_reason = stop_reason
            run.status_reason = "cancel_without_write"
            run.error_code = "user_cancel"
            return FinalizeOutcome(
                status="cancelled",
                error_code="user_cancel",
                status_reason="cancel_without_write",
            )

        # 只有 stop 才会写；其它 stop reason 视为运行异常。
        if stop_reason != "stop":
            run.status = "failed"
            run.stop_reason = stop_reason
            run.status_reason = f"unexpected stop reason {stop_reason!r}"
            # 已知上游停止语义（provider error / 超长截断 / 其它）→ upstream_error；
            # 只有真正协议外的值才映射 invalid_stop_reason。
            if stop_reason in {"error", "max_tokens", "other"}:
                run.error_code = ERROR_CODE_UPSTREAM_ERROR
            else:
                run.error_code = ERROR_CODE_INVALID_STOP_REASON
            return FinalizeOutcome(
                status="failed",
                error_code=run.error_code,
                status_reason=run.status_reason,
            )

        # 冻结 manifest：首次落库；已存在且不一致 → 拒绝（防重放漂移）。
        if frozen_manifest is not None:
            if run.frozen_manifest and run.frozen_manifest != frozen_manifest:
                run.status = "failed"
                run.status_reason = "frozen manifest changed after freeze"
                run.error_code = ERROR_CODE_FAILED_VALIDATION
                return FinalizeOutcome(
                    status="failed",
                    error_code=ERROR_CODE_FAILED_VALIDATION,
                    status_reason=run.status_reason,
                )
            run.frozen_manifest = frozen_manifest

        # 预算 fail-closed：从技能版本 budget 策略校验实际用量。
        skill_version = await session.get(SkillVersion, run.skill_version_id)
        if skill_version is None:
            raise RunFinalizeError("skill version disappeared")
        policy = skill_budget_policy(dict(skill_version.budget or {}))
        try:
            check_run_budget(policy, usage)
        except ValueError as exc:
            run.status = "failed"
            run.stop_reason = stop_reason
            run.status_reason = str(exc)[:160]
            run.error_code = ERROR_CODE_BUDGET_EXCEEDED
            return FinalizeOutcome(
                status="failed",
                error_code=ERROR_CODE_BUDGET_EXCEEDED,
                status_reason=run.status_reason,
            )

        # Structured Output Integrity 门禁（26-06 / REQ-AGENT-08 / D-16）：
        # 唯一 finalizer 在任何写入之前执行严格 schema/lineage/trail 校验；
        # blocked → run failed、零写入（不补默认值、不触发 approval/promotion）。
        decision = evaluate_integrity(envelope=envelope, run=run)
        if not decision.ok:
            reason = decision.blocked_reason or "structured output integrity blocked"
            run.status = "failed"
            run.stop_reason = stop_reason
            run.status_reason = reason[:160]
            run.error_code = ERROR_CODE_FAILED_VALIDATION
            return FinalizeOutcome(
                status="failed",
                error_code=ERROR_CODE_FAILED_VALIDATION,
                status_reason=run.status_reason,
            )

        # 引证合法性：每个 evidence_ref 必须属于冻结 manifest 白名单。
        allowed = _frozen_allowlist(run)
        try:
            _validate_artifact_evidence(envelope, allowed)
        except ValueError as exc:
            run.status = "failed"
            run.stop_reason = stop_reason
            run.status_reason = str(exc)[:160]
            run.error_code = ERROR_CODE_FAILED_VALIDATION
            return FinalizeOutcome(
                status="failed",
                error_code=ERROR_CODE_FAILED_VALIDATION,
                status_reason=run.status_reason,
            )

        # 唯一写路径：candidate 产物 + 首个不可变修订。
        artifact, revision = await artifact_service.create_artifact_with_first_revision(
            session,
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            run_id=run.id,
            skill_version_id=run.skill_version_id,
            branch=run.branch,
            artifact_type=str(envelope.get("type") or "cited_answer"),
            schema_version=str(envelope.get("schema_version") or "cited-answer.v1"),
            model_lineage=model_lineage,
            source_versions=source_versions,
            input_hash=run.input_hash,
            content=envelope,
            evidence_refs=list(envelope.get("evidence_refs") or []),
        )
        run.status = "completed"
        run.stop_reason = stop_reason
        run.status_reason = "artifact_published"
        run.error_code = None
        run.model_lineage = model_lineage
        run.source_versions = source_versions
        run.cost_usd = Decimal(str(usage.get("cost_usd", 0)))
        return FinalizeOutcome(
            status="completed",
            status_reason="artifact_published",
            artifact_id=artifact.id,
            artifact_revision_id=revision.id,
        )


def _frozen_allowlist(run: SkillRun) -> set[str]:
    """从 run 冻结 manifest 提取证据白名单；为空则 fail closed（任何引证都不合法）。"""
    manifest = dict(run.frozen_manifest or {})
    return {str(ref) for ref in manifest.get("evidence_refs") or []}


def _validate_artifact_evidence(envelope: dict[str, Any], allowed: set[str]) -> None:
    """冻结 manifest 白名单校验（T-25.2-03-02 / Phase 27 扩展）。

    - cited_answer：构造 ReaderAnswerEnvelope 校验 answer_blocks 内引用，
      并校验顶层 evidence_refs；
    - world_model_candidate 等其它类型：校验顶层 evidence_refs（claim 级
      证据合法性由确定性 WorldModelGate 在发布时裁决，finalize 不越权）。
    """
    if envelope.get("type") == "cited_answer":
        answer = envelope.get("answer")
        if not isinstance(answer, dict):
            raise ValueError("cited answer missing 'answer' payload")
        reader_envelope = ReaderAnswerEnvelope.model_validate(answer)
        validate_answer_against_manifest(reader_envelope, allowed)
    # 顶层 evidence_refs 同样必须属于白名单。
    for ref in envelope.get("evidence_refs") or []:
        if str(ref) not in allowed:
            raise ValueError(f"unknown evidence ref {ref!r}")


def expected_input_hash(input_data: dict[str, Any]) -> str:
    """复算运行输入的 canonical hash（重放断言用）。"""
    return canonical_input_hash(input_data)
