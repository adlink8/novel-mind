"""Multi-signal deterministic key-scene scoring (Phase 31-02, REQ-VIS-02/06).

D-31-03: ranking MUST combine narrative salience and diversity/coverage;
embedding similarity alone is insufficient. This module owns the pure,
deterministic scoring policy and diversity re-ranking:

- ``KeySceneScoringPolicy`` — versioned weights + thresholds; its canonical
  ``policy_hash`` is recorded on every candidate so scoring inputs are
  replayable (deterministic scoring inputs are versioned).
- ``KeySceneScorer.score`` — combines plot-turn, emotional-peak /
  quiet-emotional, visual expressiveness, character salience, coordinate
  coverage and the advisory speaker/dialogue heuristic signal into a weighted
  total with inspectable salience reasons drawn from the closed vocabulary.
  A missing/ambiguous heuristic stays ``unavailable``/``ambiguous`` with
  explicit warnings (D-31-05) and never silently becomes a score or a fact.
- embedding similarity is accepted as ONE input among many and capped at a
  small bonus; a candidate whose only strength is embedding similarity can
  never outrank a candidate with genuine salience.
- ``compute_diversity_key`` / ``diversity_groups`` / ``rank_with_diversity`` —
  deterministic diversity keying, repetition overlap penalty and a diversity
  quota round so action AND quiet/emotional scenes both survive the budget.

Nothing here writes to the database and nothing here creates Canon: scoring
only produces ranked candidate evidence packages for ``candidates.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Sequence

from app.schemas.key_scene import (
    HeuristicSignalAvailability,
    KeySceneReasonCode,
    SalienceReason,
    SceneCoordinates,
    SpeakerDialogueHeuristicSignal,
    canonical_key_scene_hash,
)
from app.services.key_scenes.boundaries import detect_dialogue_heuristic

# Advisory lexical heuristics only: deterministic signal extraction over the
# scene's own source text. They are never factual claims or citation authority.
_ACTION_TERMS = (
    "attack",
    "attacked",
    "fled",
    "drew his sword",
    "drew her sword",
    "turned to face",
    "charged",
    "struck",
    "pursued",
    "exploded",
    "struck the",
    "!" ,
    "！",
    "拔剑",
    "冲锋",
    "追击",
    "爆发",
    "厮杀",
    "逃亡",
    "反攻",
)
_EMOTION_TERMS = (
    "terror",
    "rage",
    "grief",
    "joy",
    "fear",
    "anger",
    "despair",
    "heart",
    "wept",
    "cried",
    "sorrow",
    "恐惧",
    "愤怒",
    "悲伤",
    "绝望",
    "欣喜",
    "心碎",
    "痛哭",
)
_QUIET_TERMS = (
    "quiet",
    "calm",
    "silence",
    "still",
    "whispered",
    "watched",
    "thought",
    "remembered",
    "平静",
    "沉默",
    "凝望",
    "低语",
    "回忆",
)
_VISUAL_TERMS = (
    "torch",
    "torches",
    "moonlight",
    "shadow",
    "shadow",
    "glittered",
    "gleamed",
    "pale light",
    "harbor lights",
    "firelight",
    "火把",
    "月光",
    "阴影",
    "微光",
    "灯火",
    "长明灯",
)
_QUOTE_PATTERN = re.compile(r"[\"\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f]")

_ACTION_SCENE_ACTION_THRESHOLD = 0.15
_NORMALIZATION_TARGET = 3  # matches per ~150-char scene before capping at 1.0


@dataclass(frozen=True)
class SceneScoreInput:
    """One evidence package the scorer may use; the scorer invents nothing.

    ``coordinates`` must come from a source-verified package; ``heuristic_signal``
    is advisory metadata from ``boundaries.detect_dialogue_heuristic``; the
    optional ``embedding_similarity`` / ``arc_impact_score`` are inputs among
    many and their absence stays absent (never silently a zero fact).
    """

    scene_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    source_hash: str
    content: str
    coordinates: SceneCoordinates = field(default_factory=SceneCoordinates)
    heuristic_signal: SpeakerDialogueHeuristicSignal | None = None
    embedding_similarity: float | None = None
    arc_impact_score: float | None = None


@dataclass(frozen=True)
class KeySceneScoringPolicy:
    """Versioned deterministic scoring policy (weights sum to 1.0 when all
    signals are available; unavailable signals simply do not contribute)."""

    version: str = "key-scene-scorer.v1"
    plot_turn_weight: float = 0.25
    emotion_weight: float = 0.20
    character_salience_weight: float = 0.15
    coverage_weight: float = 0.15
    visual_weight: float = 0.05
    dialogue_weight: float = 0.05
    arc_impact_weight: float = 0.15
    # Evidence-verified boundary base; every candidate that reaches the scorer
    # has one, so the score breakdown and total stay consistent/inspectable.
    evidence_base: float = 0.05
    # Embedding similarity is one input among many; its total contribution is
    # capped so similarity alone can never decide the ranking (D-31-03).
    embedding_bonus_cap: float = 0.05
    reason_threshold: float = 0.05
    overlap_threshold: float = 0.55
    diversity_bonus: float = 0.10
    max_candidates: int = 24

    def payload(self) -> dict:
        return {
            "version": self.version,
            "plot_turn_weight": self.plot_turn_weight,
            "emotion_weight": self.emotion_weight,
            "character_salience_weight": self.character_salience_weight,
            "coverage_weight": self.coverage_weight,
            "visual_weight": self.visual_weight,
            "dialogue_weight": self.dialogue_weight,
            "arc_impact_weight": self.arc_impact_weight,
            "evidence_base": self.evidence_base,
            "embedding_bonus_cap": self.embedding_bonus_cap,
            "reason_threshold": self.reason_threshold,
            "overlap_threshold": self.overlap_threshold,
            "diversity_bonus": self.diversity_bonus,
            "max_candidates": self.max_candidates,
        }


DEFAULT_SCENE_POLICY = KeySceneScoringPolicy()


def policy_hash(policy: KeySceneScoringPolicy = DEFAULT_SCENE_POLICY) -> str:
    """Canonical hash of the versioned scoring policy (recorded per candidate)."""
    return canonical_key_scene_hash({"kind": "key_scene.scoring_policy"} | policy.payload())


@dataclass(frozen=True)
class ScoredScene:
    """One scored scene: ranked candidate evidence package (never Canon)."""

    scene_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    source_hash: str
    content: str
    coordinates: SceneCoordinates
    diversity_key: str
    score_total: float
    score_breakdown: dict
    salience_reasons: tuple[SalienceReason, ...]
    heuristic_signal: SpeakerDialogueHeuristicSignal | None


@dataclass(frozen=True)
class DiversityGroup:
    key: str
    items: tuple[ScoredScene, ...]


@dataclass(frozen=True)
class DiversityResult:
    ordered: tuple[ScoredScene, ...]
    groups: tuple[DiversityGroup, ...]
    reason_codes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Deterministic lexical signal extraction (advisory, not canon)
# ---------------------------------------------------------------------------


def _term_hits(text: str, terms: Sequence[str]) -> int:
    return sum(text.count(term) for term in terms)


def _density(text: str, terms: Sequence[str]) -> float:
    if not text.strip():
        return 0.0
    hits = _term_hits(text, terms)
    return min(1.0, hits / _NORMALIZATION_TARGET)


def _action_density(text: str) -> float:
    return _density(text, _ACTION_TERMS)


def _emotion_density(text: str) -> float:
    return _density(text, _EMOTION_TERMS)


def _quiet_density(text: str) -> float:
    return _density(text, _QUIET_TERMS)


def _visual_density(text: str) -> float:
    return _density(text, _VISUAL_TERMS)


def _coverage_score(coordinates: SceneCoordinates) -> float:
    """Deterministic coordinate coverage (cast/place/time/pov) in [0, 1]."""
    filled = sum(
        1
        for value in (coordinates.place, coordinates.time, coordinates.pov)
        if value
    )
    filled += min(len(coordinates.cast), 3)
    return min(1.0, filled / 6.0)


def _character_salience_score(coordinates: SceneCoordinates) -> float:
    return min(1.0, len(coordinates.cast) / 3.0)


def _dialogue_contribution(
    signal: SpeakerDialogueHeuristicSignal | None,
) -> float:
    """Advisory dialogue contribution to ranking (D-31-05).

    ``unavailable`` contributes nothing and the caller keeps the explicit
    unavailable metadata; ``ambiguous`` contributes a reduced amount.
    """
    if signal is None or signal.availability is HeuristicSignalAvailability.UNAVAILABLE:
        return 0.0
    if signal.availability is HeuristicSignalAvailability.AMBIGUOUS:
        return round((signal.confidence or 0.0) * 0.5, 6)
    return round(signal.confidence or 0.0, 6)


# ---------------------------------------------------------------------------
# Diversity keying and overlap penalty
# ---------------------------------------------------------------------------


def compute_diversity_key(
    coordinates: SceneCoordinates,
    *,
    chapter_number: int,
) -> str:
    """Deterministic diversity identity: chapter + place/time/pov + top cast.

    Scenes in the same chapter with distinct location/time/POV/cast are
    diversity-distinct; repeated motifs collapse onto one key so a repeated
    scene cannot consume every budget slot.
    """
    cast_key = "|".join(sorted(coordinates.cast[:2])) or "?"
    return canonical_key_scene_hash(
        {
            "kind": "key_scene.diversity",
            "chapter": chapter_number,
            "place": coordinates.place or "?",
            "time": coordinates.time or "?",
            "pov": coordinates.pov or "?",
            "cast": cast_key,
        }
    )


def _overlap_ratio(a: ScoredScene, b: ScoredScene) -> float:
    if a.chapter_number != b.chapter_number:
        return 0.0
    lo = max(a.source_start, b.source_start)
    hi = min(a.source_end, b.source_end)
    if hi <= lo:
        return 0.0
    a_len = a.source_end - a.source_start
    b_len = b.source_end - b.source_start
    if a_len <= 0 or b_len <= 0:
        return 0.0
    # Overlap relative to the smaller range: a repeated motif that is mostly
    # inside a stronger scene is heavily penalized.
    return (hi - lo) / min(a_len, b_len)


def diversity_groups(
    scored: Sequence[ScoredScene],
) -> tuple[DiversityGroup, ...]:
    """Group scored scenes by their deterministic diversity key (sorted keys)."""
    grouped: dict[str, list[ScoredScene]] = {}
    for item in scored:
        grouped.setdefault(item.diversity_key, []).append(item)
    return tuple(
        DiversityGroup(key=key, items=tuple(grouped[key]))
        for key in sorted(grouped)
    )


def _apply_overlap_penalties(
    scored: Sequence[ScoredScene],
    *,
    policy: KeySceneScoringPolicy,
) -> list[ScoredScene]:
    """Apply a repetition penalty to scenes overlapping a higher-scoring one.

    The penalty is a deterministic score reduction plus an inspectable
    ``repetition_penalty`` reason (the reason score stays None so the closed
    vocabulary's non-negative score bound holds).
    """
    ordered = sorted(
        scored,
        key=lambda s: (-s.score_total, s.chapter_number, s.source_start, s.scene_id),
    )
    result: list[ScoredScene] = []
    for item in ordered:
        penalized = item
        for prev in result:
            overlap = _overlap_ratio(prev, penalized)
            if overlap < policy.overlap_threshold:
                continue
            penalty = round(policy.diversity_bonus * overlap, 6)
            reasons = list(penalized.salience_reasons)
            reasons.append(
                SalienceReason(
                    reason_code=KeySceneReasonCode.REPETITION_PENALTY,
                    detail=(
                        f"overlaps {prev.scene_id} by {overlap:.2f}; "
                        "repeated motif (advisory, not canon)"
                    ),
                )
            )
            breakdown = dict(penalized.score_breakdown)
            breakdown["repetition_penalty"] = -penalty
            penalized = replace(
                penalized,
                score_total=round(max(0.0, penalized.score_total - penalty), 6),
                salience_reasons=tuple(reasons),
                score_breakdown=breakdown,
            )
            break
        result.append(penalized)
    return result


def rank_with_diversity(
    scored: Sequence[ScoredScene],
    *,
    policy: KeySceneScoringPolicy = DEFAULT_SCENE_POLICY,
) -> DiversityResult:
    """Deterministic diversity-aware ranking (D-31-03).

    - repetition overlap penalty (dedup),
    - diversity quota round: the best candidate of every diversity group is
      retained (with a ``diversity_quota`` reason) so action and quiet/emotional
      scenes both survive,
    - remaining budget filled by score, then canonical stable ordering
      (score desc, then chapter/source).
    """
    penalized = _apply_overlap_penalties(scored, policy=policy)
    by_group: dict[str, list[ScoredScene]] = {}
    for item in penalized:
        by_group.setdefault(item.diversity_key, []).append(item)

    selected: list[ScoredScene] = []
    for key in sorted(by_group):
        winner = by_group[key][0]
        reasons = list(winner.salience_reasons)
        if not any(
            reason.reason_code is KeySceneReasonCode.DIVERSITY_QUOTA
            for reason in reasons
        ):
            reasons.append(
                SalienceReason(
                    reason_code=KeySceneReasonCode.DIVERSITY_QUOTA,
                    detail=f"retained as representative of diversity group {key}",
                )
            )
        selected.append(replace(winner, salience_reasons=tuple(reasons)))

    # Fill remaining budget with the rest of each group by score.
    rest = [
        item
        for key in sorted(by_group)
        for item in by_group[key][1:]
    ]
    rest.sort(key=lambda s: (-s.score_total, s.chapter_number, s.source_start, s.scene_id))
    for item in rest:
        if len(selected) >= policy.max_candidates:
            break
        selected.append(item)

    selected.sort(
        key=lambda s: (-s.score_total, s.chapter_number, s.source_start, s.scene_id)
    )
    reason_codes: list[str] = []
    if any(
        reason.reason_code is KeySceneReasonCode.REPETITION_PENALTY
        for item in selected
        for reason in item.salience_reasons
    ):
        reason_codes.append("repetition_penalty")
    if any(
        reason.reason_code is KeySceneReasonCode.DIVERSITY_QUOTA
        for item in selected
        for reason in item.salience_reasons
    ):
        reason_codes.append("diversity_quota")
    return DiversityResult(
        ordered=tuple(selected),
        groups=diversity_groups(scored),
        reason_codes=tuple(reason_codes),
    )


# ---------------------------------------------------------------------------
# Multi-signal scorer
# ---------------------------------------------------------------------------


class KeySceneScorer:
    """Pure deterministic scorer; no DB access, no side effects."""

    def __init__(
        self,
        policy: KeySceneScoringPolicy = DEFAULT_SCENE_POLICY,
        *,
        detector_id: str,
        detector_version: str,
    ) -> None:
        self._policy = policy
        self._detector_id = detector_id
        self._detector_version = detector_version

    @property
    def policy(self) -> KeySceneScoringPolicy:
        return self._policy

    @property
    def policy_hash_value(self) -> str:
        return policy_hash(self._policy)

    def score(self, input_: SceneScoreInput) -> ScoredScene:
        """Deterministically score one evidence package (never invents facts)."""
        policy = self._policy
        action = round(_action_density(input_.content), 6)
        emotion = round(_emotion_density(input_.content), 6)
        quiet = round(_quiet_density(input_.content), 6)
        visual = round(_visual_density(input_.content), 6)
        character_salience = round(_character_salience_score(input_.coordinates), 6)
        coverage = round(_coverage_score(input_.coordinates), 6)

        # Emotional peak vs quiet-emotional are alternative interpretations of
        # the same emotion signal (action scenes: peak; otherwise: quiet).
        is_action = action >= _ACTION_SCENE_ACTION_THRESHOLD
        emotional_peak = emotion if is_action else 0.0
        quiet_emotional = quiet if not is_action else 0.0

        heuristic = input_.heuristic_signal
        if heuristic is None:
            heuristic = detect_dialogue_heuristic(
                input_.content,
                detector_id=self._detector_id,
                detector_version=self._detector_version,
            )
        dialogue = _dialogue_contribution(heuristic)

        arc = round(input_.arc_impact_score or 0.0, 6)
        embedding = round(input_.embedding_similarity or 0.0, 6)
        embedding_bonus = round(
            min(policy.embedding_bonus_cap, embedding * policy.embedding_bonus_cap), 6
        )

        raw = round(
            policy.evidence_base
            + policy.plot_turn_weight * action
            + policy.emotion_weight * emotional_peak
            + policy.character_salience_weight * character_salience
            + policy.coverage_weight * coverage
            + policy.visual_weight * visual
            + policy.dialogue_weight * dialogue
            + policy.arc_impact_weight * arc,
            6,
        )
        total = round(raw + embedding_bonus, 6)

        breakdown = {
            "evidence_boundary": policy.evidence_base,
            "plot_turn": action,
            "emotional_peak": emotional_peak,
            "quiet_emotional": quiet_emotional,
            "character_salience": character_salience,
            "coverage": coverage,
            "visual_expressiveness": visual,
            "dialogue_turn": dialogue,
            "arc_impact": arc,
            "embedding_bonus": embedding_bonus,
            "policy_version": policy.version,
        }

        reasons = self._build_reasons(
            action=action,
            emotional_peak=emotional_peak,
            quiet_emotional=quiet_emotional,
            character_salience=character_salience,
            visual=visual,
            dialogue=dialogue,
            arc=arc,
            heuristic=heuristic,
        )

        return ScoredScene(
            scene_id=input_.scene_id,
            chapter_id=input_.chapter_id,
            chapter_number=input_.chapter_number,
            source_start=input_.source_start,
            source_end=input_.source_end,
            source_hash=input_.source_hash,
            content=input_.content,
            coordinates=input_.coordinates,
            diversity_key=compute_diversity_key(
                input_.coordinates, chapter_number=input_.chapter_number
            ),
            score_total=total,
            score_breakdown=breakdown,
            salience_reasons=tuple(reasons),
            heuristic_signal=heuristic,
        )

    def _build_reasons(
        self,
        *,
        action: float,
        emotional_peak: float,
        quiet_emotional: float,
        character_salience: float,
        visual: float,
        dialogue: float,
        arc: float,
        heuristic: SpeakerDialogueHeuristicSignal,
    ) -> list[SalienceReason]:
        threshold = self._policy.reason_threshold
        reasons: list[SalienceReason] = [
            SalienceReason(
                reason_code=KeySceneReasonCode.EVIDENCE_BOUNDARY,
                detail="verified scene boundary from the persisted hierarchy",
                score=self._policy.evidence_base,
            )
        ]

        def add(code: KeySceneReasonCode, detail: str, score: float) -> None:
            if score >= threshold:
                reasons.append(
                    SalienceReason(reason_code=code, detail=detail, score=round(score, 6))
                )

        add(
            KeySceneReasonCode.PLOT_TURN,
            "action/turn signal in the scene source",
            action,
        )
        if emotional_peak >= threshold:
            add(
                KeySceneReasonCode.EMOTIONAL_PEAK,
                "emotional intensity inside an action scene",
                emotional_peak,
            )
        if quiet_emotional >= threshold:
            add(
                KeySceneReasonCode.QUIET_EMOTIONAL,
                "quiet scene with emotional/character salience",
                quiet_emotional,
            )
        add(
            KeySceneReasonCode.CHARACTER_SALIENCE,
            "cast richness from the source-verified coordinates",
            character_salience,
        )
        add(
            KeySceneReasonCode.VISUAL_EXPRESSIVENESS,
            "visual description density in the scene source",
            visual,
        )
        if dialogue > 0.0:
            add(
                KeySceneReasonCode.DIALOGUE_TURN,
                "advisory speaker/dialogue contribution (not evidence)",
                dialogue,
            )
        if arc > 0.0:
            add(
                KeySceneReasonCode.ARC_IMPACT,
                "arc-impact typed-fact overlap (advisory)",
                arc,
            )
        if heuristic.availability is HeuristicSignalAvailability.AMBIGUOUS:
            reasons.append(
                SalienceReason(
                    reason_code=KeySceneReasonCode.AMBIGUITY_WARNING,
                    detail="; ".join(heuristic.warnings) or "ambiguous attribution",
                )
            )
        return reasons
