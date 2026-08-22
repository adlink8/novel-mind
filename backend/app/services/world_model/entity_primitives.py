"""Shared pure hashing/checksum primitives for the world-entity projection (leaf).

Extracted from ``entities.py`` (refactor split): the canonical-JSON serializer,
the SHA-256 digest builder, the hash-component constants, the ``EntityHash64``
type, the idempotency-key builder, the alias-collision threshold and the entity
source-kind values shared with the rule check constraints. Leaf by construction
— imports only stdlib, pydantic and the ``world_model`` contract primitives
(``contracts.py`` / ``rules.py``), never ``entities.py``. The ``entities``
facade re-exports these names so the ``app.services.world_model.entities``
import surface is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import StringConstraints

from app.services.world_model.rules import SourceKind

if TYPE_CHECKING:  # pragma: no cover - type-only forward references
    from ._entity_models import AliasCollisionReview, EntityLink, WorldEntity

EntityHash64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

ENTITY_SCHEMA_VERSION = "world-model-entity.v1"
ENTITY_HASH_ENTITY = f"{ENTITY_SCHEMA_VERSION}:entity"
ENTITY_HASH_LINK = f"{ENTITY_SCHEMA_VERSION}:link"
ENTITY_HASH_ALIAS_REVIEW = f"{ENTITY_SCHEMA_VERSION}:alias_review"
ENTITY_HASH_PROJECTION = f"{ENTITY_SCHEMA_VERSION}:projection"
ENTITY_HASH_IDEM = f"{ENTITY_SCHEMA_VERSION}:idem"

ALIAS_COLLISION_THRESHOLD = 0.75

#: Values shared with ``world_model_rules`` source_kind check constraints.
ENTITY_SOURCE_KIND_VALUES = tuple(kind.value for kind in SourceKind)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(component: str, body: str) -> str:
    return hashlib.sha256(f"{component}\n{body}".encode("utf-8")).hexdigest()


def entity_checksum(entity: "WorldEntity") -> str:
    return _sha256(ENTITY_HASH_ENTITY, _canonical_json(entity.model_dump(mode="json")))


def link_checksum(link: "EntityLink") -> str:
    return _sha256(ENTITY_HASH_LINK, _canonical_json(link.model_dump(mode="json")))


def alias_review_checksum(review: "AliasCollisionReview") -> str:
    return _sha256(
        ENTITY_HASH_ALIAS_REVIEW, _canonical_json(review.model_dump(mode="json"))
    )


def row_idempotency_key(component: str, payload: dict[str, Any]) -> str:
    """Deterministic replay key over one row's canonical payload."""
    return _sha256(ENTITY_HASH_IDEM, _canonical_json(payload))
