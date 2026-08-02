"""产物持久化与状态迁移门禁（25.2-03 / D-10 / T-25.2-03-03）。

规则:
  - 不可变修订：uq(artifact_id, revision_no)、content_hash、parent_revision_id、
    evidence_refs JSONB。修订只追加，不改写。
  - 状态迁移 candidate→validated→approved→published 仅前进，外加 rejected；
    任何迁移**只能**经由本模块的 service 函数发生（approval API 是唯一入口）。
  - owner 校验在迁移函数内强制：非 owner 无法改状态（artifact 状态伪造威胁）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import Artifact, ArtifactRevision

# 仅允许的迁移表：前向 + rejected 分支（published/rejected 为终态）。
_ARTIFACT_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"validated", "rejected"}),
    "validated": frozenset({"approved", "rejected"}),
    "approved": frozenset({"published"}),
    "published": frozenset(),
    "rejected": frozenset(),
}


class ArtifactStateError(RuntimeError):
    """非法状态迁移 / 非 owner 操作被拒绝。"""


def content_hash_of(content: dict[str, Any]) -> str:
    """对修订内容做规范化序列化并求 SHA-256（String(64)）。"""
    canonical = json.dumps(
        content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def create_artifact_with_first_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    run_id: int,
    skill_version_id: int,
    branch: str | None,
    artifact_type: str,
    schema_version: str,
    model_lineage: dict[str, Any],
    source_versions: dict[str, Any],
    input_hash: str,
    content: dict[str, Any],
    evidence_refs: list[str],
) -> tuple[Artifact, ArtifactRevision]:
    """finalize 唯一调用入口：写入 candidate 产物 + 首个不可变修订。"""
    artifact = Artifact(
        owner_id=owner_id,
        novel_id=novel_id,
        skill_version_id=skill_version_id,
        run_id=run_id,
        branch=branch,
        type=artifact_type,
        schema_version=schema_version,
        status="candidate",
        model_lineage=model_lineage,
        source_versions=source_versions,
        input_hash=input_hash,
        current_revision_id=None,
    )
    db.add(artifact)
    await db.flush()

    revision = ArtifactRevision(
        artifact_id=artifact.id,
        owner_id=owner_id,
        novel_id=novel_id,
        revision_no=1,
        content_hash=content_hash_of(content),
        parent_revision_id=None,
        evidence_refs=list(evidence_refs),
        content=content,
    )
    db.add(revision)
    await db.flush()
    artifact.current_revision_id = revision.id
    return artifact, revision


async def transition_artifact_status(
    db: AsyncSession,
    *,
    artifact_id: int,
    owner_id: int,
    to_status: str,
) -> Artifact:
    """唯一的状态变更路径（approval API 调用；owner 检查强制）。

    不允许的迁移或非 owner → ArtifactStateError。
    """
    artifact = await db.scalar(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.owner_id == owner_id)
    )
    if artifact is None:
        raise ArtifactStateError("artifact not found or not owned by caller")
    allowed = _ARTIFACT_TRANSITIONS.get(artifact.status, frozenset())
    if to_status not in allowed:
        raise ArtifactStateError(
            f"illegal artifact status transition "
            f"{artifact.status!r} -> {to_status!r} (forward-only + rejected)"
        )
    artifact.status = to_status
    await db.flush()
    return artifact


async def get_artifact(
    db: AsyncSession,
    *,
    artifact_id: int,
    owner_id: int,
    novel_id: int,
) -> Artifact | None:
    """按 owner+novel 读取产物（404-hide 由调用方处理）。"""
    return await db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_id == owner_id,
            Artifact.novel_id == novel_id,
        )
    )


async def get_artifact_for_owner(
    db: AsyncSession,
    *,
    artifact_id: int,
    owner_id: int,
) -> Artifact | None:
    """按 owner 读取产物（approve/reject 等非小说域路由用）。"""
    return await db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_id == owner_id,
        )
    )


async def list_artifacts(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[Artifact], int]:
    """分页列出某小说的产物（{"items","total","skip","limit"}）。"""
    where = (Artifact.owner_id == owner_id, Artifact.novel_id == novel_id)
    total = await db.scalar(select(func.count()).select_from(Artifact).where(*where))
    rows = list(
        (
            await db.scalars(
                select(Artifact)
                .where(*where)
                .order_by(Artifact.id.desc())
                .offset(skip)
                .limit(limit)
            )
        ).all()
    )
    return rows, int(total or 0)


async def list_artifact_revisions(
    db: AsyncSession,
    *,
    artifact_id: int,
    owner_id: int,
    novel_id: int,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[ArtifactRevision], int]:
    """分页列出产物的不可变修订（血缘链升序）。"""
    where = (
        ArtifactRevision.artifact_id == artifact_id,
        ArtifactRevision.owner_id == owner_id,
        ArtifactRevision.novel_id == novel_id,
    )
    total = await db.scalar(
        select(func.count()).select_from(ArtifactRevision).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(ArtifactRevision)
                .where(*where)
                .order_by(ArtifactRevision.revision_no)
                .offset(skip)
                .limit(limit)
            )
        ).all()
    )
    return rows, int(total or 0)
