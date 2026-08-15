"""Phase 31-02 Key Scene scoring unit tests (REQ-VIS-02 / REQ-VIS-06).

Covers D-31-03 / D-31-05:
- deterministic multi-signal scoring: same frozen input → stable ordering and
  identical breakdown/reasons/diversity key;
- action and quiet-emotional fixtures both survive the diversity quota;
- embedding similarity is one input among many and cannot decide ranking;
- score breakdown, reason codes and versioned policy hash are inspectable;
- advisory speaker/dialogue heuristic: available offsets/confidence, ambiguous
  reduced confidence + warnings, unavailable stays explicit (never silent zero);
- repetition overlap penalty and diversity quota reasons from the closed
  vocabulary.
"""

from __future__ import annotations

import pytest

from app.schemas.key_scene import (
    HeuristicSignalAvailability,
    KeySceneReasonCode,
    SceneCoordinates,
)
from app.services.key_scenes.scoring import (
    DEFAULT_SCENE_POLICY,
    KeySceneScorer,
    SceneScoreInput,
    compute_diversity_key,
    diversity_groups,
    policy_hash,
    rank_with_diversity,
)

pytestmark = pytest.mark.unit

SCORER = KeySceneScorer(detector_id="key-scene.v1", detector_version="1.0.0")

ACTION_TEXT = (
    "Arin drew his sword as the rain fell hard across the courtyard walls. "
    '"We attack at dawn!" he said. Mara drew her sword and charged. '
    "The enemy banners would rise with the sun and there would be no going back! "
    "Torches guttered low across the courtyard as the attack exploded."
)
QUIET_TEXT = (
    "It was a quiet night on the harbor. Arin wept quietly by the rail and "
    "remembered the grief of the long winter. She watched the moon and thought "
    "of everyone they had lost, in a calm that hurt more than any battle."
)
AMBIGUOUS_TEXT = (
    "Arin walked into the hall. He sat down. Nothing much happened and no one "
    "spoke as the minutes passed."
)
DIALOGUE_AMBIGUOUS_TEXT = '"Hurry," a strange voice echoed. "No time."'


def _input(
    scene_id: str,
    content: str,
    *,
    chapter_number: int = 3,
    source_start: int = 0,
    source_end: int = 300,
    coordinates: SceneCoordinates | None = None,
    **overrides,
) -> SceneScoreInput:
    payload = {
        "scene_id": scene_id,
        "chapter_id": 3,
        "chapter_number": chapter_number,
        "source_start": source_start,
        "source_end": source_end,
        "source_hash": "c" * 64,
        "content": content,
        "coordinates": coordinates or SceneCoordinates(),
    }
    payload.update(overrides)
    return SceneScoreInput(**payload)


def _action_input() -> SceneScoreInput:
    return _input(
        "hn_saaaaaaaaaaaaaaaaaaaaaa",
        ACTION_TEXT,
        coordinates=SceneCoordinates(
            cast=["arin", "mara"],
            place="courtyard",
            time="night",
            pov="arin",
        ),
    )


def _quiet_input() -> SceneScoreInput:
    return _input(
        "hn_sbbbbbbbbbbbbbbbbbbbbbb",
        QUIET_TEXT,
        coordinates=SceneCoordinates(
            cast=["arin"],
            place="harbor",
            time="night",
            pov="arin",
        ),
    )


def _ambiguous_input() -> SceneScoreInput:
    return _input(
        "hn_scccccccccccccccccccccc",
        AMBIGUOUS_TEXT,
        coordinates=SceneCoordinates(cast=["arin"], place="hall"),
    )


# ---------------------------------------------------------------------------
# Deterministic multi-signal scoring
# ---------------------------------------------------------------------------


def test_policy_hash_is_deterministic_and_versioned():
    assert policy_hash() == policy_hash()
    assert len(policy_hash()) == 64
    from app.services.key_scenes.scoring import KeySceneScoringPolicy

    changed = KeySceneScoringPolicy(plot_turn_weight=0.5)
    assert policy_hash(changed) != policy_hash()


def test_same_frozen_input_scores_identically():
    a = SCORER.score(_action_input())
    b = SCORER.score(_action_input())
    assert a == b
    assert a.score_total == b.score_total
    assert a.score_breakdown == b.score_breakdown
    assert a.salience_reasons == b.salience_reasons
    assert a.diversity_key == b.diversity_key


