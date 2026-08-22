"""Phase 25-02: relationship intake provenance — model, writers, query, migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.character import CharacterRelation
from app.models.relationship import (
    RELATIONSHIP_INTAKE_KINDS,
    RelationshipObservation,
)
from app.schemas.relationship import (
    RelationshipEdgeKind,
    RelationshipGraphEdge,
    RelationshipGraphEdgeLabel,
    RelationshipIntakeKind,
)
from app.services.relationships.query import RelationshipGraphQueryService
from app.services.relationships.worker import RelationshipObservationWorker

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Vocabulary and ORM defaults
# ---------------------------------------------------------------------------


def test_intake_kind_vocabulary_matches_schema_enum():
    assert RELATIONSHIP_INTAKE_KINDS == (
        "llm_judgment",
        "timeline_seed_backfill",
        "cooccurrence_candidate",
        "manual",
        "unknown",
    )
    assert set(RELATIONSHIP_INTAKE_KINDS) == {k.value for k in RelationshipIntakeKind}


def test_relationship_observation_column_defaults_and_index():
    table = RelationshipObservation.__table__
    column = table.c.intake_kind
    assert column.nullable is False
    assert column.type.length == 32
    assert column.default.arg == "unknown"
    assert column.server_default.arg == "unknown"
    assert "idx_rel_observations_intake_kind" in {i.name for i in table.indexes}
    constraint_names = {c.name for c in table.constraints if c.name}
    assert "ck_rel_observations_intake_kind" in constraint_names


def test_character_relation_column_defaults_and_index():
    table = CharacterRelation.__table__
    column = table.c.intake_kind
    assert column.nullable is False
    assert column.type.length == 32
    assert column.default.arg == "unknown"
    assert column.server_default.arg == "unknown"
    assert column.index is True


# ---------------------------------------------------------------------------
# Migration chain stays serial on top of 18appsetting1 (single head)
# ---------------------------------------------------------------------------


def test_migration_chain_is_serial_from_18appsetting1():
    first = _load_migration("25_relationship_observation_intake_kind.py")
    second = _load_migration("25_character_relation_intake_kind.py")
    assert first.revision == "25relintake01"
    assert first.down_revision == "18appsetting1"
    assert second.revision == "25relintake02"
    assert second.down_revision == "25relintake01"
    # ORM vocabulary must match the migration CHECK expression.
    for kind in RELATIONSHIP_INTAKE_KINDS:
        assert f"'{kind}'" in first.INTAKE_KINDS_SQL


# ---------------------------------------------------------------------------
# Writer assignment (worker resolution + backfill override)
# ---------------------------------------------------------------------------


def _kg_judgment(**overrides):
    judgment = MagicMock()
    judgment.model_name = overrides.get("model_name", "gemini/gemini-2.5-flash")
    judgment.structured_output = overrides.get("structured_output", {})
    judgment.raw_output = overrides.get("raw_output", {})
    return judgment


def test_resolve_intake_kind_defaults_to_llm_judgment():
    resolved = RelationshipObservationWorker._resolve_intake_kind(
        requested=None, source_judgment=_kg_judgment()
    )
    assert resolved == "llm_judgment"


def test_resolve_intake_kind_detects_timeline_seed_sources():
    by_model = RelationshipObservationWorker._resolve_intake_kind(
        requested=None,
        source_judgment=_kg_judgment(model_name="timeline_cooccur_heuristic"),
    )
    assert by_model == "timeline_seed_backfill"

    by_structured = RelationshipObservationWorker._resolve_intake_kind(
        requested=None,
        source_judgment=_kg_judgment(
            structured_output={"source": "timeline_kg_backfill"}
        ),
    )
    assert by_structured == "timeline_seed_backfill"

    by_raw = RelationshipObservationWorker._resolve_intake_kind(
        requested=None,
        source_judgment=_kg_judgment(raw_output={"source": "timeline_kg_backfill"}),
    )
    assert by_raw == "timeline_seed_backfill"


def test_resolve_intake_kind_requested_wins_and_invalid_is_unknown():
    forced = RelationshipObservationWorker._resolve_intake_kind(
        requested="timeline_seed_backfill", source_judgment=_kg_judgment()
    )
    assert forced == "timeline_seed_backfill"

    invalid = RelationshipObservationWorker._resolve_intake_kind(
        requested="made_up_kind", source_judgment=_kg_judgment()
    )
    assert invalid == "unknown"


def test_backfill_run_call_forces_timeline_seed_backfill():
    """The seed/ops path must label its worker run explicitly."""
    import inspect

    from app.services.relationships import timeline_kg_backfill as mod

    source = inspect.getsource(mod.TimelineKgBackfillService.backfill)
    assert 'intake_kind="timeline_seed_backfill"' in source


def test_projection_intake_kind_for_legacy_character_relations():
    from app.services.knowledge.projection import _judgment_intake_kind

    assert _judgment_intake_kind(_kg_judgment()) == "llm_judgment"
    assert (
        _judgment_intake_kind(_kg_judgment(model_name="timeline_cooccur_heuristic"))
        == "timeline_seed_backfill"
    )
    assert (
        _judgment_intake_kind(
            _kg_judgment(raw_output={"source": "timeline_kg_backfill"})
        )
        == "timeline_seed_backfill"
    )


# ---------------------------------------------------------------------------
# Query projection carries intake_kind through fold and API schema
# ---------------------------------------------------------------------------


def _obs(obs_id: int, intake_kind: str | None = "llm_judgment", **overrides):
    data = dict(
        id=obs_id,
        source_character_id=1,
        target_character_id=2,
        relation_type="ally",
        transition="establish",
        confidence=0.9,
        valid_from_chapter=1,
        valid_from_narrative_index=0,
        valid_to_chapter=None,
        valid_to_narrative_index=None,
    )
    data.update(overrides)
    row = SimpleNamespace(**data)
    if intake_kind is not None:
        row.intake_kind = intake_kind
    return row


def test_fold_propagates_intake_kind_from_observation():
    service = RelationshipGraphQueryService()
    folded = service._fold_observations(
        [_obs(1, "timeline_seed_backfill")],
        identity_map={},
        override_fields={},
    )
    assert len(folded) == 1
    assert folded[0].intake_kind == "timeline_seed_backfill"


def test_fold_defaults_missing_intake_kind_to_unknown():
    service = RelationshipGraphQueryService()
    folded = service._fold_observations(
        [_obs(1, intake_kind=None)],
        identity_map={},
        override_fields={},
    )
    assert len(folded) == 1
    assert folded[0].intake_kind == "unknown"


def test_graph_edge_schema_exposes_intake_kind_with_unknown_default():
    edge = RelationshipGraphEdge(
        observation_id=1,
        source_character_id=1,
        target_character_id=2,
        relation_type=RelationshipGraphEdgeLabel.ALLY,
        transition="establish",
        confidence=0.9,
        valid_from_chapter=1,
    )
    assert edge.intake_kind == RelationshipIntakeKind.UNKNOWN

    typed = RelationshipGraphEdge(
        observation_id=2,
        source_character_id=1,
        target_character_id=2,
        relation_type=RelationshipGraphEdgeLabel.ALLY,
        transition="establish",
        confidence=0.9,
        valid_from_chapter=1,
        intake_kind=RelationshipIntakeKind.LLM_JUDGMENT,
    )
    assert typed.intake_kind == RelationshipIntakeKind.LLM_JUDGMENT
    assert typed.model_dump(mode="json")["intake_kind"] == "llm_judgment"


def test_provisional_cooccurrence_edge_carries_cooccurrence_candidate_intake():
    edge = RelationshipGraphEdge(
        observation_id=3,
        source_character_id=1,
        target_character_id=2,
        relation_type=RelationshipGraphEdgeLabel.COOCCUR,
        transition="establish",
        confidence=0.4,
        valid_from_chapter=1,
        edge_kind=RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE,
        intake_kind=RelationshipIntakeKind.COOCCURRENCE_CANDIDATE,
    )
    assert edge.intake_kind == RelationshipIntakeKind.COOCCURRENCE_CANDIDATE
