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
    ChapterAnalysisArtifact,
    CitedAnswerArtifact,
    ExternalEvidenceArtifact,
    SceneCandidateArtifact,
    SkillEvaluationArtifact,
    StoryArcArtifact,
    VisualBibleArtifact,
    WorldModelCandidateArtifact,
)
from app.schemas.key_scene import (
    KeySceneGateError,
    SceneCandidateSetContract,
    validate_candidate_set_contract,
)
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleGateError,
    VisualBibleVersionContract,
    VisualReviewState,
    validate_version_contract,
)
from app.services.narrative_memory.arc_planner import (
    OutlineCandidateArtifact,
)
from app.services.narrative_memory.builder_contracts import (
    ChapterAnalysisArtifact as DomainChapterAnalysisArtifact,
    assert_digests_never_evidence_refs,
    hint_safe_at_cutoff,
)
from app.services.narrative_memory.global_builder import (
    MainlineCandidateArtifact,
)
from app.services.qualification.report import QualificationReport

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
# Phase 28 确定性边界（D-08/D-09）。
BLOCKED_CHAPTER_ANALYSIS_PAYLOAD = "integrity: chapter analysis payload failed domain validation"
BLOCKED_DIGEST_MISUSE = (
    "integrity: chapter digest cannot double as an EvidenceRef or retrieval-index input"
)
BLOCKED_FUTURE_HINT = (
    "integrity: next_context_hint leaks facts beyond cutoff (future-fact hint)"
)
BLOCKED_OUTLINE_CANON = "integrity: outline candidate must remain candidate-only"
BLOCKED_MAINLINE_CANON = "integrity: mainline candidate must remain candidate-only"
# Phase 29 确定性评估边界（D-02/D-05）。
BLOCKED_EVALUATION_REPORT = (
    "integrity: skill evaluation report failed sealed qualification validation"
)
BLOCKED_EVALUATION_REPORT_STALE = (
    "integrity: skill evaluation report checksum replay mismatch — payload changed"
)
BLOCKED_EVALUATION_SOURCE_SNAPSHOT = (
    "integrity: skill evaluation report source snapshot mismatches envelope lineage"
)
# Phase 30 Visual Bible 确定性边界（D-30-01..D-30-04）。
BLOCKED_VISUAL_BIBLE_PAYLOAD = "integrity: visual bible payload failed domain validation"
BLOCKED_VISUAL_BIBLE_APPROVAL_BYPASS = (
    "integrity: visual bible approval bypass blocked — review_state must be candidate"
)
BLOCKED_VISUAL_BIBLE_EVIDENCE_MISMATCH = (
    "integrity: visual bible canon claim evidence keys must be a subset of envelope evidence_refs"
)
# Phase 31 Key Scene 确定性边界（D-31-01..D-31-05）。
BLOCKED_SCENE_CANDIDATE_PAYLOAD = (
    "integrity: scene candidate set payload failed domain validation"
)
BLOCKED_SCENE_CANDIDATE_APPROVAL_BYPASS = (
    "integrity: scene candidate approval bypass blocked — review_state must be candidate"
)
BLOCKED_SCENE_CANDIDATE_EVIDENCE_MISMATCH = (
    "integrity: scene candidate evidence keys must be a subset of envelope evidence_refs"
)


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
    if artifact_type == "world_model_candidate":
        return _evaluate_world_model_candidate(envelope, run)
    if artifact_type == "chapter_analysis":
        return _evaluate_chapter_analysis(envelope, run)
    if artifact_type == "story_arc":
        return _evaluate_story_arc(envelope, run)
    if artifact_type == "skill_evaluation":
        return _evaluate_skill_evaluation(envelope, run)
    if artifact_type == "visual_bible":
        return _evaluate_visual_bible(envelope, run)
    if artifact_type == "scene_candidate":
        return _evaluate_scene_candidate(envelope, run)
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


