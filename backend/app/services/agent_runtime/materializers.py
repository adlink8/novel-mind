"""backfill 产物的确定性域物化（Phase 40，candidate-only）。

把 chat_backfill skill 产物（artifact content）物化为对应域表 candidate 行。
全部 candidate-only，绝不自动 promotion；gate 前提不满足时诚实返回 skipped
原因，绝不伪造通过。

按 artifact.type 分发：
- scene_candidate            → key_scene_sets/candidates/evidence_ranges
- world_model_candidate      → 六类 claim 物化路由（见下方分发说明）
- visual_bible               → visual_bible_versions/entities/claims/evidence_refs
- chapter_analysis / story_arc → digest 摘要非 leaf 证据 → skipped

world_model_candidate 分发（全部 candidate-only，fail closed，绝不伪造通过）：
- character_state / character_knowledge
        → EpistemicGate → KnowledgeRepository（知识表）
- event                           → WorldModelGate.validate_event
        → WorldModelEventRepository（事件表，与 edges 同一密封投影）
- causal_edge                     → WorldModelGate.validate_edge（依赖本批次 event）
- world_rule / rule_exception     → RuleGate → WorldEntityRepository（实体表投影内的 rules/exceptions）
- entity / entity_link            → EntityGate → WorldEntityRepository（实体表投影内的 entities/links）
disclosure_cutoff 一律程序注入（授权 cutoff），confidence 一律 float；
任一 gate 拒绝都诚实进入 skipped，绝不物化。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chapter
from app.models.agent_runtime import SkillRun
from app.schemas.key_scene import (
    KeySceneReviewState,
    SceneCandidateSetContract,
)

logger = logging.getLogger(__name__)

# 可物化为域表 candidate 的 artifact.type。
_MATERIALIZABLE = {
    "scene_candidate",
    "world_model_candidate",
    "visual_bible",
}


async def materialize_to_domain(
    session: AsyncSession,
    *,
    run: SkillRun,
    artifact_type: str,
    content: dict[str, Any],
) -> str:
    """把产物物化为域表 candidate；返回 "ok" 或跳过原因。"""
    if artifact_type not in _MATERIALIZABLE:
        return f"skipped:{artifact_type}_not_materializable"

    handler = {
        "scene_candidate": _materialize_key_scenes,
        "world_model_candidate": _materialize_world_model_knowledge,
        "visual_bible": _materialize_visual_bible,
    }[artifact_type]
    try:
        return await handler(session, run=run, content=content)
    except Exception as exc:  # noqa: BLE001 - 物化失败诚实记录，绝不伪造通过
        logger.warning(
            "backfill materialize failed run_id=%s type=%s: %s",
            run.id,
            artifact_type,
            exc,
        )
        return f"skipped:{type(exc).__name__}"


# ══════════════════════════════════════════════════════════════════════
# key_scenes（detect-key-scenes → SceneCandidateSetContract → import_set）
# ══════════════════════════════════════════════════════════════════════


async def _materialize_key_scenes(
    session: AsyncSession,
    *,
    run: SkillRun,
    content: dict[str, Any],
) -> str:
    from app.services.key_scenes.boundaries import SceneBoundaryService
    from app.services.key_scenes.candidates import (
        CandidateService,
        KeySceneCandidateConflict,
        KeySceneGateError,
    )

    raw = content.get("scene_candidate_set")
    if not isinstance(raw, dict):
        return "skipped:missing_scene_candidate_set"
    try:
        set_contract = SceneCandidateSetContract.model_validate(raw)
    except ValidationError:
        return "skipped:invalid_scene_candidate_set"
    if set_contract.review_state != KeySceneReviewState.CANDIDATE:
        return "skipped:non_candidate_review_state"
    if set_contract.owner_id != run.owner_id or set_contract.novel_id != run.novel_id:
        return "skipped:set_scope_mismatch"

    boundaries = SceneBoundaryService(session)
    if (
        await boundaries.verify_novel_scope(
            owner_id=run.owner_id, novel_id=run.novel_id
        )
        is None
    ):
        return "skipped:novel_scope_mismatch"
    try:
        current_hash, _ = await boundaries.load_source_snapshot(
            owner_id=run.owner_id, novel_id=run.novel_id
        )
    except Exception:  # noqa: BLE001
        return "skipped:snapshot_unavailable"
    if current_hash != set_contract.source_snapshot_hash:
        return "skipped:stale_source_snapshot"

    try:
        await CandidateService(session).import_set(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            set_contract=set_contract,
        )
    except KeySceneCandidateConflict:
        # 内容一致的重复 version_key → replay（不重复写）；不一致 → fail closed。
        return "ok"
    except KeySceneGateError as exc:
        return f"skipped:{str(exc)[:80]}"
    return "ok"


# ══════════════════════════════════════════════════════════════════════
# world_model_knowledge（propose-world-model-candidates → EpistemicGate）
# ══════════════════════════════════════════════════════════════════════


async def _materialize_world_model_knowledge(
    session: AsyncSession,
    *,
    run: SkillRun,
    content: dict[str, Any],
) -> str:
    from app.services.agent_runtime.materialize_helpers import (
        chat_snapshot_hash,
        resolve_active_analysis_version_id,
    )

    candidates = content.get("candidates")
    if not isinstance(candidates, dict):
        return "skipped:missing_candidates"
    raw_claims = candidates.get("claims") or []
    if not raw_claims:
        return "skipped:no_knowledge_claims"

    version_id = await resolve_active_analysis_version_id(
        session, owner_id=run.owner_id, novel_id=run.novel_id
    )
    if version_id is None:
        return "skipped:no_active_analysis_version"
    snapshot_hash = await chat_snapshot_hash(
        session, owner_id=run.owner_id, novel_id=run.novel_id, version_id=version_id
    )
    if not snapshot_hash:
        return "skipped:no_snapshot_hash"
    cutoff = await _authorized_cutoff(session, novel_id=run.novel_id)
    if cutoff is None:
        return "skipped:no_cutoff"

    # 三个 repo 各自 fail closed、幂等；Gate 全部用同一 scope（approvals 空 → fail closed）。
    from app.services.world_model._entity_gate import EntityGate
    from app.services.world_model._entity_projection import build_entity_candidate
    from app.services.world_model.entity_repository import WorldEntityRepository
    from app.services.world_model.event_repository import (
        WorldModelEventRepository,
    )
    from app.services.world_model.gates import (
        WorldModelGate,
        build_candidate as build_event_candidate,
    )
    from app.services.world_model.knowledge import (
        EpistemicGate,
        build_knowledge_candidate,
    )
    from app.services.world_model.knowledge_repository import KnowledgeRepository
    from app.services.world_model.rules import RuleGate

    scope = dict(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
        source_snapshot_hash=snapshot_hash,
        disclosure_cutoff=cutoff,
        approvals=frozenset(),
    )
    wm_gate = WorldModelGate(**scope)
    rule_gate = RuleGate(**scope)
    entity_gate = EntityGate(**scope)
    epistemic_gate = EpistemicGate(**scope)

    # 按类型分桶：先映射（fail closed），再按批次内依赖关系 gate。
    knowledge_inputs: list = []
    event_inputs: list = []
    edge_inputs: list = []
    rule_inputs: list = []
    exception_inputs: list = []
    entity_inputs: list = []
    link_inputs: list = []
    skipped: list[str] = []

    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            skipped.append("unmapped")
            continue
        kind = raw_claim.get("claim_kind")
        if kind in ("character_state", "character_knowledge"):
            mapped = await _map_epistemic_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
            if mapped is None:
                skipped.append("unmapped")
                continue
            knowledge_inputs.append(mapped)
            continue
        if kind == "event":
            mapped = await _map_event_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
        elif kind == "causal_edge":
            mapped = await _map_edge_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
        elif kind == "world_rule":
            mapped = await _map_rule_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
        elif kind == "rule_exception":
            mapped = await _map_rule_exception_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
        elif kind == "entity":
            mapped = await _map_entity_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
        elif kind == "entity_link":
            mapped = await _map_entity_link_claim(
                session, run=run, version_id=version_id,
                snapshot_hash=snapshot_hash, cutoff=cutoff, raw=raw_claim,
            )
        else:
            skipped.append("unmapped")
            continue
        if mapped is None:
            skipped.append("unmapped")
            continue
        if kind == "event":
            event_inputs.append(mapped)
        elif kind == "causal_edge":
            edge_inputs.append(mapped)
        elif kind == "world_rule":
            rule_inputs.append(mapped)
        elif kind == "rule_exception":
            exception_inputs.append(mapped)
        elif kind == "entity":
            entity_inputs.append(mapped)
        elif kind == "entity_link":
            link_inputs.append(mapped)

    # ── gate 阶段（按批次内依赖顺序）──
    events_by_key: dict[str, Any] = {}
    passed_edges: list = []
    for claim in event_inputs:
        result = wm_gate.validate_event(claim)
        if result.fact is None:
            skipped.append(f"{claim.event_key}:{_codes(result.verdicts)}")
            continue
        events_by_key[result.fact.event_key] = result.fact
    for claim in edge_inputs:
        result = wm_gate.validate_edge(claim, events_by_key)
        if result.edge is None:
            skipped.append(f"{claim.edge_key}:{_codes(result.verdicts)}")
            continue
        passed_edges.append(result.edge)

    rule_keys: set[str] = set()
    passed_rules: list = []
    passed_exceptions: list = []
    for claim in rule_inputs:
        result = rule_gate.validate_rule(claim)
        if result.rule is None:
            skipped.append(f"{claim.rule_key}:{_codes(result.verdicts)}")
            continue
        rule_keys.add(result.rule.rule_key)
        passed_rules.append(result.rule)
    for claim in exception_inputs:
        result = rule_gate.validate_exception(claim, rule_keys)
        if result.exception is None:
            skipped.append(f"{claim.exception_key}:{_codes(result.verdicts)}")
            continue
        passed_exceptions.append(result.exception)

    entity_keys: set[str] = set()
    passed_entities: list = []
    for claim in entity_inputs:
        result = entity_gate.validate_entity(claim)
        if result.entity is None:
            skipped.append(f"{claim.entity_key}:{_codes(result.verdicts)}")
            continue
        entity_keys.add(result.entity.entity_key)
        passed_entities.append(result.entity)
    passed_links: list = []
    for claim in link_inputs:
        # 链接端点必须是本批次已通过的实体（projection-local），否则 fail closed。
        if claim.source_key not in entity_keys or claim.target_key not in entity_keys:
            skipped.append(f"{claim.link_key}:unknown_endpoint")
            continue
        result = entity_gate.validate_link(claim)
        if result.link is None:
            skipped.append(f"{claim.link_key}:{_codes(result.verdicts)}")
            continue
        passed_links.append(result.link)

    knowledge_claims: list = []
    for claim in knowledge_inputs:
        result = epistemic_gate.validate_claim(claim)
        if result.claim is None:
            skipped.append(f"{claim.knowledge_key}:{result.reason_codes}")
            continue
        knowledge_claims.append(result.claim)

    # ── 写入阶段（各 repo 独立 try/except，绝不伪造通过）──
    wrote_any = False
    if events_by_key:
        try:
            projection = build_event_candidate(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                version_id=version_id,
                events=list(events_by_key.values()),
                edges=passed_edges,
            )
            await WorldModelEventRepository(session).append_projection(projection)
            wrote_any = True
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"event_repo:{type(exc).__name__}")
    if passed_entities or passed_links or passed_rules or passed_exceptions:
        try:
            projection = build_entity_candidate(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                version_id=version_id,
                entities=passed_entities,
                links=passed_links,
                rules=passed_rules,
                exceptions=passed_exceptions,
            )
            await WorldEntityRepository(session).append_projection(projection)
            wrote_any = True
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"entity_repo:{type(exc).__name__}")
    if knowledge_claims:
        try:
            projection = build_knowledge_candidate(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                version_id=version_id,
                claims=knowledge_claims,
            )
            await KnowledgeRepository(session).append_projection(projection)
            wrote_any = True
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"knowledge_repo:{type(exc).__name__}")

    if wrote_any:
        return "ok"
    # 聚合去重后上报（skipped[0] 常是首条 world_rule 的 unmapped，会掩盖
    # 后续 claim 的真实拒绝原因，如 canon_fact 缺审批——run 132 实测）。
    summary = ";".join(dict.fromkeys(skipped)) if skipped else "all_gate_rejected"
    return f"skipped:{summary[:100]}"


async def _map_epistemic_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """把 skill 产物的 raw claim 映射为 EpistemicClaim；缺关键字段 → None。"""
    from app.services.world_model.contracts import Authority
    from app.services.world_model.knowledge import (
        EpistemicAspect,
        EpistemicClaim,
        SourceKind,
    )

    claim_kind = raw.get("claim_kind")
    if claim_kind == "character_state":
        aspect = EpistemicAspect.STATE
    elif claim_kind == "character_knowledge":
        aspect = EpistemicAspect.KNOWLEDGE
    else:
        return None  # 其它 claim_kind 不属 knowledge 表

    claim_key = raw.get("claim_key")
    subject = raw.get("subject")
    proposition = raw.get("proposition")
    if not claim_key or not subject or not proposition:
        return None
    # disclosure_cutoff 是血缘锚定字段，程序权威注入（run 已授权 cutoff），
    # 模型输出值只作 sanity 参考——模型常照抄 SKILL 示例的字面值（如 1），
    # 导致 known_at > cutoff 被整条拒绝（run 126 实测）。Gate 用同一 cutoff
    # 裁决，注入值与其一致，不引入越权披露。
    disclosure_cutoff = int(cutoff)
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None

    # evidence_refs：qp: key 解析 + DB 查 chapter_number（共用 helper）。
    source_refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if source_refs is None or not source_refs:
        return None

    known_at = min(ref.chapter_number for ref in source_refs)
    if known_at > disclosure_cutoff:
        return None

    return EpistemicClaim(
        claim_kind="character_knowledge",
        knowledge_key=f"{claim_key}",
        subject=str(subject),
        aspect=aspect,
        proposition=str(proposition),
        known_at=known_at,
        disclosure_cutoff=disclosure_cutoff,
        pov="omniscient",
        pov_kind="omniscient",
        source_kind=SourceKind.CANON_SOURCE,
        authority=authority,
        confidence=float(confidence),
        epistemic_status="candidate",
        transition_from=None,
        lineage=(claim_key,),
        source_refs=tuple(source_refs),
        gate_status="pending",
        gate_reason=None,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _resolve_evidence_refs(
    session: AsyncSession,
    *,
    run: SkillRun,
    snapshot_hash: str,
    raw_refs: object,
) -> tuple | None:
    """解析 raw evidence_refs（``qp:`` keys）为 EvidenceRef 元组。

    任一 key 解析失败或指向不存在/跨 novel 的章节 → 返回 None（fail closed）。
    空列表 → 返回空元组（允许 causal_edge 无独立证据，交给 gate 以
    co_occurrence_only 拒绝）。knowledge / event / rule / entity 等要求
    非空证据，由调用方在解析后显式判断。
    """
    from app.services.world_model.contracts import EvidenceRef

    if not raw_refs:
        return tuple()
    refs = []
    for ref_key in raw_refs:
        parsed = _parse_qp_key(str(ref_key))
        if parsed is None:
            return None
        chapter_id, start, end, content_hash = parsed
        ch = await session.scalar(
            select(Chapter.chapter_number).where(
                Chapter.id == chapter_id, Chapter.novel_id == run.novel_id
            )
        )
        if ch is None:
            return None
        refs.append(
            EvidenceRef(
                evidence_id=str(ref_key),
                chapter_id=chapter_id,
                chapter_number=ch,
                source_start=start,
                source_end=end,
                content_hash=content_hash,
                source_snapshot_hash=snapshot_hash,
            )
        )
    return tuple(refs)


def _authority_of(raw_authority: object):
    """把模型输出的 authority 字符串解析为 Authority 枚举；非法 → None。"""
    from app.services.world_model.contracts import Authority

    if raw_authority in Authority._value2member_map_:
        return Authority(raw_authority)
    return None


def _codes(verdicts) -> str:
    """把 verdict 列表的拒绝原因码聚合成稳定字符串（用于 skipped 上报）。"""
    return ",".join(sorted({str(v.reason_code) for v in verdicts}))


async def _map_event_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """映射 event claim → EventClaim；必填 claim_key/title/description，缺 → None。"""
    from app.services.world_model.claims import EffectiveInterval, EventClaim

    claim_key = raw.get("claim_key")
    title = raw.get("title")
    description = raw.get("description")
    if not claim_key or not title or not description:
        return None
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None
    eff_raw = raw.get("effective") or {}
    effective = EffectiveInterval(start=eff_raw.get("start"), end=eff_raw.get("end"))
    refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if refs is None or not refs:
        return None
    return EventClaim(
        event_key=str(claim_key),
        title=str(title),
        description=str(description),
        authority=authority,
        confidence=float(confidence),
        effective=effective,
        disclosure_cutoff=int(cutoff),
        source_refs=refs,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _map_edge_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """映射 causal_edge claim → CausalEdgeClaim；source/target/edge_type 必填。

    空 evidence_refs 允许（交给 gate 以 co_occurrence_only 拒绝）。
    """
    from app.services.world_model.claims import CausalEdgeClaim
    from app.services.world_model.contracts import CausalEdgeType

    claim_key = raw.get("claim_key")
    source_event_key = raw.get("source_event_key")
    target_event_key = raw.get("target_event_key")
    edge_type_raw = raw.get("edge_type")
    if not claim_key or not source_event_key or not target_event_key:
        return None
    edge_type = (
        CausalEdgeType(edge_type_raw)
        if edge_type_raw in CausalEdgeType._value2member_map_
        else None
    )
    if edge_type is None:
        return None
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None
    refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if refs is None:
        return None
    return CausalEdgeClaim(
        edge_key=str(claim_key),
        source_event_key=str(source_event_key),
        target_event_key=str(target_event_key),
        edge_type=edge_type,
        authority=authority,
        confidence=float(confidence),
        disclosure_cutoff=int(cutoff),
        source_refs=refs,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _map_rule_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """映射 world_rule claim → RuleClaim；必填 claim_key/rule_name/proposition。"""
    from app.services.world_model.rules import RuleClaim, SourceKind

    claim_key = raw.get("claim_key")
    rule_name = raw.get("rule_name")
    statement = raw.get("proposition")
    if not claim_key or not rule_name or not statement:
        return None
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None
    refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if refs is None or not refs:
        return None
    return RuleClaim(
        rule_key=str(claim_key),
        rule_name=str(rule_name),
        statement=str(statement),
        source_kind=SourceKind.CANON_SOURCE,
        authority=authority,
        confidence=float(confidence),
        disclosure_cutoff=int(cutoff),
        source_refs=refs,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _map_rule_exception_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """映射 rule_exception claim → RuleExceptionClaim；rule_key 必须本批次规则。"""
    from app.services.world_model.rules import RuleExceptionClaim, SourceKind

    claim_key = raw.get("claim_key")
    rule_key = raw.get("rule_key")
    statement = raw.get("proposition")
    if not claim_key or not rule_key or not statement:
        return None
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None
    applies_to = raw.get("applies_to")
    refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if refs is None or not refs:
        return None
    return RuleExceptionClaim(
        exception_key=str(claim_key),
        rule_key=str(rule_key),
        applies_to=str(applies_to) if applies_to else None,
        statement=str(statement),
        source_kind=SourceKind.CANON_SOURCE,
        authority=authority,
        confidence=float(confidence),
        disclosure_cutoff=int(cutoff),
        source_refs=refs,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _map_entity_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """映射 entity claim → EntityClaim；entity_type/primary_name 必填。"""
    from app.services.world_model._entity_models import (
        EntityAlias,
        EntityClaim,
        EntityType,
    )
    from app.services.world_model.rules import SourceKind

    claim_key = raw.get("claim_key")
    entity_type_raw = raw.get("entity_type")
    primary_name = raw.get("primary_name") or raw.get("subject")
    description = raw.get("proposition")
    if not claim_key or not entity_type_raw or not primary_name or not description:
        return None
    entity_type = (
        EntityType(entity_type_raw)
        if entity_type_raw in EntityType._value2member_map_
        else None
    )
    if entity_type is None:
        return None
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None
    aliases = tuple(EntityAlias(alias=str(a)) for a in (raw.get("aliases") or []))
    refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if refs is None or not refs:
        return None
    return EntityClaim(
        entity_key=str(claim_key),
        entity_type=entity_type,
        primary_name=str(primary_name),
        description=str(description),
        aliases=aliases,
        source_kind=SourceKind.CANON_SOURCE,
        authority=authority,
        confidence=float(confidence),
        disclosure_cutoff=int(cutoff),
        source_refs=refs,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _map_entity_link_claim(
    session: AsyncSession,
    *,
    run: SkillRun,
    version_id: int,
    snapshot_hash: str,
    cutoff: int,
    raw: dict[str, Any],
):
    """映射 entity_link claim → EntityLinkClaim；link_kind/source/target 必填。"""
    from app.services.world_model._entity_models import EntityLinkClaim, LinkKind
    from app.services.world_model.rules import SourceKind

    claim_key = raw.get("claim_key")
    link_kind_raw = raw.get("link_kind")
    source_key = raw.get("source_key")
    target_key = raw.get("target_key")
    if not claim_key or not link_kind_raw or not source_key or not target_key:
        return None
    link_kind = (
        LinkKind(link_kind_raw)
        if link_kind_raw in LinkKind._value2member_map_
        else None
    )
    if link_kind is None:
        return None
    authority = _authority_of(raw.get("authority"))
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None
    refs = await _resolve_evidence_refs(
        session, run=run, snapshot_hash=snapshot_hash, raw_refs=raw.get("evidence_refs")
    )
    if refs is None or not refs:
        return None
    return EntityLinkClaim(
        link_key=str(claim_key),
        link_kind=link_kind,
        source_key=str(source_key),
        target_key=str(target_key),
        source_kind=SourceKind.CANON_SOURCE,
        authority=authority,
        confidence=float(confidence),
        disclosure_cutoff=int(cutoff),
        source_refs=refs,
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        version_id=version_id,
    )


async def _authorized_cutoff(session: AsyncSession, *, novel_id: int) -> int | None:
    from app.models.novel import Novel as NovelModel
    from app.services.timeline.query import resolve_chapter_cutoff

    novel = await session.get(NovelModel, novel_id)
    if novel is None:
        return None
    # 阅读进度即授权 cutoff（与 chat 路径一致）。reading_progress 现为 dict
    # （chapter_id/progress_percent），复用 timeline 的 resolve_chapter_cutoff
    # 统一解析——旧实现只认 int，dict 一律兜底第 1 章，导致 knowledge backfill
    # 的证据章（189）> cutoff(1) 全部被拒（run 126/128/130 实测根因）。
    cutoff = await resolve_chapter_cutoff(session, novel)
    if cutoff is not None and int(cutoff) > 0:
        return int(cutoff)
    return 1


# ══════════════════════════════════════════════════════════════════════
# visual_bible（build-visual-bible → VisualBibleEvidenceService + create_revision）
# ══════════════════════════════════════════════════════════════════════


async def _materialize_visual_bible(
    session: AsyncSession,
    *,
    run: SkillRun,
    content: dict[str, Any],
) -> str:
    from app.schemas.visual_bible import VisualBibleVersionContract
    from app.services.visual_bible.authority import (
        VisualBibleAuthorityService,
        VisualBibleAuthorityError,
    )
    from app.services.visual_bible.evidence import VisualBibleEvidenceService

    raw = content.get("visual_bible")
    if not isinstance(raw, dict):
        return "skipped:missing_visual_bible"
    try:
        version = VisualBibleVersionContract.model_validate(raw)
    except ValidationError:
        return "skipped:invalid_visual_bible"
    if version.review_state.value != "candidate":
        return "skipped:non_candidate_review_state"
    if version.owner_id != run.owner_id or version.novel_id != run.novel_id:
        return "skipped:version_scope_mismatch"

    evidence = VisualBibleEvidenceService(session)
    outcome = await evidence.materialize_version_claims(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff_chapter=version.cutoff_chapter,
        claims=version.claims,
    )
    if outcome.blocked:
        first = outcome.unresolved[0] if outcome.unresolved else None
        return f"skipped:{first.reason_code if first else 'evidence_blocked'}"
    verified = {m.claim.claim_key: m.verified_evidence for m in outcome.resolved}
    try:
        await VisualBibleAuthorityService(session).create_revision(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            version=version,
            verified_evidence=verified,
        )
    except VisualBibleAuthorityError as exc:
        return f"skipped:{type(exc).__name__}"
    return "ok"


# ══════════════════════════════════════════════════════════════════════
# 共用辅助
# ══════════════════════════════════════════════════════════════════════


def _parse_qp_key(key: str) -> tuple[int, int, int, str] | None:
    """解析 ``qp:<chapter_id>:<source_start>:<source_end>:<content_hash>``。"""
    parts = key.split(":")
    if len(parts) != 5 or parts[0] != "qp":
        return None
    try:
        chapter_id, start, end = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    if len(parts[4]) != 64 or start < 0 or end <= start:
        return None
    return chapter_id, start, end, parts[4]
