"""World-model domain default tool services for the agent-tools facade.

Extracted from the agent-tools facade (Phase 27-05 world-model tools): this
module owns the default service entry and JSON-serialization helpers for the
read-only events / character-state / character-knowledge / world-rules /
evidence-span tools. Query seams delegate to the world-model entity/event/
knowledge queries with the facade-supplied version + server cutoff (D-05);
the epistemic answer helpers here are the only serializers that know how to
flatten pydantic claims/evidence into the tool contract payload.
"""

from __future__ import annotations

from typing import Any

from app.services.agent_tools.errors import InvalidInputError, NotFoundError
from app.services.novel_service import novel_service
from app.services.queryplan.adapters import chapter_content_hash
from app.services.queryplan.contracts import leaf_evidence_key
from app.services.world_model.entity_queries import WorldEntityQueries
from app.services.world_model.event_queries import WorldModelEventQueries
from app.services.world_model.knowledge import EpistemicAspect, KnowledgeResultStatus
from app.services.world_model.knowledge_queries import KnowledgeQueries


def _epistemic_answer_to_json(answer) -> dict[str, Any]:
    """把 EpistemicAnswer 序列化为 JSON 安全 payload（claims/evidence 是 pydantic）。"""
    return {
        "status": answer.status.value,
        "subject": answer.subject,
        "claims": [claim.model_dump(mode="json") for claim in answer.claims],
        "evidence": [ref.model_dump(mode="json") for ref in answer.evidence],
        "has_approval": answer.has_approval,
        "message": answer.message,
    }


def _merge_state_answers(
    *, subject: str, answers: list[Any], message: str
) -> dict[str, Any]:
    """合并 state/goal/motivation 三个 aspect 的查询结果（无编造，abstain 优先）。"""
    claims = tuple(claim for answer in answers for claim in answer.claims)
    evidence = tuple(ref for answer in answers for ref in answer.evidence)
    approved = any(answer.has_approval for answer in answers)
    if not claims:
        status = KnowledgeResultStatus.ABSTAINED
    elif approved:
        status = KnowledgeResultStatus.ANSWERED
    else:
        status = KnowledgeResultStatus.CANDIDATE_ONLY
    return {
        "status": status.value,
        "subject": subject,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "evidence": [ref.model_dump(mode="json") for ref in evidence],
        "has_approval": approved,
        "message": message,
    }


async def _default_get_events(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff: int,
) -> dict[str, Any] | None:
    """世界模型事件/因果投影（D-05 cutoff 过滤；无投影 → None → 404-hide）。"""
    projection = await WorldModelEventQueries(db).query_cutoff_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        cutoff=cutoff,
    )
    return projection.model_dump(mode="json") if projection is not None else None


async def _default_get_character_state(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    subject: str,
    cutoff: int,
    pov: str | None,
) -> dict[str, Any]:
    """角色状态/目标/动机（aspect ∈ state/goal/motivation 合并，D-05）。"""
    queries = KnowledgeQueries(db)
    answers = [
        await queries.query_character_knowledge(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            subject=subject,
            cutoff=cutoff,
            pov=pov,
            aspect=aspect,
        )
        for aspect in (
            EpistemicAspect.STATE,
            EpistemicAspect.GOAL,
            EpistemicAspect.MOTIVATION,
        )
    ]
    return _merge_state_answers(
        subject=subject,
        answers=answers,
        message="character state merged across state/goal/motivation (D-05)",
    )


async def _default_get_character_knowledge(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    subject: str,
    cutoff: int,
    pov: str | None,
) -> dict[str, Any]:
    """角色知识（aspect=knowledge；mistaken/hidden 保持显式标签，D-05）。"""
    answer = await KnowledgeQueries(db).query_character_knowledge(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        subject=subject,
        cutoff=cutoff,
        pov=pov,
        aspect=EpistemicAspect.KNOWLEDGE,
    )
    return _epistemic_answer_to_json(answer)


async def _default_get_world_rules(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """世界规则与规则例外（D-05 cutoff 过滤；例外是 first-class，D-04）。"""
    queries = WorldEntityQueries(db)
    rules = [
        rule.model_dump(mode="json")
        for rule in await queries.query_rules(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if rule.disclosure_cutoff <= cutoff
    ]
    exceptions = [
        exc.model_dump(mode="json")
        for exc in await queries.query_rule_exceptions(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if exc.disclosure_cutoff <= cutoff
    ]
    return {"rules": rules, "exceptions": exceptions}


async def _default_get_evidence_span(
    db,
    *,
    chapter_id: int,
    source_start: int,
    source_end: int,
    content_hash: str,
) -> dict[str, Any] | None:
    """按 chapter+offsets+content_hash 物化 leaf 证据跨度（D-07/D-08）。

    chapter 缺失 → None（404-hide）；offsets 非法 / hash 与原文切片不匹配 →
    InvalidInputError（fail closed，绝不返回错误切片）。
    """
    chapter = await novel_service.get_chapter(db, chapter_id)
    if chapter is None:
        return None
    content = chapter.content
    if source_start < 0 or source_end > len(content) or source_end <= source_start:
        raise InvalidInputError(
            f"offsets [{source_start},{source_end}) 不是合法 half-open 区间"
        )
    excerpt = content[source_start:source_end]
    if chapter_content_hash(excerpt) != content_hash:
        raise InvalidInputError("evidence content hash 与原文切片不匹配")
    return {
        "evidence_key": leaf_evidence_key(
            chapter_id=chapter_id,
            source_start=source_start,
            source_end=source_end,
            content_hash=content_hash,
        ),
        "chapter_id": chapter_id,
        "chapter_number": chapter.chapter_number,
        "novel_id": chapter.novel_id,
        "source_start": source_start,
        "source_end": source_end,
        "content_hash": content_hash,
        "excerpt": excerpt,
    }


async def _resolve_world_model_version(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
) -> int:
    """显式 version 直接返回；缺省取该 owner/novel 最新版本（无 → 404-hide）。"""
    if version_id is not None:
        return int(version_id)
    versions = await WorldModelEventQueries(db).list_versions(
        owner_id=owner_id, novel_id=novel_id
    )
    if not versions:
        raise NotFoundError("world-model projection not found in owner scope")
    return versions[-1]
