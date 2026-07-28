"""Unit tests for timeline → KG character/judgment backfill (deterministic seed)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.relationship import RELATIONSHIP_EDGE_TYPES
from app.schemas.relationship import RelationshipSemanticJudgment
from app.services.relationships.gates import RelationshipGateService
from app.services.relationships.query import RelationshipGraphQueryService
from app.services.relationships.timeline_kg_backfill import (
    ALLOWED_TYPES,
    BackfillResult,
    TimelineKgBackfillService,
    _sha1_hex,
)

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64


def _unit(
    eid: str = "ev-1",
    chapter: int = 1,
    narrative_index: int = 0,
    text: str = "Alice and Bob",
):
    from app.services.relationships.evidence import make_evidence_unit

    return make_evidence_unit(
        evidence_id=eid,
        chapter_id=10 + chapter,
        chapter_number=chapter,
        narrative_index=narrative_index,
        text=text,
    )


def _package(**overrides: Any):
    from app.services.relationships.evidence import build_relationship_evidence_package

    units = overrides.pop("units", None) or [_unit()]
    kwargs = dict(
        owner_id=1,
        novel_id=2,
        analysis_version_id=3,
        candidate_key="sj:9:sc:1:tc:2:rt:ally",
        source_judgment_id=9,
        source_relation_candidate_id=8,
        source_character_id=1,
        target_character_id=2,
        source_ref="character:1",
        target_ref="character:2",
        relation_type="ally",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        source_judgment_checksum=HEX64_C,
        units=units,
        recall_signals={"timeline_cooccur": {"count": 5}},
    )
    kwargs.update(overrides)
    return build_relationship_evidence_package(**kwargs)


# ---------------------------------------------------------------------------
# Pure helpers / result serialisation
# ---------------------------------------------------------------------------


def test_sha1_hex_is_stable_and_hex_digest():
    assert _sha1_hex("pair-key") == _sha1_hex("pair-key")
    assert _sha1_hex("a") != _sha1_hex("b")
    digest = _sha1_hex("timeline")
    assert len(digest) == 40
    assert all(c in "0123456789abcdef" for c in digest)


def test_backfill_result_to_dict_defaults_and_fields():
    result = BackfillResult(novel_id=91, owner_id=1, analysis_version_id=None)
    payload = result.to_dict()
    assert payload == {
        "novel_id": 91,
        "owner_id": 1,
        "analysis_version_id": None,
        "characters_created": 0,
        "characters_total": 0,
        "kg_run_id": None,
        "judgments_created": 0,
        "judgments_by_type": {},
        "relationship_build_status": None,
        "relationship_accepted": 0,
        "relationship_candidate_count": 0,
        "errors": [],
    }

    result.characters_created = 3
    result.characters_total = 5
    result.kg_run_id = 42
    result.judgments_created = 7
    result.judgments_by_type = {"ally": 4, "enemy": 3}
    result.relationship_build_status = "completed"
    result.relationship_accepted = 6
    result.relationship_candidate_count = 7
    result.errors.append("pair_skip:x")
    payload2 = result.to_dict()
    assert payload2["characters_created"] == 3
    assert payload2["judgments_by_type"] == {"ally": 4, "enemy": 3}
    assert payload2["errors"] == ["pair_skip:x"]
    assert set(payload2.keys()) == set(payload.keys())


def test_allowed_types_match_relationship_edge_types():
    assert ALLOWED_TYPES == frozenset(RELATIONSHIP_EDGE_TYPES)
    assert ALLOWED_TYPES == frozenset({"ally", "enemy", "family", "mentor", "romantic"})


# ---------------------------------------------------------------------------
# Pair typing via _infer_provisional_type (shared with query service)
# ---------------------------------------------------------------------------


def test_type_infer_uses_event_type_prior_for_conflict():
    infer = RelationshipGraphQueryService._infer_provisional_type
    assert infer(title="对峙", description="双方僵持", event_type="conflict") == "enemy"
    assert infer(title="会谈", description="平静交谈", event_type="character") == "ally"


def test_type_infer_keyword_overrides_for_family_mentor_romantic_enemy():
    infer = RelationshipGraphQueryService._infer_provisional_type
    assert (
        infer(title="血脉相认", description="父子相认", event_type="plot") == "family"
    )
    assert (
        infer(title="拜师", description="收徒传授功法", event_type="plot") == "mentor"
    )
    assert infer(title="告白", description="两人恋爱", event_type="plot") == "romantic"
    assert infer(title="决裂", description="仇敌厮杀", event_type="plot") == "enemy"


def test_backfill_service_binds_same_type_infer():
    service = TimelineKgBackfillService()
    # Stored as callable on the service; same semantics as the query helper.
    assert callable(service._type_infer)
    kwargs = dict(title="仇敌交锋", description="决战", event_type="conflict")
    assert service._type_infer(**kwargs) == "enemy"
    assert service._type_infer(
        **kwargs
    ) == RelationshipGraphQueryService._infer_provisional_type(**kwargs)


@pytest.mark.asyncio
async def test_collect_typed_pairs_falls_back_to_ally_for_unknown_infer(monkeypatch):
    """If infer returns a non-edge label, backfill coerces to ally."""
    service = TimelineKgBackfillService()
    monkeypatch.setattr(service, "_type_infer", lambda **_kw: "friend")

    # Minimal fake async session: Alice+Bob co-occurring three times.
    events = []
    for i in range(3):
        e = MagicMock()
        e.id = i + 1
        e.title = "x"
        e.description = "y"
        e.event_type = "plot"
        e.narrative_chapter_number = i + 1
        events.append(e)

    parts = []
    for eid in (1, 2, 3):
        for name in ("Alice", "Bob"):
            p = MagicMock()
            p.event_id = eid
            p.mention = name
            parts.append(p)

    char_a = MagicMock()
    char_a.id = 10
    char_b = MagicMock()
    char_b.id = 11
    mention_to_char = {"Alice": char_a, "Bob": char_b}

    call_n = {"n": 0}

    async def _scalars(_stmt):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return MagicMock(all=lambda: events)
        return MagicMock(all=lambda: parts)

    db = AsyncMock()
    db.scalars = _scalars

    pairs = await service._collect_typed_pairs(
        db,
        owner_id=1,
        novel_id=2,
        version_id=3,
        mention_to_char=mention_to_char,
        min_cooccur=3,
        max_judgments=10,
    )
    assert pairs
    assert all(p["relation_type"] == "ally" for p in pairs)
    assert pairs[0]["name_a"] == "Alice"
    assert pairs[0]["name_b"] == "Bob"
    assert pairs[0]["count"] == 3


# ---------------------------------------------------------------------------
# Deterministic Phase-09 judge payloads: empty risk_flags (gate contract)
# ---------------------------------------------------------------------------


def _deterministic_payload_like_backfill(package, **overrides: Any) -> dict[str, Any]:
    """Mirror TimelineKgBackfillService._build_deterministic_outputs body."""
    eids = package.allowed_evidence_ids()
    payload = {
        "schema_version": "relationship-semantic-judgment.v1",
        "candidate_key": package.candidate_key,
        "source_ref": package.source_ref,
        "target_ref": package.target_ref,
        "relation_type": package.relation_type,
        "transition": "establish",
        "valid_from_evidence_id": eids[0],
        "valid_to_evidence_id": None,
        "supporting_evidence_ids": eids[:3],
        "confidence": 0.9,
        "rationale": "timeline_kg_backfill deterministic establish",
        "risk_flags": [],
    }
    payload.update(overrides)
    return payload


def test_deterministic_judge_payload_has_empty_risk_flags():
    package = _package()
    payload = _deterministic_payload_like_backfill(package)
    assert payload["risk_flags"] == []
    j = RelationshipSemanticJudgment.model_validate(payload)
    assert j.risk_flags == []


def test_gate_auto_accepts_deterministic_seed_with_empty_risk_flags():
    package = _package()
    gates = RelationshipGateService()
    j = RelationshipSemanticJudgment.model_validate(
        _deterministic_payload_like_backfill(package, confidence=0.9)
    )
    decision = gates.evaluate(
        package=package,
        judgment=j,
        source_still_accepted=True,
        fiction_domain=True,
    )
    assert decision.accepted
    assert decision.gate_status == "accepted"


def test_gate_rejects_any_risk_flag_even_at_high_confidence():
    """Contract: any risk_flag forces needs_human_review (backfill must keep [])."""
    package = _package()
    gates = RelationshipGateService()
    j = RelationshipSemanticJudgment.model_validate(
        _deterministic_payload_like_backfill(
            package,
            confidence=0.95,
            risk_flags=["timeline_heuristic_seed"],
        )
    )
    decision = gates.evaluate(
        package=package,
        judgment=j,
        source_still_accepted=True,
        fiction_domain=True,
    )
    assert decision.accepted is False
    assert decision.needs_review
    assert any("risk_flags" in f for f in decision.gate_failures)


@pytest.mark.asyncio
async def test_build_deterministic_outputs_empty_risk_flags_and_keys():
    """_build_deterministic_outputs maps drafts → payloads with risk_flags=[]."""
    from app.services.relationships.evidence import build_relationship_evidence_package

    package = build_relationship_evidence_package(
        owner_id=1,
        novel_id=2,
        analysis_version_id=3,
        candidate_key="sj:99:sc:1:tc:2:rt:enemy",
        source_judgment_id=99,
        source_relation_candidate_id=88,
        source_character_id=1,
        target_character_id=2,
        source_ref="character:1",
        target_ref="character:2",
        relation_type="enemy",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        source_judgment_checksum=HEX64_C,
        units=[_unit("ev-seed")],
        recall_signals={},
    )

    draft = MagicMock()
    draft.package = package
    draft.relation_type = "enemy"
    draft.source_judgment_id = 99

    selection = MagicMock()
    selection.drafts = [draft]

    service = TimelineKgBackfillService()
    with patch(
        "app.services.relationships.candidates.relationship_candidate_service.select_and_build",
        new=AsyncMock(return_value=selection),
    ):
        outputs = await service._build_deterministic_outputs(
            AsyncMock(),
            owner_id=1,
            novel_id=2,
            analysis_version_id=3,
        )

    assert package.candidate_key in outputs
    assert "99" in outputs
    for payload in outputs.values():
        assert payload["risk_flags"] == []
        assert payload["transition"] == "establish"
        assert payload["confidence"] == 0.9
        assert payload["relation_type"] == "enemy"
        # Gate must accept
        j = RelationshipSemanticJudgment.model_validate(payload)
        decision = RelationshipGateService().evaluate(
            package=package,
            judgment=j,
            source_still_accepted=True,
            fiction_domain=True,
        )
        assert decision.accepted


# ---------------------------------------------------------------------------
# Character mention filtering (logic mirrored via private ranking path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_characters_filters_noise_and_ranks():
    """Noise mentions dropped; top-N by frequency; first role protagonist when new."""
    from app.models.character import Character

    service = TimelineKgBackfillService()

    events = [
        MagicMock(id=1, narrative_chapter_number=2),
        MagicMock(id=2, narrative_chapter_number=1),
    ]
    # Alice x3, Bob x2, noise, punctuation
    parts = [
        MagicMock(event_id=1, mention="Alice"),
        MagicMock(event_id=1, mention="Alice"),
        MagicMock(event_id=2, mention="Alice"),
        MagicMock(event_id=1, mention="Bob"),
        MagicMock(event_id=2, mention="Bob"),
        MagicMock(event_id=1, mention="!!!"),
        MagicMock(event_id=1, mention=""),
        MagicMock(event_id=1, mention="  "),
        MagicMock(event_id=1, mention="x" * 41),  # too long
    ]

    call_n = {"n": 0}

    async def _scalars(_stmt):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return MagicMock(all=lambda: events)
        if call_n["n"] == 2:
            return MagicMock(all=lambda: parts)
        # existing characters
        return MagicMock(all=lambda: [])

    created_chars: list[Any] = []
    next_id = {"v": 0}

    async def _flush():
        for c in created_chars:
            if getattr(c, "id", None) is None:
                next_id["v"] += 1
                c.id = next_id["v"]

    db = AsyncMock()
    db.scalars = _scalars
    db.add = created_chars.append
    db.flush = _flush

    out, created = await service._ensure_characters(
        db,
        novel_id=9,
        version_id=3,
        owner_id=1,
        max_characters=40,
    )

    assert created == 2
    assert set(out.keys()) == {"Alice", "Bob"}
    assert all(isinstance(c, Character) for c in out.values())
    assert out["Alice"].role == "protagonist"
    assert out["Bob"].role == "supporting"
    assert out["Alice"].first_appearance_chapter == 1  # chapter from event 2
    assert out["Alice"].extra_data["mention_count"] == 3
    assert out["Bob"].extra_data["mention_count"] == 2
