"""Visual-domain integrity evaluators（拆分自 structured_output_integrity）。

持有 4 个视觉类信封 evaluator：visual_bible / scene_candidate / scene_spec /
prompt，及其 Phase 30-32 专属 blocked 常量与 spec evidence 前缀匹配助手
（``_spec_evidence_keys`` / ``_evidence_prefix_matches``）。共享基座来自
``_integrity_core``（叶模块），本模块不依赖其它域模块（零环）。

拆分纪律与原文件一致（fail-closed gate）：任何失败 → 稳定 blocked，零写入。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.models.agent_runtime import SkillRun
from app.schemas.agent_runtime import (
    PromptArtifact,
    SceneCandidateArtifact,
    SceneSpecArtifact,
    VisualBibleArtifact,
)
from app.schemas.key_scene import (
    KeySceneGateError,
    SceneCandidateSetContract,
    validate_candidate_set_contract,
)
from app.schemas.scene_spec import (
    PromptRevisionContract,
    SceneSpecContract,
    SceneSpecGateError,
    SpecReviewState,
    validate_prompt_revision_contract,
    validate_scene_spec_contract,
)
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleGateError,
    VisualBibleVersionContract,
    VisualReviewState,
    validate_version_contract,
)
from app.services.agent_runtime._integrity_core import (
    BLOCKED_NO_EVIDENCE,
    BLOCKED_SCHEMA,
    IntegrityDecision,
    _check_common_lineage,
    _first_validation_error,
)

# Phase 30 Visual Bible 确定性边界（D-30-01..D-30-04）。
BLOCKED_VISUAL_BIBLE_PAYLOAD = (
    "integrity: visual bible payload failed domain validation"
)
BLOCKED_VISUAL_BIBLE_APPROVAL_BYPASS = (
    "integrity: visual bible approval bypass blocked — review_state must be candidate"
)
BLOCKED_VISUAL_BIBLE_EVIDENCE_MISMATCH = "integrity: visual bible canon claim evidence keys must be a subset of envelope evidence_refs"
# Phase 31 Key Scene 确定性边界（D-31-01..D-31-05）。
BLOCKED_SCENE_CANDIDATE_PAYLOAD = (
    "integrity: scene candidate set payload failed domain validation"
)
BLOCKED_SCENE_CANDIDATE_APPROVAL_BYPASS = "integrity: scene candidate approval bypass blocked — review_state must be candidate"
BLOCKED_SCENE_CANDIDATE_EVIDENCE_MISMATCH = "integrity: scene candidate evidence keys must be a subset of envelope evidence_refs"
# Phase 32 Scene Spec / Prompt 确定性边界（D-32-01..D-32-04）。
BLOCKED_SCENE_SPEC_PAYLOAD = "integrity: scene spec payload failed domain validation"
BLOCKED_SCENE_SPEC_APPROVAL_BYPASS = (
    "integrity: scene spec approval bypass blocked — review_state must be candidate"
)
BLOCKED_SCENE_SPEC_EVIDENCE_MISMATCH = (
    "integrity: scene spec evidence keys must be a subset of envelope evidence_refs"
)
BLOCKED_SCENE_SPEC_SOURCE_DRIFT = (
    "integrity: scene spec source_snapshot_hash drifts from envelope source_versions"
)
BLOCKED_PROMPT_PAYLOAD = "integrity: prompt revision payload failed domain validation"
BLOCKED_PROMPT_APPROVAL_BYPASS = (
    "integrity: prompt approval bypass blocked — review_state must be candidate"
)
BLOCKED_PROMPT_EVIDENCE_MISMATCH = "integrity: prompt scene spec evidence keys must be a subset of envelope evidence_refs"


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
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

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
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

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


def _spec_evidence_keys(spec: SceneSpecContract) -> set[str]:
    """收集 spec 内全部 namespaced leaf evidence keys（detail + constraint）。"""
    keys: set[str] = set()
    for detail in spec.details:
        keys.update(ref.evidence_key for ref in detail.evidence_refs)
    for constraint in spec.negative_constraints:
        keys.update(ref.evidence_key for ref in constraint.evidence_refs)
    return keys


def _evidence_prefix_matches(spec_key: str, envelope_keys: set[str]) -> bool:
    """namespaced spec evidence key 必须前缀匹配某个信封 leaf evidence key。

    编译器把 candidate 的原始 leaf evidence key 加 ``:{namespace}`` 后缀
    （如 ``ev-ayla-hair:action``）；信封 evidence_refs 携带原始 key
    （``ev-ayla-hair``）。前缀匹配确保每条 clause 的引用都能追溯到物化的
    leaf 证据（D-32-02），未物化的编造引用 → fail closed。
    """
    return any(k == spec_key or spec_key.startswith(k + ":") for k in envelope_keys)


def _evaluate_scene_spec(envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    """Phase 32 SceneSpecArtifact 信封 integrity gate（D-32-01..D-32-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``scene_spec`` 负载上做确定性域边界校验：
      - ``scene_spec`` 必须是严格 ``SceneSpecContract`` 且通过
        ``validate_scene_spec_contract``（唯一 detail/constraint key、snapshot/
        cutoff/VB-revision 血缘、content_hash 重放——Canon/Visual Bible 一致性
        与未支持细节拒绝，D-32-02）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass）→ blocked（用户审查/批准是服务端显式、
        append-only 的 ``scene_spec:approve`` 迁移，只授权 Phase 33 消费，
        D-32-04）；
      - spec 的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定；
      - 每个 detail/constraint 的 namespaced evidence key 必须前缀匹配信封
        顶层 ``evidence_refs``（leaf-evidence 资格门，D-32-02）。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 validators 保留
    permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 Scene Spec 网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = SceneSpecArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. scene_spec 负载：严格域契约 + approval bypass 门。
    payload = envelope.get("scene_spec")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_SCENE_SPEC_PAYLOAD)
    if payload.get("review_state") != SpecReviewState.CANDIDATE.value:
        return IntegrityDecision(False, BLOCKED_SCENE_SPEC_APPROVAL_BYPASS)
    try:
        spec = SceneSpecContract.model_validate(payload)
        validate_scene_spec_contract(spec)
    except (ValidationError, SceneSpecGateError) as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_SCENE_SPEC_PAYLOAD} ({exc})",
        )

    # 4. source snapshot 血缘绑定（D-32-03）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != spec.source_snapshot_hash:
        return IntegrityDecision(False, BLOCKED_SCENE_SPEC_SOURCE_DRIFT)

    # 5. namespaced evidence keys 必须 ⊆ 信封 evidence_refs（D-32-02）。
    envelope_keys = set(envelope.get("evidence_refs") or [])
    spec_keys = _spec_evidence_keys(spec)
    if not all(_evidence_prefix_matches(key, envelope_keys) for key in spec_keys):
        return IntegrityDecision(False, BLOCKED_SCENE_SPEC_EVIDENCE_MISMATCH)

    return IntegrityDecision(True)


