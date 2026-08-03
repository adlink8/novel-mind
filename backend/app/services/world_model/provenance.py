"""Structural provenance validation for the world-entity candidate package.

Phase 27-03 / REQ-WM-03. Pure graph/source-closure checks only. It does not
promote candidates, write rows, or emit approvals — it fail-closes a candidate
package before the sealed projection is built (D-02/D-03/D-04/D-06).

Checks:
- unique entity / rule / exception / link keys;
- aliases unique within an entity;
- link endpoints are projection-local entities;
- rule exceptions bind to a projection-local rule and (when present) a
  projection-local entity — exceptions are never orphaned or dropped;
- every durable row carries at least one source EvidenceRef (source lineage);
- no row may carry a Reader Chat / user-conversation source kind (D-06);
- every entity/rule lineage is a chain ending at itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from app.services.world_model.entities import WorldEntity
from app.services.world_model.rules import (
    CHAT_SOURCE_KINDS,
    RuleException,
    WorldRule,
)


class EntityProvenanceReason(StrEnum):
    DUPLICATE_ENTITY_KEY = "duplicate_entity_key"
    DUPLICATE_RULE_KEY = "duplicate_rule_key"
    DUPLICATE_EXCEPTION_KEY = "duplicate_exception_key"
    DUPLICATE_LINK_KEY = "duplicate_link_key"
    DUPLICATE_ALIAS = "duplicate_alias"
    ORPHAN_LINK_ENDPOINT = "orphan_link_endpoint"
    ORPHAN_EXCEPTION_RULE = "orphan_exception_rule"
    ORPHAN_EXCEPTION_TARGET = "orphan_exception_target"
    MISSING_SOURCE_LINEAGE = "missing_source_lineage"
    CHAT_SOURCE_IN_CANDIDATE = "chat_source_in_candidate"
    LINEAGE_BROKEN = "lineage_broken"


@dataclass(frozen=True)
class EntityProvenanceResult:
    ok: bool
    reason_codes: tuple[str, ...]
    observed_counts: dict[str, int]


def validate_entity_package(
    *,
    entities: Sequence[WorldEntity],
    links: Sequence[object] = (),
    rules: Sequence[WorldRule] = (),
    exceptions: Sequence[RuleException] = (),
) -> EntityProvenanceResult:
    """Deterministic pure structural validation for a candidate package."""
    from app.services.world_model.entities import EntityLink

    reasons: set[str] = set()

    entity_keys: set[str] = set()
    for entity in entities:
        if entity.entity_key in entity_keys:
            reasons.add(EntityProvenanceReason.DUPLICATE_ENTITY_KEY.value)
        entity_keys.add(entity.entity_key)
        if not entity.source_refs:
            reasons.add(EntityProvenanceReason.MISSING_SOURCE_LINEAGE.value)
        if entity.source_kind in CHAT_SOURCE_KINDS:
            reasons.add(EntityProvenanceReason.CHAT_SOURCE_IN_CANDIDATE.value)
        if not entity.lineage or entity.lineage[-1] != entity.entity_key:
            reasons.add(EntityProvenanceReason.LINEAGE_BROKEN.value)
        alias_names = [alias.alias for alias in entity.aliases]
        if len(alias_names) != len(set(alias_names)):
            reasons.add(EntityProvenanceReason.DUPLICATE_ALIAS.value)

    link_keys: set[str] = set()
    for raw_link in links:
        if not isinstance(raw_link, EntityLink):
            reasons.add(EntityProvenanceReason.DUPLICATE_LINK_KEY.value)
            continue
        if raw_link.link_key in link_keys:
            reasons.add(EntityProvenanceReason.DUPLICATE_LINK_KEY.value)
        link_keys.add(raw_link.link_key)
        if (
            raw_link.source_key not in entity_keys
            or raw_link.target_key not in entity_keys
        ):
            reasons.add(EntityProvenanceReason.ORPHAN_LINK_ENDPOINT.value)
        if not raw_link.source_refs:
            reasons.add(EntityProvenanceReason.MISSING_SOURCE_LINEAGE.value)
        if raw_link.source_kind in CHAT_SOURCE_KINDS:
            reasons.add(EntityProvenanceReason.CHAT_SOURCE_IN_CANDIDATE.value)

    rule_keys: set[str] = set()
    for rule in rules:
        if rule.rule_key in rule_keys:
            reasons.add(EntityProvenanceReason.DUPLICATE_RULE_KEY.value)
        rule_keys.add(rule.rule_key)
        if not rule.source_refs:
            reasons.add(EntityProvenanceReason.MISSING_SOURCE_LINEAGE.value)
        if rule.source_kind in CHAT_SOURCE_KINDS:
            reasons.add(EntityProvenanceReason.CHAT_SOURCE_IN_CANDIDATE.value)
        if not rule.lineage or rule.lineage[-1] != rule.rule_key:
            reasons.add(EntityProvenanceReason.LINEAGE_BROKEN.value)

    exception_keys: set[str] = set()
    for exception in exceptions:
        if exception.exception_key in exception_keys:
            reasons.add(EntityProvenanceReason.DUPLICATE_EXCEPTION_KEY.value)
        exception_keys.add(exception.exception_key)
        if exception.rule_key not in rule_keys:
            reasons.add(EntityProvenanceReason.ORPHAN_EXCEPTION_RULE.value)
        if (
            exception.applies_to is not None
            and exception.applies_to not in entity_keys
        ):
            reasons.add(EntityProvenanceReason.ORPHAN_EXCEPTION_TARGET.value)
        if not exception.source_refs:
            reasons.add(EntityProvenanceReason.MISSING_SOURCE_LINEAGE.value)
        if exception.source_kind in CHAT_SOURCE_KINDS:
            reasons.add(EntityProvenanceReason.CHAT_SOURCE_IN_CANDIDATE.value)

    observed = {
        "entities": len(entities),
        "links": len(links),
        "rules": len(rules),
        "exceptions": len(exceptions),
        "reason_count": len(reasons),
    }
    ordered = tuple(sorted(reasons))
    return EntityProvenanceResult(
        ok=not ordered,
        reason_codes=ordered,
        observed_counts=observed,
    )


def entity_provenance_reasons(
    result: EntityProvenanceResult,
) -> tuple[EntityProvenanceReason, ...]:
    """Map reason code strings back to enum values for tests."""
    return tuple(EntityProvenanceReason(code) for code in result.reason_codes)


# Re-export for convenience of the pure validation API.
__all__ = [
    "EntityProvenanceReason",
    "EntityProvenanceResult",
    "entity_provenance_reasons",
    "validate_entity_package",
]
