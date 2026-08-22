"""Phase 27-03 world entity/rule/faction/place/item unit tests (REQ-WM-03).

Coverage:
- typed entities/factions/places/items carry aliases, membership/ownership/
  spatial/item-state links, world rules and first-class rule exceptions with
  owner/novel/version/cutoff, authority, EvidenceRefs and gate status;
- alias similarity produces review candidates and never silently merges;
- rule exceptions remain first-class, queryable and never dropped;
- wrong-owner, spoiler cutoff, chat contamination, authority upgrade and stale
  evidence fail closed at the gate;
- provenance rejects orphan links / orphan exceptions / missing lineage / chat
  sources in a candidate package;
- the durable projection is immutable and hash-sealed with no active pointer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.world_model.contracts import (
    Authority,
    EvidenceRef,
    GateStatus,
)
from app.services.world_model.entities import (
    AliasCollisionKind,
    AliasReviewStatus,
    AliasStatus,
    EntityCandidateProjection,
    EntityClaim,
    EntityGate,
    EntityLinkClaim,
    EntityType,
    LinkKind,
    WorldEntity,
    WorldEntityQueryEngine,
    build_entity_candidate,
    build_entity_projection,
    detect_alias_collisions,
    entity_checksum,
    entity_projection_checksum,
    entity_projection_verified,
    name_similarity,
    visible_at_cutoff,
)
from app.services.world_model.provenance import (
    EntityProvenanceReason,
    entity_provenance_reasons,
    validate_entity_package,
)
from app.services.world_model.rules import (
    GateReason,
    RuleClaim,
    RuleExceptionClaim,
    RuleGate,
    SourceKind,
)

pytestmark = pytest.mark.unit

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "entities_v1.json"
    ).read_text(encoding="utf-8")
)


def scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def make_entity_gate(name: str, *, version_id: int | None = None) -> EntityGate:
    scope = scenario(name)["scope"]
    return EntityGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id if version_id is not None else scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def make_rule_gate(name: str, *, version_id: int | None = None) -> RuleGate:
    scope = scenario(name)["scope"]
    return RuleGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id if version_id is not None else scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def build_valid(
    name: str = "valid", *, version_id: int = 1
) -> EntityCandidateProjection:
    """Run one fixture scenario through the gates into an immutable candidate."""
    sc = scenario(name)
    scope = sc["scope"]
    egate = make_entity_gate(name, version_id=version_id)
    entities = [
        egate.validate_entity(
            EntityClaim.model_validate({**raw, "version_id": version_id})
        ).entity
        for raw in sc["entities"]
    ]
    assert all(entity is not None for entity in entities), "all entities must gate"
    links = [
        egate.validate_link(
            EntityLinkClaim.model_validate({**raw, "version_id": version_id})
        ).link
        for raw in sc["links"]
    ]
    assert all(link is not None for link in links), "all links must gate"
    rgate = make_rule_gate(name, version_id=version_id)
    rules = [
        rgate.validate_rule(
            RuleClaim.model_validate({**raw, "version_id": version_id})
        ).rule
        for raw in sc["rules"]
    ]
    assert all(rule is not None for rule in rules), "all rules must gate"
    rule_keys = {rule.rule_key for rule in rules}
    exceptions = [
        rgate.validate_exception(
            RuleExceptionClaim.model_validate({**raw, "version_id": version_id}),
            rule_keys,
        ).exception
        for raw in sc["exceptions"]
    ]
    assert all(exc is not None for exc in exceptions), "all exceptions must gate"
    return build_entity_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        entities=entities,
        links=links,
        rules=rules,
        exceptions=exceptions,
    )


def engine_for(name: str) -> WorldEntityQueryEngine:
    return WorldEntityQueryEngine(build_valid(name))


# ---------------------------------------------------------------------------
# Typed entities, links, rules and first-class exceptions (REQ-WM-03)
# ---------------------------------------------------------------------------


def test_valid_projection_carries_typed_rows_with_lineage():
    projection = build_valid()
    assert entity_projection_verified(projection)
    assert projection.schema_version == "world-model-entity.v1"
    by_key = {entity.entity_key: entity for entity in projection.entities}
    assert by_key["e-place-lin-an"].entity_type == EntityType.PLACE
    assert by_key["e-faction-southern"].entity_type == EntityType.FACTION
    assert by_key["e-item-seal"].entity_type == EntityType.ITEM
    assert by_key["e-char-lin-an"].entity_type == EntityType.ENTITY
    for entity in projection.entities:
        assert entity.lineage == (entity.entity_key,)
        assert entity.gate_status == GateStatus.PASSED
        assert entity.owner_id == 1 and entity.novel_id == 1 and entity.version_id == 1
        assert entity.source_refs
        assert isinstance(entity.source_refs[0], EvidenceRef)


def test_valid_projection_links_membership_ownership_spatial_item_state():
    engine = engine_for("valid")
    kinds = {link.link_kind for link in engine.query_links()}
    assert kinds == {
        LinkKind.MEMBER_OF,
        LinkKind.OWNS,
        LinkKind.LOCATED_IN,
        LinkKind.CARRIED_BY,
    }
    owns = engine.query_links(link_kind=LinkKind.OWNS)
    assert owns[0].source_key == "e-char-lin-an"
    assert owns[0].target_key == "e-item-seal"
    carried = engine.query_links(link_kind=LinkKind.CARRIED_BY)
    assert carried[0].source_key == "e-item-seal"
    assert carried[0].target_key == "e-char-lin-an"


def test_rule_exception_is_first_class_and_queryable():
    projection = build_valid("rule_exception", version_id=3)
    assert [rule.rule_key for rule in projection.rules] == ["rule-magic"]
    exceptions = WorldEntityQueryEngine(projection).query_exceptions(
        rule_key="rule-magic"
    )
    assert [exc.exception_key for exc in exceptions] == ["exc-magic-moon"]
    exception = exceptions[0]
    assert exception.rule_key == "rule-magic"
    assert exception.applies_to == "e-char-lin-an"
    assert exception.gate_status == GateStatus.PASSED
    assert exception.source_refs


def test_exception_never_folded_into_rule_statement():
    projection = build_valid("rule_exception", version_id=3)
    rule = projection.rules[0]
    exception = WorldEntityQueryEngine(projection).query_exceptions()[0]
    # The exception statement stays separate from the rule statement — it is
    # never normalized away or concatenated into the rule text.
    assert exception.statement != rule.statement
    assert "月蚀之夜" not in rule.statement
    assert "月蚀之夜" in exception.statement


def test_aliases_are_preserved_on_entities():
    projection = build_valid()
    lin_an = next(
        entity
        for entity in projection.entities
        if entity.entity_key == "e-place-lin-an"
    )
    assert [alias.alias for alias in lin_an.aliases] == ["临安城"]
    assert lin_an.aliases[0].status == AliasStatus.ACTIVE


def test_duplicate_alias_within_entity_rejected():
    raw = scenario("valid")["entities"][0]
    with pytest.raises(ValidationError):
        EntityClaim.model_validate(
            {
                **raw,
                "aliases": [
                    {"alias": "临安城"},
                    {"alias": "临安城"},
                ],
            }
        )


# ---------------------------------------------------------------------------
# Alias collisions: reviewable, never a silent merge
# ---------------------------------------------------------------------------


def test_alias_collision_produces_reviews_and_never_merges():
    projection = build_valid("alias_collision", version_id=2)
    engine = WorldEntityQueryEngine(projection)
    keys = {entity.entity_key for entity in engine.query_entities()}
    # Both entities remain distinct — nothing was merged, renamed or removed.
    assert keys == {
        "e-faction-nan",
        "e-faction-nanjiang",
        "e-place-lin-an",
        "e-place-lin-anfu",
    }
    reviews = engine.query_alias_reviews()
    assert len(reviews) == 2
    by_key = {review.review_key: review for review in reviews}
    exact = by_key["alias-review:e-faction-nan:e-faction-nanjiang"]
    assert exact.kind == AliasCollisionKind.EXACT_ALIAS
    assert exact.similarity == 1.0
    assert exact.status == AliasReviewStatus.REVIEW
    fuzzy = by_key["alias-review:e-place-lin-an:e-place-lin-anfu"]
    assert fuzzy.kind == AliasCollisionKind.NAME_SIMILARITY
    assert fuzzy.similarity == 0.8
    assert fuzzy.status == AliasReviewStatus.REVIEW
    # Every review carries evidence and scope lineage.
    for review in reviews:
        assert review.source_refs
        assert review.owner_id == 1 and review.version_id == 2


def test_alias_reviews_are_never_auto_resolved():
    projection = build_valid("alias_collision", version_id=2)
    assert all(
        review.status == AliasReviewStatus.REVIEW for review in projection.alias_reviews
    )
    # No review field can silently upgrade; the pair keeps two entity keys.
    for review in projection.alias_reviews:
        assert review.entity_key_a != review.entity_key_b


def test_name_similarity_threshold_behavior():
    assert name_similarity("临安", "临安") == 1.0
    assert name_similarity("南境军", "南疆军") < 0.75
    assert name_similarity("临安", "临安府") >= 0.75
    # Punctuation/whitespace is normalized away before comparison.
    assert name_similarity("林 安", "林安") == 1.0


def test_detect_alias_collisions_skips_cross_scope_pairs():
    raw_a = scenario("alias_collision")["entities"][0]
    raw_b = json.loads(json.dumps(scenario("alias_collision")["entities"][1]))
    raw_b["version_id"] = 99  # different version scope
    entity_a = WorldEntity.model_validate(
        {**raw_a, "lineage": [raw_a["entity_key"]], "gate_status": "passed"}
    )
    entity_b = WorldEntity.model_validate(
        {**raw_b, "lineage": [raw_b["entity_key"]], "gate_status": "passed"}
    )
    reviews = detect_alias_collisions([entity_a, entity_b])
    assert reviews == ()


# ---------------------------------------------------------------------------
# Wrong owner / spoiler / chat / authority upgrade fail closed
# ---------------------------------------------------------------------------


def test_wrong_owner_entity_claim_rejected():
    gate = make_entity_gate("wrong_owner")
    result = gate.validate_entity(
        EntityClaim.model_validate(scenario("wrong_owner")["entities"][0])
    )
    assert result.entity is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.WRONG_OWNER}


def test_spoiler_cutoff_entity_rejected():
    gate = make_entity_gate("spoiler_cutoff")
    result = gate.validate_entity(
        EntityClaim.model_validate(scenario("spoiler_cutoff")["entities"][0])
    )
    assert result.entity is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.SPOILER_CUTOFF}


def test_chat_contamination_rejected_for_entity_and_rule():
    gate = make_entity_gate("chat_contamination")
    entity_result = gate.validate_entity(
        EntityClaim.model_validate(scenario("chat_contamination")["entities"][0])
    )
    assert entity_result.entity is None
    assert GateReason.CHAT_NOT_FACT_SOURCE in entity_result.reason_codes

    rgate = make_rule_gate("chat_contamination")
    rule_result = rgate.validate_rule(
        RuleClaim.model_validate(scenario("chat_contamination")["rules"][0])
    )
    assert rule_result.rule is None
    assert GateReason.CHAT_NOT_FACT_SOURCE in rule_result.reason_codes
    # Even with canon_fact approved, Reader Chat stays a non-source.
    assert GateReason.AUTHORITY_UPGRADE not in rule_result.reason_codes


def test_authority_upgrade_to_canon_fact_rejected():
    gate = make_entity_gate("authority_upgrade")
    claim = EntityClaim.model_validate(scenario("authority_upgrade")["entities"][0])
    assert claim.authority == Authority.CANON_FACT
    result = gate.validate_entity(claim)
    assert result.entity is None
    assert GateReason.AUTHORITY_UPGRADE in result.reason_codes


def test_user_interpretation_requires_approval():
    gate = make_entity_gate("user_interpretation_unapproved")
    result = gate.validate_entity(
        EntityClaim.model_validate(
            scenario("user_interpretation_unapproved")["entities"][0]
        )
    )
    assert result.entity is None
    assert GateReason.MISSING_APPROVAL in result.reason_codes


def test_stale_evidence_rejected_before_any_write():
    gate = make_entity_gate("stale_evidence")
    claim = EntityClaim.model_validate(scenario("stale_evidence")["entities"][0])
    assert claim.source_refs[0].source_snapshot_hash != gate.source_snapshot_hash
    result = gate.validate_entity(claim)
    assert result.entity is None
    assert GateReason.STALE_EVIDENCE in result.reason_codes


def test_stale_version_claim_rejected():
    gate = make_entity_gate("valid", version_id=9)
    result = gate.validate_entity(
        EntityClaim.model_validate(scenario("valid")["entities"][0])
    )
    assert result.entity is None
    assert GateReason.STALE_VERSION in result.reason_codes


# ---------------------------------------------------------------------------
# Provenance: orphan links / exceptions / lineage / chat sources fail closed
# ---------------------------------------------------------------------------


def test_provenance_rejects_orphan_link_endpoint():
    projection = build_valid()
    orphan = projection.links[0].model_copy(
        update={"link_key": "link-orphan", "target_key": "e-no-such-entity"}
    )
    result = validate_entity_package(
        entities=list(projection.entities),
        links=[orphan],
    )
    assert not result.ok
    assert EntityProvenanceReason.ORPHAN_LINK_ENDPOINT in entity_provenance_reasons(
        result
    )


def test_provenance_rejects_orphan_exception_rule():
    projection = build_valid("rule_exception", version_id=3)
    from app.services.world_model.rules import RuleException

    orphan = RuleException.model_validate(
        projection.exceptions[0]
        .model_copy(
            update={"exception_key": "exc-orphan-rule", "rule_key": "rule-no-such"}
        )
        .model_dump(mode="json")
    )
    result = validate_entity_package(
        entities=list(projection.entities),
        rules=list(projection.rules),
        exceptions=[orphan],
    )
    assert not result.ok
    assert EntityProvenanceReason.ORPHAN_EXCEPTION_RULE in entity_provenance_reasons(
        result
    )


def test_provenance_rejects_orphan_exception_target():
    projection = build_valid("rule_exception", version_id=3)
    from app.services.world_model.rules import RuleException

    orphan = RuleException.model_validate(
        projection.exceptions[0]
        .model_copy(
            update={
                "exception_key": "exc-orphan-target",
                "applies_to": "e-no-such-entity",
            }
        )
        .model_dump(mode="json")
    )
    result = validate_entity_package(
        entities=list(projection.entities),
        rules=list(projection.rules),
        exceptions=[orphan],
    )
    assert not result.ok
    assert EntityProvenanceReason.ORPHAN_EXCEPTION_TARGET in entity_provenance_reasons(
        result
    )


def test_provenance_rejects_missing_source_lineage():
    projection = build_valid()
    entity = projection.entities[0]
    without_source = entity.model_copy(update={"source_refs": ()})
    result = validate_entity_package(
        entities=[without_source],
    )
    assert not result.ok
    assert EntityProvenanceReason.MISSING_SOURCE_LINEAGE in entity_provenance_reasons(
        result
    )


def test_provenance_rejects_chat_source_in_candidate():
    projection = build_valid()
    entity = projection.entities[0]
    poisoned = entity.model_copy(update={"source_kind": SourceKind.READER_CHAT})
    result = validate_entity_package(entities=[poisoned])
    assert not result.ok
    assert EntityProvenanceReason.CHAT_SOURCE_IN_CANDIDATE in entity_provenance_reasons(
        result
    )


def test_build_candidate_fails_closed_on_provenance_break():
    projection = build_valid()
    orphan_link = projection.links[0].model_copy(
        update={"link_key": "link-orphan", "target_key": "e-no-such"}
    )
    with pytest.raises(ValueError, match="provenance failed"):
        build_entity_candidate(
            owner_id=1,
            novel_id=1,
            version_id=1,
            entities=list(projection.entities),
            links=[orphan_link],
        )


# ---------------------------------------------------------------------------
# Immutability and lineage
# ---------------------------------------------------------------------------


def test_projection_is_immutable_and_hash_sealed():
    projection = build_valid()
    assert entity_projection_verified(projection)
    fields = set(projection.model_dump().keys())
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in fields


def test_entity_checksum_is_content_anchored():
    projection = build_valid()
    entity = projection.entities[0]
    mutated = entity.model_copy(update={"authority": Authority.CANON_FACT})
    assert entity_checksum(mutated) != entity_checksum(entity)
    # An exception is part of the projection checksum — dropping it changes hash.
    projection_without_exception = build_entity_candidate(
        owner_id=1,
        novel_id=1,
        version_id=1,
        entities=list(projection.entities),
        links=list(projection.links),
        rules=list(projection.rules),
        exceptions=(),
    )
    assert entity_projection_checksum(
        projection_without_exception
    ) != entity_projection_checksum(projection)


def test_projection_rejects_cross_scope_rows():
    projection = build_valid()
    hijack = projection.entities[0].model_copy(update={"owner_id": 2})
    with pytest.raises(ValueError):
        build_entity_candidate(owner_id=1, novel_id=1, version_id=1, entities=[hijack])


def test_projection_rejects_link_to_unknown_entity():
    projection = build_valid()
    orphan = projection.links[0].model_copy(
        update={"link_key": "link-orphan", "target_key": "e-no-such"}
    )
    with pytest.raises(ValueError, match="projection-local"):
        build_entity_projection(
            owner_id=1,
            novel_id=1,
            version_id=1,
            entities=list(projection.entities),
            links=[orphan],
        )


def test_visible_at_cutoff_obeyed_by_query_engine():
    projection = build_valid()
    engine = WorldEntityQueryEngine(projection)
    # The item (disclosure 3) is hidden from a cutoff-2 reader view.
    at_2 = engine.query_entities(cutoff=2)
    assert "e-item-seal" not in {entity.entity_key for entity in at_2}
    at_3 = engine.query_entities(cutoff=3)
    assert "e-item-seal" in {entity.entity_key for entity in at_3}
    assert visible_at_cutoff(1, 1)
    assert not visible_at_cutoff(3, 2)


def test_aliases_status_and_evidence_roundtrip():
    projection = build_valid()
    engine = WorldEntityQueryEngine(projection)
    lin_an = engine.query_entities(entity_type=EntityType.PLACE)[0]
    assert [alias.alias for alias in lin_an.aliases] == ["临安城"]
    for ref in lin_an.source_refs:
        assert len(ref.content_hash) == 64
        assert len(ref.source_snapshot_hash) == 64


def test_query_engine_is_read_only():
    members = {
        name
        for name, _ in WorldEntityQueryEngine.__dict__.items()
        if callable(getattr(WorldEntityQueryEngine, name, None))
    }
    assert not {m for m in members if m.startswith(("append", "write", "update"))}
    assert "query_entities" in members
    assert "query_exceptions" in members
    assert "query_alias_reviews" in members


def test_entity_gate_has_no_promotion_path():
    gate_members = {
        name
        for name, _ in EntityGate.__dict__.items()
        if callable(getattr(EntityGate, name, None))
    }
    assert not {
        m for m in gate_members if m.startswith(("promote", "update", "delete"))
    }