def test_score_breakdown_and_reasons_are_inspectable():
    scored = SCORER.score(_action_input())
    assert scored.score_total > 0.0
    assert scored.score_breakdown["plot_turn"] > 0.0
    assert (
        scored.score_breakdown["evidence_boundary"]
        == DEFAULT_SCENE_POLICY.evidence_base
    )
    codes = {r.reason_code for r in scored.salience_reasons}
    assert KeySceneReasonCode.EVIDENCE_BOUNDARY in codes
    assert KeySceneReasonCode.PLOT_TURN in codes
    # Every reason code belongs to the closed vocabulary.
    from app.schemas.key_scene import KEY_SCENE_REASON_CODES

    assert codes <= {KeySceneReasonCode(code) for code in KEY_SCENE_REASON_CODES}


def test_action_scene_outranks_quiet_but_both_score():
    action = SCORER.score(_action_input())
    quiet = SCORER.score(_quiet_input())
    assert action.score_total > quiet.score_total
    assert quiet.score_total > 0.0


# ---------------------------------------------------------------------------
# Embedding similarity alone cannot decide ranking (D-31-03)
# ---------------------------------------------------------------------------


def test_embedding_similarity_alone_cannot_win_ranking():
    action = SCORER.score(_action_input())
    # A scene whose only strength is embedding similarity: no cast, no
    # coordinates, no salience, but a perfect similarity score.
    embedding_only = SCORER.score(
        _input(
            "hn_sdddddddddddddddddddddd",
            "Nothing here.",
            embedding_similarity=1.0,
        )
    )
    assert action.score_total > embedding_only.score_total
    # The embedding bonus is explicitly capped.
    assert embedding_only.score_breakdown["embedding_bonus"] == (
        DEFAULT_SCENE_POLICY.embedding_bonus_cap
    )
    # The cap is smaller than the genuine salience of a real action scene.
    assert (
        embedding_only.score_total
        < action.score_breakdown["plot_turn"] * DEFAULT_SCENE_POLICY.plot_turn_weight
    )


def test_embedding_alone_does_not_change_relative_order_of_real_scenes():
    action = SCORER.score(_action_input())
    quiet = SCORER.score(_quiet_input())
    base = _action_input()
    action_boosted = SCORER.score(
        SceneScoreInput(
            scene_id=base.scene_id,
            chapter_id=base.chapter_id,
            chapter_number=base.chapter_number,
            source_start=base.source_start,
            source_end=base.source_end,
            source_hash=base.source_hash,
            content=base.content,
            coordinates=base.coordinates,
            embedding_similarity=1.0,
        )
    )
    assert action_boosted.score_total >= action.score_total
    assert action_boosted.score_total > quiet.score_total


# ---------------------------------------------------------------------------
# Diversity quota: action + quiet both enter; dedup overlap penalty
# ---------------------------------------------------------------------------


def test_action_and_quiet_both_enter_diversity_ranking():
    action = SCORER.score(_action_input())
    quiet = SCORER.score(_quiet_input())
    ambiguous = SCORER.score(_ambiguous_input())
    result = rank_with_diversity([action, quiet, ambiguous])
    ordered = {s.scene_id for s in result.ordered}
    assert ordered >= {action.scene_id, quiet.scene_id, ambiguous.scene_id}
    assert any(
        r.reason_code is KeySceneReasonCode.DIVERSITY_QUOTA
        for s in result.ordered
        for r in s.salience_reasons
    )


def test_diversity_groups_split_by_coordinates_and_chapter():
    action = SCORER.score(_action_input())
    quiet = SCORER.score(_quiet_input())
    same_as_quiet = SCORER.score(
        _input(
            "hn_sbbbbbbbbbbbbbbbbbbbbbb",
            QUIET_TEXT,
            coordinates=SceneCoordinates(
                cast=["arin"], place="harbor", time="night", pov="arin"
            ),
        )
    )
    groups = {
        g.key: len(g.items) for g in diversity_groups([action, quiet, same_as_quiet])
    }
    assert len(groups) == 2  # action + quiet share one diversity key
    assert compute_diversity_key(
        action.coordinates, chapter_number=3
    ) != compute_diversity_key(quiet.coordinates, chapter_number=3)


def test_overlapping_duplicate_scene_receives_repetition_penalty():
    winner = SCORER.score(
        _input(
            "hn_saaaaaaaaaaaaaaaaaaaaaa",
            ACTION_TEXT,
            chapter_number=3,
            source_start=0,
            source_end=300,
        )
    )
    duplicate = SCORER.score(
        _input(
            "hn_seeaaaaaaaaaaaaaaaaaaaaa",
            ACTION_TEXT,
            chapter_number=3,
            source_start=20,
            source_end=310,
        )
    )
    result = rank_with_diversity([winner, duplicate])
    dup_ordered = next(s for s in result.ordered if s.scene_id == duplicate.scene_id)
    reasons = {r.reason_code for r in dup_ordered.salience_reasons}
    assert KeySceneReasonCode.REPETITION_PENALTY in reasons
    assert dup_ordered.score_total < duplicate.score_total


