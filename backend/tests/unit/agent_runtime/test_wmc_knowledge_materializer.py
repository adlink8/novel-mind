"""knowledge materializer 的 `_map_epistemic_claim` 映射契约（run 126 回归）。

失败背景（2026-09-07 run 126）：模型输出的 claim 缺 `subject`，且把
SKILL 示例的字面 `disclosure_cutoff: 1` 原样照抄——materializer 要求
`known_at(章号) <= disclosure_cutoff` 被整条拒绝 → skipped:unmapped。

修复契约：
1. `disclosure_cutoff` 由程序注入（授权 cutoff），模型值不参与裁决——
   血缘锚定字段永不信任模型（与 guided 模式哲学一致）。
2. `subject` 缺失仍然 fail closed（语义字段，程序无法替模型发明）。
3. 证据章节超出授权 cutoff 的 claim 仍然拒绝（越权披露防线不变）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.agent_runtime.materializers import _map_epistemic_claim

pytestmark = pytest.mark.unit

SNAPSHOT = "a" * 64


def _qp_key(chapter_id: int, start: int = 0, end: int = 100) -> str:
    return f"qp:{chapter_id}:{start}:{end}:{'b' * 64}"


def _raw_claim(chapter_id: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "claim_kind": "character_knowledge",
        "claim_key": "hero_knows_prophecy",
        "subject": "林安",
        "proposition": "林安知晓了剑冢的预言。",
        "authority": "canon_fact",
        "confidence": 0.8,
        "disclosure_cutoff": 1,  # 模型照抄示例的字面值（历史坑）
        "evidence_refs": [_qp_key(chapter_id)],
        "details": {},
    }
    base.update(overrides)
    return base


async def _seed(session: AsyncSession) -> tuple[User, Novel, Chapter, Chapter]:
    owner = User(
        username="wmc_owner",
        email="wmc_owner@example.com",
        hashed_password=hash_password("pass12345"),
    )
    session.add(owner)
    await session.flush()
    novel = Novel(
        title="WMC Novel",
        owner_id=owner.id,
        status="ready",
        reading_progress={},
        chapter_count=2,
        word_count=100,
    )
    session.add(novel)
    await session.flush()
    ch1 = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content="甲" * 200
    )
    ch2 = Chapter(
        novel_id=novel.id, chapter_number=2, title="第二章", content="乙" * 200
    )
    session.add(ch1)
    session.add(ch2)
    await session.flush()
    return owner, novel, ch1, ch2


async def test_model_garbage_cutoff_is_program_injected(db_session: AsyncSession):
    """run 126 回归：模型 cutoff=1（照抄示例）也必须物化成功。"""
    owner, novel, ch1, _ = await _seed(db_session)
    run = SimpleNamespace(owner_id=owner.id, novel_id=novel.id)
    mapped = await _map_epistemic_claim(
        db_session,
        run=run,
        version_id=1,
        snapshot_hash=SNAPSHOT,
        cutoff=2,  # 授权 cutoff = 全书 2 章
        raw=_raw_claim(ch1.id),
    )
    assert mapped is not None
    assert mapped.disclosure_cutoff == 2  # 程序注入的授权值
    assert mapped.known_at == 1
    assert mapped.subject == "林安"
    assert mapped.aspect.value == "knowledge" if hasattr(mapped.aspect, "value") else True


async def test_missing_subject_fails_closed(db_session: AsyncSession):
    """subject 是语义字段，缺了必须整条拒绝（不能由程序编造）。"""
    owner, novel, ch1, _ = await _seed(db_session)
    run = SimpleNamespace(owner_id=owner.id, novel_id=novel.id)
    mapped = await _map_epistemic_claim(
        db_session,
        run=run,
        version_id=1,
        snapshot_hash=SNAPSHOT,
        cutoff=2,
        raw=_raw_claim(ch1.id, subject=None),
    )
    assert mapped is None


async def test_non_knowledge_claim_kind_rejected(db_session: AsyncSession):
    """world_rule 等 claim_kind 不属 knowledge 表，返回 None。"""
    owner, novel, ch1, _ = await _seed(db_session)
    run = SimpleNamespace(owner_id=owner.id, novel_id=novel.id)
    mapped = await _map_epistemic_claim(
        db_session,
        run=run,
        version_id=1,
        snapshot_hash=SNAPSHOT,
        cutoff=2,
        raw=_raw_claim(ch1.id, claim_kind="world_rule"),
    )
    assert mapped is None


async def test_evidence_beyond_cutoff_rejected(db_session: AsyncSession):
    """证据章节号超出授权 cutoff → 拒绝（越权披露防线不变）。"""
    owner, novel, _, ch2 = await _seed(db_session)
    run = SimpleNamespace(owner_id=owner.id, novel_id=novel.id)
    mapped = await _map_epistemic_claim(
        db_session,
        run=run,
        version_id=1,
        snapshot_hash=SNAPSHOT,
        cutoff=1,  # 只授权到第 1 章
        raw=_raw_claim(ch2.id),  # 证据在第 2 章
    )
    assert mapped is None


async def test_unknown_chapter_in_ref_rejected(db_session: AsyncSession):
    """evidence_refs 指向不存在/跨 novel 的 chapter_id → 拒绝。"""
    owner, novel, _, _ = await _seed(db_session)
    run = SimpleNamespace(owner_id=owner.id, novel_id=novel.id)
    mapped = await _map_epistemic_claim(
        db_session,
        run=run,
        version_id=1,
        snapshot_hash=SNAPSHOT,
        cutoff=2,
        raw=_raw_claim(999999),
    )
    assert mapped is None


def owner_id_of(owner: User) -> int:
    return owner.id


# 防止 select 未使用告警（保持与生产代码相同的导入面）。
_ = select
