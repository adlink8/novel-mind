"""backfill 产物的确定性域物化（Phase 40，candidate-only）。

把 chat_backfill skill 产物（artifact content）物化为对应域表 candidate 行。
全部 candidate-only，绝不自动 promotion；gate 前提不满足时诚实返回 skipped
原因，绝不伪造通过。

按 artifact.type 分发：
- scene_candidate            → key_scene_sets/candidates/evidence_ranges
- world_model_candidate      → world_model_knowledge（EpistemicGate → candidate）
- visual_bible               → visual_bible_versions/entities/claims/evidence_refs
- chapter_analysis / story_arc → digest 摘要非 leaf 证据 → skipped
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

    from app.services.world_model.knowledge import (
        EpistemicGate,
        build_knowledge_candidate,
    )
    from app.services.world_model.knowledge_repository import KnowledgeRepository

    claims = []
    skipped: list[str] = []
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            skipped.append("unmapped")
            continue
        mapped = await _map_epistemic_claim(
            session,
            run=run,
            version_id=version_id,
            snapshot_hash=snapshot_hash,
            cutoff=cutoff,
            raw=raw_claim,
        )
        if mapped is None:
            skipped.append("unmapped")
            continue
        gate = EpistemicGate(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            version_id=version_id,
            source_snapshot_hash=snapshot_hash,
            disclosure_cutoff=cutoff,
            approvals=frozenset(),
        )
        result = gate.validate_claim(mapped)
        if result.claim is None:
            skipped.append(f"{mapped.knowledge_key}:{result.reason_codes}")
            continue
        claims.append(result.claim)

    if not claims:
        return f"skipped:{skipped[0] if skipped else 'all_gate_rejected'}"
    try:
        projection = build_knowledge_candidate(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            version_id=version_id,
            claims=claims,
        )
        await KnowledgeRepository(session).append_projection(projection)
    except Exception as exc:  # noqa: BLE001
        return f"skipped:{type(exc).__name__}"
    return "ok"


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
    from app.services.world_model.contracts import Authority, EvidenceRef
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
    disclosure_cutoff = raw.get("disclosure_cutoff")
    if not isinstance(disclosure_cutoff, int) or disclosure_cutoff < 1:
        return None
    authority_raw = raw.get("authority")
    authority = (
        Authority(authority_raw)
        if authority_raw in Authority._value2member_map_
        else None
    )
    if authority is None:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        return None

    # evidence_refs：qp: key 解析 + DB 查 chapter_number
    source_refs = []
    for ref_key in raw.get("evidence_refs") or []:
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
        source_refs.append(
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
    if not source_refs:
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


async def _authorized_cutoff(session: AsyncSession, *, novel_id: int) -> int | None:
    from app.models.novel import Novel as NovelModel

    novel = await session.get(NovelModel, novel_id)
    if novel is None:
        return None
    # 阅读进度即授权 cutoff（与 chat 路径一致）；无进度时保守取第 1 章。
    progress = getattr(novel, "reading_progress", None)
    if isinstance(progress, int) and progress > 0:
        return progress
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
