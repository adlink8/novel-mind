"""Structured Output Integrity 服务端严格适配器（26-06 / REQ-AGENT-08 / D-16）。

唯一 Artifact finalizer（`finalize.py`）在 `create_artifact_with_first_revision`
**之前**调用本模块的 fail-closed gate。规则:
  - 只有通过严格 wire schema、run lineage、完整性 trail 重放与 leaf-evidence
    校验的 repaired payload 才能成为官方 Artifact。
  - normalization 绝不授予 authority：受保护字段（owner/cutoff/authority/branch/
    fork/approval）缺失或非法 → blocked，**不补默认值**；repaired_hash 漂移
    （payload 在规范化后被改动）→ blocked。
  - blocked/invalid → 0 Artifact、0 Revision；不触发 ApprovalRequest / Publisher /
    promotion / active-pointer 写入。
  - evidence_refs 的冻结 manifest 白名单校验仍由
    `reader_chat.validate_answer_against_manifest`（finalize 内）承担；本模块做
    schema、lineage 与 trail 的权威门，并复用现有 `CitedAnswerArtifact` /
    `ExternalEvidenceArtifact` wire 模型（不新增第二套事实或 citation authority）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.models.agent_runtime import SkillRun
from app.schemas.agent_runtime import (
    CitedAnswerArtifact,
    ExternalEvidenceArtifact,
)

# 受保护字段：normalizer 绝不合成，服务端也禁止出现在信封中（extra=forbid 兜底，
# 这里给出明确的 blocked 原因）。owner/branch/evidence_refs 是 lineage 提供的
# 合法字段，由 schema + lineage 校验单独检查。
FORBIDDEN_PROTECTED_KEYS: tuple[str, ...] = (
    "authority",
    "cutoff",
    "fork",
    "approval",
    "approval_state",
)

# 稳定 blocked 原因（可审计）。
BLOCKED_SCHEMA = "integrity: envelope failed strict wire schema validation"
BLOCKED_LINEAGE_OWNER = "integrity: lineage mismatch — owner_id must match the run"
BLOCKED_LINEAGE_NOVEL = "integrity: lineage mismatch — novel_id must match the run"
BLOCKED_LINEAGE_SKILL = "integrity: lineage mismatch — skill_version_id must match the run"
BLOCKED_LINEAGE_INPUT_HASH = "integrity: lineage mismatch — input_hash must match the run"
BLOCKED_STATUS = "integrity: artifact status must be candidate at finalize"
BLOCKED_STALE_REPAIRED_HASH = (
    "integrity: repaired_hash replay mismatch — payload changed after normalization"
)
BLOCKED_TRAIL_INCONSISTENT = (
    "integrity: normalization trail inconsistent — no actions but raw_hash != repaired_hash"
)
BLOCKED_PROTECTED_SYNTHESIS = "integrity: protected-field synthesis blocked"
BLOCKED_NO_EVIDENCE = (
    "integrity: cited answer without evidence_refs is not eligible (heuristic candidate)"
)
BLOCKED_UNKNOWN_TYPE = "integrity: unknown artifact type (fail closed)"
BLOCKED_EXTERNAL_CANON = "integrity: external evidence must be prohibited_from_canon=true"


@dataclass(frozen=True)
class IntegrityDecision:
    """integrity gate 决策：ok=False 表示 blocked（零写入）。"""

    ok: bool
    blocked_reason: str | None = None


def canonical_content_hash(content: dict[str, Any]) -> str:
    """对 payload 做 canonical 序列化并求 SHA-256（与 artifacts.content_hash_of 口径一致）。

    注意：repaired_hash 的口径与 agent-service normalizer 的 canonicalHash 相同——
    sort_keys + 紧凑分隔符 + 非 ASCII 保持 UTF-8 原样。
    """
    canonical = json.dumps(
        content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    """剥离 normalization trail：repaired_hash 是对不含 trail 的 payload 计算的。"""
    return {key: value for key, value in envelope.items() if key != "normalization"}


def _first_validation_error(exc: ValidationError) -> str:
    err = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(part) for part in err.get("loc", ()))
    msg = err.get("msg") or err.get("type") or "validation error"
    return f"{loc}: {msg}"


def evaluate_integrity(*, envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    """唯一 finalizer 在任何写入前调用的 fail-closed integrity gate。

    :param envelope: agent-service 提交的（已规范化）信封 payload。
    :param run:     当前 SkillRun 行（owner/novel/skill_version/input_hash 权威）。
    """
    artifact_type = envelope.get("type")
    if artifact_type == "cited_answer":
        return _evaluate_cited_answer(envelope, run)
    if artifact_type == "external_evidence":
        return _evaluate_external_evidence(envelope)
    return IntegrityDecision(False, BLOCKED_UNKNOWN_TYPE)


def _evaluate_cited_answer(envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 cited-answer 网关
    #    （schema 的 min_length=1 是兜底；这里给出明确的稳定 blocked 原因）。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema（extra=forbid + 必需字段 + hash 格式 + trail 形状）。
    try:
        model = CitedAnswerArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")

    # 2. run lineage：owner/novel/skill_version/input_hash 必须与 run 一致。
    if model.owner_id != run.owner_id:
        return IntegrityDecision(False, BLOCKED_LINEAGE_OWNER)
    if model.novel_id != run.novel_id:
        return IntegrityDecision(False, BLOCKED_LINEAGE_NOVEL)
    if model.skill_version_id != run.skill_version_id:
        return IntegrityDecision(False, BLOCKED_LINEAGE_SKILL)
    if model.input_hash != run.input_hash:
        return IntegrityDecision(False, BLOCKED_LINEAGE_INPUT_HASH)
    if model.status != "candidate":
        return IntegrityDecision(False, f"{BLOCKED_STATUS} (got {model.status!r})")

    # 3. 完整性 trail 重放：repaired_hash 必须等于剥离 trail 后的 payload 的 hash。
    recomputed = canonical_content_hash(_strip_trail(envelope))
    if recomputed != model.normalization.repaired_hash:
        return IntegrityDecision(False, BLOCKED_STALE_REPAIRED_HASH)
    if not model.normalization.normalization_actions and (
        model.normalization.raw_hash != model.normalization.repaired_hash
    ):
        return IntegrityDecision(False, BLOCKED_TRAIL_INCONSISTENT)

    # 4. 受保护字段合成检查（authority/cutoff/fork/approval 绝不出现；
    #    extra=forbid 已兜底，此处为纵深防御 + 明确原因）。
    forbidden = [key for key in FORBIDDEN_PROTECTED_KEYS if key in envelope]
    if forbidden:
        return IntegrityDecision(False, f"{BLOCKED_PROTECTED_SYNTHESIS}: {sorted(forbidden)}")

    # 5. leaf-evidence 白名单校验由 finalize 的 validate_answer_against_manifest 承担。
    return IntegrityDecision(True)


def _evaluate_external_evidence(envelope: dict[str, Any]) -> IntegrityDecision:
    # D-09：prohibited_from_canon 是服务端 Literal[True] 常量，wire 形状无法断言
    # 其它值（T-25.3-03-02，Pitfall 6）；显式检查给出明确原因，schema 兜底。
    if envelope.get("prohibited_from_canon") is not True:
        return IntegrityDecision(False, BLOCKED_EXTERNAL_CANON)
    try:
        ExternalEvidenceArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")
    return IntegrityDecision(True)