# ---------------------------------------------------------------------------
# Advisory speaker/dialogue heuristic (REQ-VIS-06 / D-31-05)
# ---------------------------------------------------------------------------


def test_dialogue_rich_signal_exposes_offsets_confidence_and_ranking_contribution():
    scored = SCORER.score(_action_input())
    signal = scored.heuristic_signal
    assert signal is not None
    assert signal.availability is HeuristicSignalAvailability.AVAILABLE
    assert signal.confidence == 0.9
    assert signal.speaker_offsets
    assert signal.dialogue_offsets
    assert signal.warnings == []
    for offset in signal.dialogue_offsets:
        assert ACTION_TEXT[offset.offset_start : offset.offset_end]
    assert scored.score_breakdown["dialogue_turn"] > 0.0
    assert any(
        r.reason_code is KeySceneReasonCode.DIALOGUE_TURN
        for r in scored.salience_reasons
    )


def test_dialogue_ambiguous_preserves_warnings_and_reduced_confidence():
    scored = SCORER.score(_input("hn_sffffffffffffffffffffff", DIALOGUE_AMBIGUOUS_TEXT))
    signal = scored.heuristic_signal
    assert signal is not None
    assert signal.availability is HeuristicSignalAvailability.AMBIGUOUS
    assert signal.confidence == 0.3
    assert signal.dialogue_offsets
    assert any("unattributed" in warning for warning in signal.warnings)
    assert any(
        r.reason_code is KeySceneReasonCode.AMBIGUITY_WARNING
        for r in scored.salience_reasons
    )


def test_no_dialogue_is_unavailable_never_silent_zero():
    scored = SCORER.score(_quiet_input())
    signal = scored.heuristic_signal
    assert signal is not None
    assert signal.availability is HeuristicSignalAvailability.UNAVAILABLE
    assert signal.confidence is None
    assert signal.speaker_offsets == []
    assert signal.dialogue_offsets == []
    assert "no_dialogue_detected" in signal.warnings
    # Unavailable heuristic contributes nothing and never becomes a zero fact.
    assert scored.score_breakdown["dialogue_turn"] == 0.0
    assert not any(
        r.reason_code is KeySceneReasonCode.DIALOGUE_TURN
        for r in scored.salience_reasons
    )


# ---------------------------------------------------------------------------
# Missing/ambiguous signals stay explicit (never coerced)
# ---------------------------------------------------------------------------


def test_missing_embedding_and_arc_signals_stay_absent_not_fact():
    scored = SCORER.score(_quiet_input())
    assert scored.score_breakdown["embedding_bonus"] == 0.0
    assert scored.score_breakdown["arc_impact"] == 0.0
    assert not any(
        r.reason_code is KeySceneReasonCode.ARC_IMPACT for r in scored.salience_reasons
    )
    arc_scored = SCORER.score(
        _input(
            "hn_sgggggggggggggggggggggg",
            QUIET_TEXT,
            coordinates=_quiet_input().coordinates,
            arc_impact_score=0.8,
        )
    )
    assert arc_scored.score_breakdown["arc_impact"] == 0.8
    assert any(
        r.reason_code is KeySceneReasonCode.ARC_IMPACT
        for r in arc_scored.salience_reasons
    )
    assert arc_scored.score_total > scored.score_total


def test_arc_and_embedding_inputs_change_total_without_reordering_real_salience():
    action = SCORER.score(_action_input())
    arc_only = SCORER.score(
        _input(
            "hn_shhhhhhhhhhhhhhhhhhhhhh",
            "Nothing here.",
            arc_impact_score=1.0,
            embedding_similarity=1.0,
        )
    )
    # Even maximum arc + embedding on an empty scene cannot beat a real scene.
    assert action.score_total > arc_only.score_total


# ---------------------------------------------------------------------------
# ScoredScene → deterministic diversity key
# ---------------------------------------------------------------------------


def test_diversity_key_is_deterministic_and_stable():
    a = compute_diversity_key(
        SceneCoordinates(cast=["arin"], place="courtyard", time="night"),
        chapter_number=3,
    )
    b = compute_diversity_key(
        SceneCoordinates(cast=["arin"], place="courtyard", time="night"),
        chapter_number=3,
    )
    assert a == b
    assert a != compute_diversity_key(
        SceneCoordinates(cast=["mara"], place="harbor", time="dawn"),
        chapter_number=3,
    )
    assert a != compute_diversity_key(
        SceneCoordinates(cast=["arin"], place="courtyard", time="night"),
        chapter_number=4,
    )