def _evaluate_prompt(envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    """Phase 32 PromptArtifact 信封 integrity gate（D-32-01..D-32-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``prompt_revision`` / ``scene_spec`` 负载上做确定性域边界校验：
      - ``prompt_revision`` 必须是严格 ``PromptRevisionContract`` 且对其派生自
        的 ``scene_spec``（严格 ``SceneSpecContract`` + 域校验）通过
        ``validate_prompt_revision_contract``（prompt scene_spec_hash 与 spec
        一致、canonical sections 与 spec 确定性重放一致、input_hash/prompt_hash
        可重放——provider-neutral、无 unsupported detail 渲染成 canon，
        D-32-01/D-32-03）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass）→ blocked（``scene_spec:approve`` 只授权
        Phase 33 消费，D-32-04）；
      - spec 的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定；
      - spec 的 namespaced evidence keys 必须前缀匹配信封顶层 ``evidence_refs``
        （leaf-evidence 资格门，D-32-02）。
    任何失败 → 稳定 blocked，零写入；prompt 字符串永远不是权威（D-32-01）。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 Prompt 网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = PromptArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. prompt + spec 负载：严格域契约 + approval bypass 门。
    prompt_payload = envelope.get("prompt_revision")
    spec_payload = envelope.get("scene_spec")
    if not isinstance(prompt_payload, dict) or not isinstance(spec_payload, dict):
        return IntegrityDecision(False, BLOCKED_PROMPT_PAYLOAD)
    if prompt_payload.get("review_state") != SpecReviewState.CANDIDATE.value:
        return IntegrityDecision(False, BLOCKED_PROMPT_APPROVAL_BYPASS)
    try:
        spec = SceneSpecContract.model_validate(spec_payload)
        validate_scene_spec_contract(spec)
        prompt = PromptRevisionContract.model_validate(prompt_payload)
        validate_prompt_revision_contract(prompt, spec)
    except (ValidationError, SceneSpecGateError) as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_PROMPT_PAYLOAD} ({exc})",
        )

    # 4. source snapshot 血缘绑定（D-32-03）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != spec.source_snapshot_hash:
        return IntegrityDecision(False, BLOCKED_SCENE_SPEC_SOURCE_DRIFT)

    # 5. spec 的 namespaced evidence keys 必须 ⊆ 信封 evidence_refs（D-32-02）。
    envelope_keys = set(envelope.get("evidence_refs") or [])
    spec_keys = _spec_evidence_keys(spec)
    if not all(_evidence_prefix_matches(key, envelope_keys) for key in spec_keys):
        return IntegrityDecision(False, BLOCKED_PROMPT_EVIDENCE_MISMATCH)

    return IntegrityDecision(True)
