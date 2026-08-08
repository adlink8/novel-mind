"""Visible-set-first evidence retrieval and Phase 09 read-only consumer contract.

Phase 10 never imports Phase 09 ORM models. Relationship observations arrive only
through :class:`RelationshipObservationReader`, bound to the completed Phase 09
public API (``load_filtered_relationship_graph``). Runtime outages are explicit
source statuses; missing contracts are execution failures, not null adapters.

拆分说明（refactor split）：本模块保留为门面 —— 全部顶层符号从 3 个按职责
域拆分的同目录模块显式 re-export，``from app.services.reader_chat.retrieval
import X`` 的 import surface 完全不变：

- ``retrieval_types``：契约类型层（``SOURCE_PRIORITY`` / ``SourceStatus`` /
  ``RelationshipObservationReader`` Protocol / ``RetrievedEvidence`` /
  ``RetrievalResult`` dataclass / bound、overlap 工具，叶模块，无 DB 依赖）；
- ``retrieval_sources``：4 个来源 fetch 器（hierarchy / timeline /
  relationship / knowledge）+ ``Phase09RelationshipObservationReader`` +
  active pointer 解析（``resolve_active_hierarchy`` /
  ``resolve_active_analysis_version``）；
- ``retrieval_snapshot``：可见性合并（``retrieve_visible_evidence``）与
  QueryPlan 快照层（``build_source_snapshot`` / ``_snapshot_hash`` /
  ``chat_retrieval_dimension_results``）。

注意：``SOURCE_PRIORITY`` 是**单例 dict 对象**，唯一定义在
``retrieval_types``，此处经 import re-export（引用拷贝），绝不能重新定义
——``app.services.retrieval_policy.READER_CHAT_SOURCE_PRIORITY`` 与它必须是
同一个对象（contract test 断言身份）。
"""

from __future__ import annotations

from .retrieval_snapshot import (
    _snapshot_hash,
    build_source_snapshot,
    chat_retrieval_dimension_results,
    retrieve_visible_evidence,
)
from .retrieval_sources import (
    Phase09RelationshipObservationReader,
    fetch_hierarchy_evidence,
    fetch_knowledge_evidence,
    fetch_relationship_evidence,
    fetch_timeline_evidence,
    resolve_active_analysis_version,
    resolve_active_hierarchy,
)
from .retrieval_types import (
    DEFAULT_MAX_EVIDENCE,
    DEFAULT_MAX_EXCERPT_CODE_POINTS,
    DEFAULT_MAX_PER_SOURCE,
    SOURCE_PRIORITY,
    RelationshipObservationEvidence,
    RelationshipObservationItem,
    RelationshipObservationReader,
    RetrievedEvidence,
    RetrievalResult,
    SourceStatus,
    bound_excerpt,
    code_point_len_local,
    code_point_slice_local,
    overlaps,
    revalidate_observation_item,
)

__all__ = [
    "SOURCE_PRIORITY",
    "DEFAULT_MAX_EVIDENCE",
    "DEFAULT_MAX_EXCERPT_CODE_POINTS",
    "DEFAULT_MAX_PER_SOURCE",
    "SourceStatus",
    "RelationshipObservationEvidence",
    "RelationshipObservationItem",
    "RelationshipObservationReader",
    "revalidate_observation_item",
    "RetrievedEvidence",
    "RetrievalResult",
    "bound_excerpt",
    "code_point_len_local",
    "code_point_slice_local",
    "overlaps",
    "resolve_active_hierarchy",
    "resolve_active_analysis_version",
    "fetch_hierarchy_evidence",
    "fetch_timeline_evidence",
    "fetch_relationship_evidence",
    "Phase09RelationshipObservationReader",
    "fetch_knowledge_evidence",
    "retrieve_visible_evidence",
    "_snapshot_hash",
    "build_source_snapshot",
    "chat_retrieval_dimension_results",
]
