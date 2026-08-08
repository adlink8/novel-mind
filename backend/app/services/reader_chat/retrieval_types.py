"""Reader Chat retrieval contract types and evidence projection helpers.

叶模块（无 DB 依赖、不 import 任何 reader_chat 同层模块）：承载检索契约的
数据类型层 —— ``SOURCE_PRIORITY`` 优先级常量、``SourceStatus`` 枚举、
``RelationshipObservationReader`` Protocol、``RetrievedEvidence`` /
``RetrievalResult`` / ``RelationshipObservationItem`` dataclass，以及
bound/overlap 等纯函数工具。

拆分说明（refactor split）：原 ``retrieval.py`` 按职责域拆为
``retrieval_types`` / ``retrieval_sources`` / ``retrieval_snapshot`` 三模块，
``retrieval.py`` 保留为门面并显式 re-export 全部顶层符号。注意
``SOURCE_PRIORITY`` 是**单例 dict 对象**：``app.services.retrieval_policy``
的 ``READER_CHAT_SOURCE_PRIORITY`` 与它必须是同一对象（contract test 断言
身份），本模块是它的唯一定义处，任何其他模块不得重新定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel

# Priority ranks used when packing the immutable context manifest (AI-SPEC §7).
SOURCE_PRIORITY: dict[str, int] = {
    "selection": 0,
    "hierarchy": 1,
    "knowledge": 2,
    "timeline": 3,
    "relationship_observation": 4,
}

DEFAULT_MAX_EVIDENCE = 24
DEFAULT_MAX_EXCERPT_CODE_POINTS = 700
DEFAULT_MAX_PER_SOURCE = 8


class SourceStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ABSENT = "absent"


@dataclass(frozen=True)
class RelationshipObservationEvidence:
    evidence_id: str
    chapter_id: int
    source_start: int
    source_end: int
    content_hash: str
    chapter_number: int | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class RelationshipObservationItem:
    """Strict D-10 consumer DTO: versioned, evidence-bound, spoiler-filtered."""

    observation_id: int
    analysis_version_id: int
    owner_id: int
    novel_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    valid_from_chapter: int
    valid_to_chapter: int | None
    status: str
    evidence: tuple[RelationshipObservationEvidence, ...]
    confidence: float = 0.0

    def version_lineage(self) -> dict[str, Any]:
        return {
            "analysis_version_id": self.analysis_version_id,
            "observation_id": self.observation_id,
            "relation_type": self.relation_type,
            "valid_from_chapter": self.valid_from_chapter,
            "valid_to_chapter": self.valid_to_chapter,
        }


@runtime_checkable
class RelationshipObservationReader(Protocol):
    """Read-only Phase 09 consumer. Implementations must not write domain facts."""

    async def list_visible_observations(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        version_id: int,
        through_chapter: int | None,
        request_full_book: bool = False,
    ) -> list[RelationshipObservationItem]:
        """Return accepted, version/evidence/spoiler-scoped observations only."""
        ...


def revalidate_observation_item(
    item: RelationshipObservationItem,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff_chapter: int | None,
    full_book: bool,
) -> RelationshipObservationItem | None:
    """Drop observations that fail owner/version/status/spoiler revalidation."""

    if item.owner_id != owner_id or item.novel_id != novel_id:
        return None
    if item.analysis_version_id != version_id:
        return None
    if item.status != "accepted":
        return None
    if item.source_character_id == item.target_character_id:
        return None
    if not item.evidence:
        return None
    if not full_book and cutoff_chapter is not None:
        if item.valid_from_chapter > cutoff_chapter:
            return None
        if item.valid_to_chapter is not None and item.valid_to_chapter < 1:
            return None
    for ev in item.evidence:
        if ev.source_end <= ev.source_start or ev.source_start < 0:
            return None
        if len(ev.content_hash) != 64:
            return None
    return item


@dataclass
class RetrievedEvidence:
    evidence_key: str
    source_type: str
    source_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str
    version_lineage: dict[str, Any] = field(default_factory=dict)
    priority: int = 99
    rank_key: tuple = ()


@dataclass
class RetrievalResult:
    items: list[RetrievedEvidence]
    omitted_counts: dict[str, int]
    source_status: dict[str, str]
    hierarchy_build_id: str
    hierarchy_checksum: str
    analysis_version_id: int | None


def bound_excerpt(
    text: str, max_code_points: int = DEFAULT_MAX_EXCERPT_CODE_POINTS
) -> str:
    if code_point_len_local(text) <= max_code_points:
        return text
    return code_point_slice_local(text, 0, max_code_points - 1) + "…"


def code_point_len_local(text: str) -> int:
    return len(text)


def code_point_slice_local(text: str, start: int, end: int) -> str:
    return text[start:end]


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end
