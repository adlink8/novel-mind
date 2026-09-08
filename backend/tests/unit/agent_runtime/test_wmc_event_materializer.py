"""world_model_candidate 六类 claim 物化路由（事件/因果/规则/例外/实体/链接）。

run 133 之后 knowledge 路径已落库；本测试打通 event/causal_edge/world_rule/
rule_exception/entity/entity_link 五条新路由，并验证：
- 合格 claim 落到正确的域表（事件表 / 实体表 / 知识表）；
- 缺字段、canon_fact 无审批、未知端点/规则一律 fail closed（不落库、不伪造通过）；
- `_resolve_world_model_version` 在事件表空时 fallback 到实体表/知识表。

沿用 test_wmc_knowledge_materializer 的 fixture 模式（db_session + SimpleNamespace run）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.analysis import AnalysisVersion
from app.models.novel import Chapter, Novel
from app.models.timeline import TimelineActivePointer
from app.models.user import User
from app.models.world_model_entity import (
    WorldModelEntity,
    WorldModelEntityLink,
    WorldModelRule,
    WorldModelRuleException,
)
from app.models.world_model_event import WorldModelCausalEdge, WorldModelEvent
from app.models.world_model_knowledge import WorldModelKnowledge
from app.services.agent_runtime import materializers
from app.services.agent_runtime.materializers import _materialize_world_model_knowledge

pytestmark = pytest.mark.unit

H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
H4 = "d" * 64


def _uid() -> str:
    """每次 seed 用唯一用户名/邮箱，避免跨测试唯一约束冲突。"""
    return uuid.uuid4().hex[:12]


def _qp_key(chapter_id: int, start: int = 0, end: int = 100) -> str:
    return f"qp:{chapter_id}:{start}:{end}:{'b' * 64}"


async def _seed_full(session: AsyncSession):
    """用户/小说(授权到第2章)/两章/分析版本/活动指针，使完整 handler 可跑。"""
    uid = _uid()
    owner = User(
        username=f"wmc_evt_{uid}",
        email=f"wmc_evt_{uid}@example.com",
        hashed_password=hash_password("pass12345"),
    )
    session.add(owner)
    await session.flush()
    novel = Novel(
        title="WMC Event Novel",
        owner_id=owner.id,
        status="ready",
        reading_progress={},  # 先空，下面用 ch2.id 覆盖
        chapter_count=2,
        word_count=200,
    )
    session.add(novel)
    await session.flush()
    ch1 = Chapter(novel_id=novel.id, chapter_number=1, title="一", content="甲" * 200)
    ch2 = Chapter(novel_id=novel.id, chapter_number=2, title="二", content="乙" * 200)
    session.add(ch1)
    session.add(ch2)
    await session.flush()
    # 授权 cutoff = 第 2 章（事件/证据在第 1 章，≤ cutoff 通过）。
    novel.reading_progress = {"chapter_id": ch2.id}
    await session.flush()

    av = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="v1",
        source_snapshot_hash=H,
        hierarchy_build_id=H2,
        hierarchy_checksum=H3,
        prompt_hash=H,
        schema_hash=H2,
        decoding_hash=H3,
        config_hash=H,
        model_lineage={},
        price_snapshot={},
        manifest={},
    )
    session.add(av)
    await session.flush()
    session.add(
        TimelineActivePointer(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=av.id,
            revision=1,
            manifest_checksum=H,
        )
    )
    await session.flush()
    return owner, novel, ch1, ch2


async def _seed_shared(session: AsyncSession):
    """仅用户+小说（版本 fallback 测试不需要章节）。"""
    uid = _uid()
    owner = User(
        username=f"wmc_fb_{uid}",
        email=f"wmc_fb_{uid}@example.com",
        hashed_password=hash_password("pass12345"),
    )
    session.add(owner)
    await session.flush()
    novel = Novel(
        title="WMC Fallback Novel",
        owner_id=owner.id,
        status="ready",
        reading_progress={},
        chapter_count=1,
        word_count=10,
    )
    session.add(novel)
    await session.flush()
    return owner, novel


def _run(owner, novel) -> SimpleNamespace:
    return SimpleNamespace(owner_id=owner.id, novel_id=novel.id)


def _claims(*claims: dict) -> dict:
    return {"candidates": {"claims": list(claims)}}


# ═══════════════════════════════════════════════════════════════════════════
# 1) event 全字段合格 → ok，事件表落 1 行
# ═══════════════════════════════════════════════════════════════════════════


async def test_event_full_materializes(db_session: AsyncSession):
    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "event",
            "claim_key": "prophecy_revealed",
            "title": "预言现世",
            "description": "林安在剑冢触发了古老预言。",
            "authority": "probable_inference",
            "confidence": 0.7,
            "effective": {"start": 1, "end": 1},
            "disclosure_cutoff": 1,
            "evidence_refs": [_qp_key(ch1.id)],
            "details": {},
        }
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    assert result == "ok"
    count = await db_session.scalar(
        select(func.count())
        .select_from(WorldModelEvent)
        .where(WorldModelEvent.owner_id == owner.id, WorldModelEvent.novel_id == novel.id)
    )
    assert count == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2) causal_edge 指向本批次 event → ok；无独立 evidence → co_occurrence_only
#    边缘不落库（并直接验证 gate 原因码）。
# ═══════════════════════════════════════════════════════════════════════════


async def test_edge_needs_independent_evidence(db_session: AsyncSession):
    from app.services.world_model.claims import (
        CausalEdgeClaim,
        EffectiveInterval,
        EventClaim,
    )
    from app.services.world_model.contracts import (
        Authority,
        CausalEdgeType,
        EvidenceRef,
    )
    from app.services.world_model.gates import WorldModelGate

    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "event",
            "claim_key": "ev1",
            "title": "t",
            "description": "d",
            "authority": "probable_inference",
            "confidence": 0.6,
            "effective": {"start": 1, "end": 1},
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "causal_edge",
            "claim_key": "ed1",
            "source_event_key": "ev1",
            "target_event_key": "ev1",
            "edge_type": "caused",
            "authority": "probable_inference",
            "confidence": 0.5,
            "disclosure_cutoff": 1,
            "evidence_refs": [],  # 空：交给 gate 以 co_occurrence_only 拒绝
        },
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    # event 落库 → ok；边因 co_occurrence_only 不落库。
    assert result == "ok"
    evt = await db_session.scalar(
        select(func.count())
        .select_from(WorldModelEvent)
        .where(WorldModelEvent.owner_id == owner.id, WorldModelEvent.novel_id == novel.id)
    )
    edge = await db_session.scalar(
        select(func.count()).select_from(WorldModelCausalEdge).where(
            WorldModelCausalEdge.owner_id == owner.id,
            WorldModelCausalEdge.novel_id == novel.id,
        )
    )
    assert evt == 1 and edge == 0

    # 直接验证 gate 原因码确为 co_occurrence_only。
    ev = EvidenceRef(
        evidence_id="e", chapter_id=ch1.id, chapter_number=1,
        source_start=0, source_end=10, content_hash=H, source_snapshot_hash=H,
    )
    gate = WorldModelGate(
        owner_id=owner.id, novel_id=novel.id, version_id=1,
        source_snapshot_hash=H, disclosure_cutoff=2, approvals=frozenset(),
    )
    ec = EventClaim(
        event_key="ev1", title="t", description="d",
        authority=Authority.PROBABLE_INFERENCE, confidence=0.6,
        effective=EffectiveInterval(start=1, end=1), disclosure_cutoff=2,
        source_refs=(ev,), owner_id=owner.id, novel_id=novel.id, version_id=1,
    )
    fact = gate.validate_event(ec).fact
    edge_claim = CausalEdgeClaim(
        edge_key="ed1", source_event_key="ev1", target_event_key="ev1",
        edge_type=CausalEdgeType.CAUSED, authority=Authority.PROBABLE_INFERENCE,
        confidence=0.5, disclosure_cutoff=2, source_refs=(),
        owner_id=owner.id, novel_id=novel.id, version_id=1,
    )
    dr = gate.validate_edge(edge_claim, {fact.event_key: fact})
    assert dr.edge is None
    assert any(str(v.reason_code) == "co_occurrence_only" for v in dr.verdicts)


# ═══════════════════════════════════════════════════════════════════════════
# 3) causal_edge 指向不存在 event → skipped 含 unknown_endpoint（无落库）
# ═══════════════════════════════════════════════════════════════════════════


async def test_edge_unknown_endpoint_skipped(db_session: AsyncSession):
    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "causal_edge",
            "claim_key": "ed_x",
            "source_event_key": "ghost",
            "target_event_key": "ghost2",
            "edge_type": "caused",
            "authority": "probable_inference",
            "confidence": 0.5,
            "disclosure_cutoff": 1,
            "evidence_refs": [_qp_key(ch1.id)],
        }
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    assert "skipped" in result and "unknown_endpoint" in result
    edge = await db_session.scalar(
        select(func.count()).select_from(WorldModelCausalEdge).where(
            WorldModelCausalEdge.owner_id == owner.id,
            WorldModelCausalEdge.novel_id == novel.id,
        )
    )
    assert edge == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4) world_rule → ok；rule_exception 指向本批次 rule → ok；
#    指向未知 rule → skipped（不落库）
# ═══════════════════════════════════════════════════════════════════════════


async def test_rule_and_exception(db_session: AsyncSession):
    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "world_rule",
            "claim_key": "rule_magic_cost",
            "rule_name": "magic_cost",
            "proposition": "施法消耗寿命。",
            "authority": "probable_inference",
            "confidence": 0.6,
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "rule_exception",
            "claim_key": "exc_royal",
            "rule_key": "rule_magic_cost",  # 指向本批次 rule
            "proposition": "王室血脉豁免。",
            "authority": "probable_inference",
            "confidence": 0.5,
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "rule_exception",
            "claim_key": "exc_ghost",
            "rule_key": "rule_nonexistent",  # 未知规则
            "proposition": "幽灵不受限。",
            "authority": "probable_inference",
            "confidence": 0.5,
            "evidence_refs": [_qp_key(ch1.id)],
        },
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    assert result == "ok"
    rules = await db_session.scalar(
        select(func.count()).select_from(WorldModelRule).where(
            WorldModelRule.owner_id == owner.id, WorldModelRule.novel_id == novel.id
        )
    )
    excs = await db_session.scalar(
        select(func.count()).select_from(WorldModelRuleException).where(
            WorldModelRuleException.owner_id == owner.id,
            WorldModelRuleException.novel_id == novel.id,
        )
    )
    assert rules == 1
    # 只有指向本批次 rule 的例外落库；未知规则的例外被拒绝。
    assert excs == 1
    ghost = await db_session.scalar(
        select(func.count())
        .select_from(WorldModelRuleException)
        .where(WorldModelRuleException.exception_key == "exc_ghost")
    )
    assert ghost == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5) entity → ok；entity_link 指向本批次 entity → ok；指向未知实体 → skipped
# ═══════════════════════════════════════════════════════════════════════════


async def test_entity_and_link(db_session: AsyncSession):
    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "entity",
            "claim_key": "ent_hero",
            "entity_type": "entity",
            "primary_name": "林安",
            "proposition": "主角林安。",
            "authority": "probable_inference",
            "confidence": 0.6,
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "entity",
            "claim_key": "ent_guild",
            "entity_type": "faction",
            "primary_name": "炼金行会",
            "proposition": "炼金行会掌控北方贸易。",
            "authority": "probable_inference",
            "confidence": 0.6,
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "entity_link",
            "claim_key": "link_member",
            "link_kind": "member_of",
            "source_key": "ent_hero",
            "target_key": "ent_guild",  # 两端均指向本批次 entity
            "authority": "probable_inference",
            "confidence": 0.5,
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "entity_link",
            "claim_key": "link_ghost",
            "link_kind": "member_of",
            "source_key": "ent_ghost",
            "target_key": "ent_guild",  # 源未知
            "authority": "probable_inference",
            "confidence": 0.5,
            "evidence_refs": [_qp_key(ch1.id)],
        },
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    assert result == "ok"
    entities = await db_session.scalar(
        select(func.count()).select_from(WorldModelEntity).where(
            WorldModelEntity.owner_id == owner.id,
            WorldModelEntity.novel_id == novel.id,
        )
    )
    links = await db_session.scalar(
        select(func.count()).select_from(WorldModelEntityLink).where(
            WorldModelEntityLink.owner_id == owner.id,
            WorldModelEntityLink.novel_id == novel.id,
        )
    )
    assert entities == 2
    assert links == 1  # 仅 link_member 落库（link_ghost 源未知被拒）
    ghost = await db_session.scalar(
        select(func.count())
        .select_from(WorldModelEntityLink)
        .where(WorldModelEntityLink.link_key == "link_ghost")
    )
    assert ghost == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6) canon_fact 无审批 → skipped 含 authority_upgrade（不落库）
# ═══════════════════════════════════════════════════════════════════════════


async def test_canon_fact_authority_rejected(db_session: AsyncSession):
    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "event",
            "claim_key": "ev_canon",
            "title": "t",
            "description": "d",
            "authority": "canon_fact",  # backfill 无审批通道 → 拒
            "confidence": 0.9,
            "effective": {"start": 1, "end": 1},
            "evidence_refs": [_qp_key(ch1.id)],
        }
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    assert "skipped" in result and "authority_upgrade" in result
    evt = await db_session.scalar(
        select(func.count()).select_from(WorldModelEvent).where(
            WorldModelEvent.owner_id == owner.id, WorldModelEvent.novel_id == novel.id
        )
    )
    assert evt == 0


# ═══════════════════════════════════════════════════════════════════════════
# 7) character_knowledge 混合 event → 知识表与事件表各落库（回归 run 133 路径）
# ═══════════════════════════════════════════════════════════════════════════


async def test_knowledge_and_event_mixed(db_session: AsyncSession):
    owner, novel, ch1, _ = await _seed_full(db_session)
    content = _claims(
        {
            "claim_kind": "event",
            "claim_key": "ev_m",
            "title": "t",
            "description": "d",
            "authority": "probable_inference",
            "confidence": 0.6,
            "effective": {"start": 1, "end": 1},
            "evidence_refs": [_qp_key(ch1.id)],
        },
        {
            "claim_kind": "character_knowledge",
            "claim_key": "ck_m",
            "subject": "林安",
            "proposition": "林安知晓预言。",
            "authority": "probable_inference",
            "confidence": 0.6,
            "disclosure_cutoff": 1,
            "evidence_refs": [_qp_key(ch1.id)],
        },
    )
    result = await _materialize_world_model_knowledge(
        db_session, run=_run(owner, novel), content=content
    )
    assert result == "ok"
    evt = await db_session.scalar(
        select(func.count()).select_from(WorldModelEvent).where(
            WorldModelEvent.owner_id == owner.id, WorldModelEvent.novel_id == novel.id
        )
    )
    # 知识表落库通过 KnowledgeRepository；用 canonical_payload 检索。
    know = await db_session.scalar(
        select(func.count()).select_from(WorldModelKnowledge).where(
            WorldModelKnowledge.owner_id == owner.id,
            WorldModelKnowledge.novel_id == novel.id,
        )
    )
    assert evt == 1 and know == 1


# ═══════════════════════════════════════════════════════════════════════════
# 8) _resolve_world_model_version fallback：事件表空但知识表有版本
# ═══════════════════════════════════════════════════════════════════════════


def _insert_knowledge(session, owner, novel, vid: int):
    session.add(
        WorldModelKnowledge(
            knowledge_key="k", subject="s", aspect="knowledge", known_at=1,
            disclosure_cutoff=1, pov="x", pov_kind="omniscient",
            source_kind="canon_source", authority="canon_fact", confidence=0.5,
            epistemic_status="candidate", lineage=[], source_refs=[],
            gate_status="passed", owner_id=owner.id, novel_id=novel.id,
            version_id=vid, canonical_payload={}, canonical_payload_hash=H,
            idempotency_key=H2, projection_hash=H,
            schema_version="world-model-knowledge.v1",
        )
    )


def _insert_event(session, owner, novel, vid: int):
    session.add(
        WorldModelEvent(
            event_key="e", owner_id=owner.id, novel_id=novel.id, version_id=vid,
            authority="probable_inference", confidence=0.5, effective_start=1,
            effective_end=1, disclosure_cutoff=1, gate_status="passed",
            source_refs=[], canonical_payload={}, canonical_payload_hash=H,
            idempotency_key=H3, projection_hash=H,
            schema_version="world-model-event.v1",
        )
    )


def _insert_entity(session, owner, novel, vid: int):
    session.add(
        WorldModelEntity(
            entity_key="en", entity_type="entity", disclosure_cutoff=1,
            source_kind="canon_source", authority="probable_inference",
            confidence=0.5, gate_status="passed", source_refs=[], aliases=[],
            lineage=["en"], owner_id=owner.id, novel_id=novel.id, version_id=vid,
            canonical_payload={}, canonical_payload_hash=H, idempotency_key=H4,
            projection_hash=H, schema_version="world-model-entity.v1",
        )
    )


async def test_resolve_version_falls_back_to_knowledge(db_session: AsyncSession):
    from app.services.agent_tools._defaults_world import _resolve_world_model_version
    from app.services.agent_tools.errors import NotFoundError

    owner, novel = await _seed_shared(db_session)
    _insert_knowledge(db_session, owner, novel, 5)
    await db_session.flush()
    v = await _resolve_world_model_version(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=None
    )
    assert v == 5

    # 全空 → NotFoundError
    owner2, novel2 = await _seed_shared(db_session)
    await db_session.flush()
    with pytest.raises(NotFoundError):
        await _resolve_world_model_version(
            db_session, owner_id=owner2.id, novel_id=novel2.id, version_id=None
        )


async def test_resolve_version_event_preferred(db_session: AsyncSession):
    from app.services.agent_tools._defaults_world import _resolve_world_model_version

    owner, novel = await _seed_shared(db_session)
    _insert_event(db_session, owner, novel, 3)
    _insert_knowledge(db_session, owner, novel, 5)
    await db_session.flush()
    v = await _resolve_world_model_version(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=None
    )
    assert v == 3  # 事件表优先于知识表


async def test_resolve_version_entity_before_knowledge(db_session: AsyncSession):
    from app.services.agent_tools._defaults_world import _resolve_world_model_version

    owner, novel = await _seed_shared(db_session)
    _insert_entity(db_session, owner, novel, 7)
    _insert_knowledge(db_session, owner, novel, 5)
    await db_session.flush()
    v = await _resolve_world_model_version(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=None
    )
    assert v == 7  # 实体表优先于知识表


# 防止 select/func 未使用告警（保持与生产代码相同的导入面）。
_ = (select, func, materializers)