def _evaluate_world_model_candidate(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 27 世界模型候选信封 integrity gate（D-01..D-06 / D-16）。

    与 cited_answer 纪律一致：无证据的启发式候选不能进世界模型网关；lineage
    （owner/novel/skill_version/input_hash）必须与 run 一致；status 恒为
    candidate；trail 可重放；受保护字段（authority/cutoff/fork/approval）绝不
    出现在信封中。candidates 内部 claim 的合法性由确定性 WorldModelGate 在
    发布时裁决，本 gate 不越权。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进世界模型网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema（extra=forbid + 必需字段 + hash 格式 + trail 形状）。
    try:
        model = WorldModelCandidateArtifact.model_validate(envelope)
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

    # 5. leaf-evidence 白名单校验由 finalize 的 _validate_artifact_evidence 承担。
    return IntegrityDecision(True)


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


def _evaluate_chapter_analysis(envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    """Phase 28 ChapterAnalysisArtifact 信封 integrity gate（D-08/D-16）。

    与 cited_answer 纪律一致（evidence/lineage/status/trail/protected），并在
    ``analysis`` 负载上做确定性 D-08 边界校验：
      - analysis 用领域 ``builder_contracts.ChapterAnalysisArtifact`` 严格校验
        （bounded max_length、chunk digest 唯一、hint/reason 互斥、digest 64-hex）；
      - digests（chapter_digest + chunk_digests）绝不与 EvidenceRef 或检索索引输入
        冲突（assert_digests_never_evidence_refs）；
      - ``next_context_hint`` 只消歧、绝不泄漏未来事实（hint_safe_at_cutoff）。
    任何失败 → 稳定 blocked，零写入。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进章节分析网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema（extra=forbid + 必需字段 + hash 格式 + trail 形状）。
    try:
        model = ChapterAnalysisArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. analysis 负载：领域严格校验（D-08 bounded context/continuity）。
    analysis_payload = envelope.get("analysis")
    if not isinstance(analysis_payload, dict):
        return IntegrityDecision(False, BLOCKED_CHAPTER_ANALYSIS_PAYLOAD)
    try:
        analysis = DomainChapterAnalysisArtifact.model_validate(analysis_payload)
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_CHAPTER_ANALYSIS_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. digests 是压缩负载：绝不作为 EvidenceRef / 检索索引输入（D-08）。
    digests = [analysis.chapter_digest, *analysis.chunk_digests]
    try:
        assert_digests_never_evidence_refs(
            digests,
            authority_content_hashes=list(envelope["evidence_refs"]),
            retrieval_index_inputs=list(envelope["evidence_refs"]),
        )
    except ValueError as exc:
        return IntegrityDecision(False, f"{BLOCKED_DIGEST_MISUSE}: {exc}")

    # 5. next hint 只消歧、绝不泄漏未来事实（D-08）。
    if analysis.next_context_hint and not hint_safe_at_cutoff(
        analysis.next_context_hint, cutoff=analysis.cutoff
    ):
        return IntegrityDecision(False, BLOCKED_FUTURE_HINT)

    return IntegrityDecision(True)


def _evaluate_story_arc(envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    """Phase 28 StoryArcArtifact 信封 integrity gate（D-05/D-07/D-09/D-16）。

    Outline/Mainline 候选只以 candidate-only 进入：领域
    ``OutlineCandidateArtifact`` / ``MainlineCandidateArtifact`` 强制
    ``candidate_status == "candidate"``（Canon 提升尝试 → schema 校验失败）。
    lineage/evidence/status/trail/protected 与其余信封纪律一致；任何失败 →
    稳定 blocked，零写入。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进故事弧网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = StoryArcArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. Outline/Mainline 候选：领域严格校验（candidate-only，绝不进入 Canon）。
    outline_payload = envelope.get("outline_candidate")
    mainline_payload = envelope.get("mainline_candidate")
    if not isinstance(outline_payload, dict) or not isinstance(mainline_payload, dict):
        return IntegrityDecision(False, f"{BLOCKED_OUTLINE_CANON}: missing candidate")
    try:
        OutlineCandidateArtifact.model_validate(outline_payload)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_OUTLINE_CANON} ({_first_validation_error(exc)})"
        )
    try:
        MainlineCandidateArtifact.model_validate(mainline_payload)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_MAINLINE_CANON} ({_first_validation_error(exc)})"
        )

    return IntegrityDecision(True)


def _evaluate_skill_evaluation(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 29 SkillEvaluationArtifact 信封 integrity gate（D-02/D-05/D-16）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``report`` 上做确定性评估边界校验：
      - report 必须是密封的 ``QualificationReport``（verdict 只允许
        qualified_candidate / blocked，无 promotion 词）；
      - report checksum 必须可重放（后端确定性评估 runner 产出，不可由
        Agent/UI 更改）；
      - report header 的 source snapshot / dataset version 必须与信封
        ``source_versions`` 血缘绑定（可选但若提供则必须一致）。
    任何失败 → 稳定 blocked，零写入；无 ApprovalRequest / Publisher / promotion。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进评估网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = SkillEvaluationArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")

    # 2. 共享 lineage/status/trail/protected 门（evaluated_run/evaluated_artifact
    #    只允许冻结终态，由 wire schema 的 enum/shape 强制）。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. 密封 QualificationReport：two-value verdict + checksum 重放（D-05）。
    report_payload = envelope.get("report")
    if not isinstance(report_payload, dict):
        return IntegrityDecision(False, BLOCKED_EVALUATION_REPORT)
    try:
        report = QualificationReport.model_validate(report_payload)
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_EVALUATION_REPORT} ({_first_validation_error(exc)})",
        )
    if not report.checksum_valid:
        return IntegrityDecision(False, BLOCKED_EVALUATION_REPORT_STALE)

    # 4. dataset/source 血缘绑定：report header 与信封 source_versions 一致
    #    （可选；若提供则必须匹配，D-02 纵深防御）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != report.header.source_snapshot:
        return IntegrityDecision(False, BLOCKED_EVALUATION_SOURCE_SNAPSHOT)
    dataset_version = source_versions.get("dataset_version")
    if (
        dataset_version is not None
        and dataset_version != report.header.dataset_version
    ):
        return IntegrityDecision(False, BLOCKED_EVALUATION_SOURCE_SNAPSHOT)

    return IntegrityDecision(True)


