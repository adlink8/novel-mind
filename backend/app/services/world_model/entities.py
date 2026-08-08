"""Immutable typed contracts and gates for world entities (REQ-WM-03).

Phase 27-03. Typed candidates for entity / faction / place / item, plus the
entity links that express membership (``member_of`` / ``allegiance``),
ownership (``owns`` / ``controls``) and spatial / item state (``located_in`` /
``carried_by``). Semantics locked by decisions D-01..D-06:

- D-01: ``Authority`` keeps the four distinct labels; the gate rejects any
  attempt to silently upgrade an inference or interpretation into ``canon_fact``
  unless explicitly approved.
- D-02: The durable output is a versioned immutable candidate set. There is no
  active-pointer / promotion / current-revision field and no promotion API.
- D-03: Every entity, alias, link, rule, rule exception and alias review carries
  owner/novel/version/cutoff, source EvidenceRefs, authority, confidence and gate
  status. ``lineage`` keeps the version chain of a logical entity.
- D-04: Rule exceptions are first-class records (see ``rules.py``); alias
  collisions are never silently merged. Alias similarity produces only
  ``AliasCollisionReview`` candidates with ``status == REVIEW``.
- D-06: Reader Chat / user conversation is never a world-model fact source; the
  gate rejects such claims on any authority.

Only the immutable candidate projection (``EntityCandidateProjection``) crosses
the persistence seam.

拆分说明（refactor split）：hashing/checksum 原语下沉到叶模块
``entity_primitives.py``；类型化实体/链接/别名/claim 模型拆到
``_entity_models.py``；实体门（``EntityGate``）拆到 ``_entity_gate.py``；
持久化投影 + 别名碰撞检测 + 纯内存查询引擎拆到 ``_entity_projection.py``。
本文件成为 facade，re-export 全部原顶层符号 ——
``app.services.world_model.entities`` 的 public import surface 不变。
"""

from __future__ import annotations

import hashlib  # noqa: F401  # facade surface parity (moved to entity_primitives)
import json  # noqa: F401  # facade surface parity (moved to entity_primitives)
import re  # noqa: F401  # facade surface parity (moved to _entity_projection)
from dataclasses import dataclass  # noqa: F401  # facade surface parity (moved to _entity_gate)
from difflib import SequenceMatcher  # noqa: F401  # facade surface parity (moved to _entity_projection)
from enum import StrEnum  # noqa: F401  # facade surface parity (moved to _entity_models)
from typing import Annotated, Any, Iterable  # noqa: F401  # facade surface parity

from pydantic import (  # noqa: F401  # facade surface parity (moved to _entity_models)
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.services.world_model.contracts import (  # noqa: F401  # facade surface parity
    Authority,
    Description,
    EvidenceRef,
    GateStatus,
    Key,
    PositiveInt,
    StrictModel,
)
from app.services.world_model.rules import (  # noqa: F401  # facade surface parity
    GateReason,
    RuleException,
    SourceKind,
    WorldRule,
    exception_checksum,
    rule_checksum,
)

from .entity_primitives import (  # noqa: F401  # facade surface parity
    ALIAS_COLLISION_THRESHOLD,
    ENTITY_HASH_ALIAS_REVIEW,
    ENTITY_HASH_ENTITY,
    ENTITY_HASH_IDEM,
    ENTITY_HASH_LINK,
    ENTITY_HASH_PROJECTION,
    ENTITY_SCHEMA_VERSION,
    ENTITY_SOURCE_KIND_VALUES,
    EntityHash64,
    _canonical_json,
    _sha256,
    alias_review_checksum,
    entity_checksum,
    link_checksum,
    row_idempotency_key,
)
from ._entity_models import (
    AliasCollisionKind,
    AliasCollisionReview,
    AliasReviewStatus,
    AliasStatus,
    EntityAlias,
    EntityClaim,
    EntityLink,
    EntityLinkClaim,
    EntityType,
    LinkKind,
    WorldEntity,
)
from ._entity_gate import (
    EntityGate,
    EntityGateResult,
    EntityLinkGateResult,
    EntityVerdict,  # noqa: F401  # facade surface parity (not in __all__)
)
from ._entity_projection import (
    EntityCandidateProjection,
    WorldEntityQueryEngine,
    _normalize_name,  # noqa: F401  # facade surface parity (not in __all__)
    build_entity_candidate,
    build_entity_projection,
    detect_alias_collisions,
    entity_projection_checksum,
    entity_projection_verified,
    name_similarity,
    visible_at_cutoff,
)

# ---------------------------------------------------------------------------
# Re-export the rule-side checksum helpers for the repository layer.
# ---------------------------------------------------------------------------

__all__ = [
    "ALIAS_COLLISION_THRESHOLD",
    "AliasCollisionKind",
    "AliasCollisionReview",
    "AliasReviewStatus",
    "AliasStatus",
    "EntityAlias",
    "EntityCandidateProjection",
    "EntityClaim",
    "EntityGate",
    "EntityGateResult",
    "EntityLink",
    "EntityLinkClaim",
    "EntityLinkGateResult",
    "EntityType",
    "ENTITY_SCHEMA_VERSION",
    "LinkKind",
    "SourceKind",
    "WorldEntity",
    "WorldEntityQueryEngine",
    "alias_review_checksum",
    "build_entity_candidate",
    "build_entity_projection",
    "detect_alias_collisions",
    "entity_checksum",
    "entity_projection_checksum",
    "entity_projection_verified",
    "exception_checksum",
    "link_checksum",
    "name_similarity",
    "rule_checksum",
    "visible_at_cutoff",
]
