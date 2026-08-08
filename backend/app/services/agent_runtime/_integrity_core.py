"""Structured Output Integrity 共享基座（拆分后的叶模块）。

从 ``structured_output_integrity`` 门面拆出：本模块持有被全部 evaluator 复用的
共享基座 —— ``IntegrityDecision`` 决策类型、canonical hash 工具、trail 剥离 /
首个校验错误助手、公共 blocked 常量与共享 lineage/status/trail/protected 门
``_check_common_lineage``。本模块是拆分后的唯一叶模块：叙事/视觉/插画-衍生三
个域模块只依赖本模块，本模块不依赖任何其它 ``_integrity_*`` 模块（零环）。

拆分纪律与原文件一致（fail-closed gate，见门面模块 docstring）：
  - 只有通过严格 wire schema、run lineage、完整性 trail 重放与 leaf-evidence
    校验的 repaired payload 才能成为官方 Artifact；
  - normalization 绝不授予 authority：受保护字段缺失或非法 → blocked，
    不补默认值。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.models.agent_runtime import SkillRun

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

# 稳定 blocked 原因（可审计）——所有信封共享的基座常量。
BLOCKED_SCHEMA = "integrity: envelope failed strict wire schema validation"
BLOCKED_LINEAGE_OWNER = "integrity: lineage mismatch — owner_id must match the run"
BLOCKED_LINEAGE_NOVEL = "integrity: lineage mismatch — novel_id must match the run"
BLOCKED_LINEAGE_SKILL = (
    "integrity: lineage mismatch — skill_version_id must match the run"
)
BLOCKED_LINEAGE_INPUT_HASH = (
    "integrity: lineage mismatch — input_hash must match the run"
)
BLOCKED_STATUS = "integrity: artifact status must be candidate at finalize"
BLOCKED_STALE_REPAIRED_HASH = (
    "integrity: repaired_hash replay mismatch — payload changed after normalization"
)
BLOCKED_TRAIL_INCONSISTENT = "integrity: normalization trail inconsistent — no actions but raw_hash != repaired_hash"
BLOCKED_PROTECTED_SYNTHESIS = "integrity: protected-field synthesis blocked"
BLOCKED_NO_EVIDENCE = "integrity: cited answer without evidence_refs is not eligible (heuristic candidate)"
BLOCKED_UNKNOWN_TYPE = "integrity: unknown artifact type (fail closed)"


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


def _check_common_lineage(
    *, envelope: dict[str, Any], run: SkillRun, wire: Any
) -> IntegrityDecision | None:
    """共享 lineage/status/trail/protected 门（cited_answer 等复用）。

    返回 None 表示通过；返回 IntegrityDecision 表示已阻断。
    """
    if wire.owner_id != run.owner_id:
        return IntegrityDecision(False, BLOCKED_LINEAGE_OWNER)
    if wire.novel_id != run.novel_id:
        return IntegrityDecision(False, BLOCKED_LINEAGE_NOVEL)
    if wire.skill_version_id != run.skill_version_id:
        return IntegrityDecision(False, BLOCKED_LINEAGE_SKILL)
    if wire.input_hash != run.input_hash:
        return IntegrityDecision(False, BLOCKED_LINEAGE_INPUT_HASH)
    if wire.status != "candidate":
        return IntegrityDecision(False, f"{BLOCKED_STATUS} (got {wire.status!r})")

    # 完整性 trail 重放：repaired_hash 必须等于剥离 trail 后的 payload 的 hash。
    recomputed = canonical_content_hash(_strip_trail(envelope))
    if recomputed != wire.normalization.repaired_hash:
        return IntegrityDecision(False, BLOCKED_STALE_REPAIRED_HASH)
    if not wire.normalization.normalization_actions and (
        wire.normalization.raw_hash != wire.normalization.repaired_hash
    ):
        return IntegrityDecision(False, BLOCKED_TRAIL_INCONSISTENT)

    # 受保护字段合成检查（authority/cutoff/fork/approval 绝不出现）。
    forbidden = [key for key in FORBIDDEN_PROTECTED_KEYS if key in envelope]
    if forbidden:
        return IntegrityDecision(
            False, f"{BLOCKED_PROTECTED_SYNTHESIS}: {sorted(forbidden)}"
        )
    return None