def _evaluate_visual_bible(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 30 VisualBibleArtifact 信封 integrity gate（D-30-01..D-30-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``visual_bible`` 负载上做确定性域边界校验：
      - ``visual_bible`` 必须是严格 ``VisualBibleVersionContract`` 且通过
        ``validate_version_contract``（claim hash / manifest hash / evidence
        refs 结构 / 唯一 stable ID / spoiler cutoff 全部服务端重算）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass）→ blocked（approval 是服务端显式、
        append-only 的 ``visual_bible:approve`` 迁移，D-30-04）；
      - 每个 canon_fact claim 的 evidence_key 必须 ⊆ 信封顶层
        ``evidence_refs``（leaf-evidence 资格门，D-30-02）。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 validators 保留
    permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 Visual Bible 网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = VisualBibleArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. visual_bible 负载：严格域契约 + approval bypass 门。
    vb_payload = envelope.get("visual_bible")
    if not isinstance(vb_payload, dict):
        return IntegrityDecision(False, BLOCKED_VISUAL_BIBLE_PAYLOAD)
    if vb_payload.get("review_state") != VisualReviewState.CANDIDATE.value:
        return IntegrityDecision(False, BLOCKED_VISUAL_BIBLE_APPROVAL_BYPASS)
    try:
        version = VisualBibleVersionContract.model_validate(vb_payload)
        validate_version_contract(version)
    except (ValidationError, VisualBibleGateError) as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_VISUAL_BIBLE_PAYLOAD} ({exc})",
        )

    # 4. canon_fact claim 的 leaf evidence 必须 ⊆ 信封 evidence_refs（D-30-02）。
    claim_keys = {
        ref.evidence_key
        for claim in version.claims
        if claim.authority is VisualAuthority.CANON_FACT
        for ref in claim.evidence_refs
    }
    envelope_keys = set(envelope.get("evidence_refs") or [])
    if not claim_keys.issubset(envelope_keys):
        return IntegrityDecision(False, BLOCKED_VISUAL_BIBLE_EVIDENCE_MISMATCH)

    return IntegrityDecision(True)


def _evaluate_scene_candidate(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 31 SceneCandidateArtifact 信封 integrity gate（D-31-01..D-31-05）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``scene_candidate_set`` 负载上做确定性域边界校验：
      - ``scene_candidate_set`` 必须是严格 ``SceneCandidateSetContract`` 且通过
        ``validate_candidate_set_contract``（候选证据血缘、spoiler cutoff、
        heuristic-signal isolation、manifest hash 重放全部服务端重算，
        D-31-02/D-31-03/D-31-05）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass）→ blocked（用户选择/审查是服务端显式、
        append-only 的 ``key_scene:approve`` 迁移，D-31-04）；
      - 每个候选的 evidence key 必须 ⊆ 信封顶层 ``evidence_refs``（leaf-evidence
        资格门，D-31-02）；speaker/dialogue heuristic 信号是诊断元数据
        （D-31-05），结构性隔离由 ``validate_candidate_set_contract`` 强制。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 validators 保留
    permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进关键场景网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = SceneCandidateArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})")

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. scene_candidate_set 负载：严格域契约 + approval bypass 门 + 证据血缘。
    payload = envelope.get("scene_candidate_set")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_SCENE_CANDIDATE_PAYLOAD)
    if payload.get("review_state") != "candidate":
        return IntegrityDecision(False, BLOCKED_SCENE_CANDIDATE_APPROVAL_BYPASS)
    try:
        set_ = SceneCandidateSetContract.model_validate(payload)
        validate_candidate_set_contract(set_)
    except (ValidationError, KeySceneGateError) as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_SCENE_CANDIDATE_PAYLOAD} ({exc})",
        )

    # 4. 每个候选的 leaf evidence 必须 ⊆ 信封 evidence_refs（D-31-02）。
    candidate_keys = {
        ref.evidence_key
        for candidate in set_.candidates
        for ref in candidate.evidence_ranges
    }
    envelope_keys = set(envelope.get("evidence_refs") or [])
    if not candidate_keys.issubset(envelope_keys):
        return IntegrityDecision(False, BLOCKED_SCENE_CANDIDATE_EVIDENCE_MISMATCH)

    return IntegrityDecision(True)
