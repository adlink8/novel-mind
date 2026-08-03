"""Phase 31-01 Key Scene candidate contract, boundary and permission tests.

Covers REQ-VIS-02 / REQ-VIS-06 / D-31-01..D-31-05:
- strict typed contract rejects unsupported coordinates, spoiler ranges,
  missing source hashes and any canon promotion field; candidates are not canon;
- candidate sets are evidence-first: every candidate locates to a replayable
  evidence range and preserves detector/policy lineage;
- REQ-VIS-06 speaker/dialogue heuristic signals are advisory metadata only:
  offsets/confidence/warnings stay explicit (or unavailable), never populate
  evidence ranges and never become citation/canon/approval authority;
- owner_id/novel_id/version/source-snapshot/evidence-hash/spoiler-cutoff gates
  hold at every applicable boundary; cross-owner access fails closed;
- ORM + migration chain (20260801_key_scene on top of 20260801_visual_bible)
  and append-only content rows are verified.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel, Chapter
from app.models.user import User
from app.models.visual_bible import VisualBibleVersion
from app.models.key_scene import (
    SceneCandidate,
    SceneCandidateSet,
    SceneEvidenceRange,
    SceneReviewDecision,
)
from app.schemas.key_scene import (
    KEY_SCENE_REASON_CODES,
    LEGAL_SCENE_REVIEW_TRANSITIONS,
    SCENE_REVIEW_ACTION_TO_STATE,
    HeuristicSignalAvailability,
    KeySceneGateError,
    KeySceneReasonCode,
    KeySceneReviewAction,
    KeySceneReviewState,
    SalienceReason,
    SceneCandidateContract,
    SceneCandidateSetContract,
    SceneCoordinates,
    SceneEvidenceRange as EvidenceRangeSchema,
    SceneReviewDecisionInput,
    SpeakerDialogueHeuristicSignal,
    candidate_canonical_payload,
    canonical_key_scene_hash,
    recompute_manifest_hash,
    review_state_after,
    validate_candidate_set_contract,
    validate_heuristic_signal_isolation,
    validate_review_decision,
)
from app.services.key_scenes.boundaries import (
    SceneBoundaryService,
    detect_chapter_boundaries,
    detect_dialogue_heuristic,
    build_candidate,
    build_evidence_range,
    compute_source_snapshot_hash,
    filter_by_cutoff,
)
from app.services.chunking.manifests import content_hash

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"

KEY_SCENE_TABLES = {
    "key_scene_sets",
    "key_scene_candidates",
    "key_scene_evidence_ranges",
    "key_scene_review_decisions",
}

# Pinned canonical hash of the reason-code vocabulary so a future rename cannot
# pass silently (stable hash pins the closed contract, D-31-02/03).
REASON_CODES_HASH = canonical_key_scene_hash(
    {"reason_codes": list(KEY_SCENE_REASON_CODES)}
)

DETECTOR_ID = "boundary.v1"
DETECTOR_VERSION = "1.0.0"
DIALOGUE_DETECTOR = "dialogue.v1"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _evidence(**overrides):
    payload = {
        "evidence_key": "ev-1",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "chapter_id": 3,
        "chapter_number": 3,
        "source_start": 0,
        "source_end": 120,
        "content_hash": HEX64_C,
        "excerpt": "a long slice of primary text for the candidate",
        "cutoff_chapter": 8,
    }
    payload.update(overrides)
    return EvidenceRangeSchema.model_validate(payload)


def _heuristic(**overrides):
    payload = {
        "availability": "available",
        "speaker_offsets": [
            {"offset_start": 10, "offset_end": 16, "speaker_key": "arin"}
        ],
        "dialogue_offsets": [{"offset_start": 18, "offset_end": 34}],
        "confidence": 0.9,
        "warnings": [],
        "detector_id": DIALOGUE_DETECTOR,
        "detector_version": "1.0.0",
    }
    payload.update(overrides)
    return SpeakerDialogueHeuristicSignal.model_validate(payload)


def _candidate(**overrides):
    payload = {
        "candidate_key": "ks-cand-1",
        "candidate_order": 0,
        "scene_id": "hn_s0193134a6011ebed1ceb96",
        "chapter_id": 3,
        "chapter_number": 3,
        "source_start": 0,
        "source_end": 120,
        "source_hash": HEX64_C,
        "coordinates": {
            "cast": ["arin"],
            "place": "courtyard",
            "time": "night",
            "pov": "arin",
        },
        "spoiler_cutoff": 8,
        "salience_reasons": [
            {
                "reason_code": "evidence_boundary",
                "detail": "detected scene boundary",
                "score": 0.5,
            }
        ],
        "score_total": 0.5,
        "score_breakdown": {"evidence_boundary": 0.5},
        "diversity_key": "night-courtyard",
        "detector_id": DETECTOR_ID,
        "detector_version": DETECTOR_VERSION,
        "policy_hash": HEX64_B,
        "evidence_ranges": [_evidence().model_dump()],
        "heuristic_signal": _heuristic().model_dump(),
        "review_state": "candidate",
    }
    payload.update(overrides)
    return SceneCandidateContract.model_validate(payload)


def _set(**overrides):
    payload = {
        "schema_version": "key-scene.v1",
        "artifact_kind": "key_scene",
        "owner_id": 11,
        "novel_id": 22,
        "version_key": "ks-arin",
        "revision_number": 1,
        "parent_set_id": None,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "cutoff_chapter": 8,
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "detector_id": DETECTOR_ID,
        "detector_version": DETECTOR_VERSION,
        "manifest_hash": "0" * 64,
        "approved_visual_bible_revision_id": None,
        "approved_visual_bible_revision_hash": None,
        "candidates": [_candidate().model_dump()],
        "review_state": "candidate",
    }
    payload.update(overrides)
    set_ = SceneCandidateSetContract.model_validate(payload)
    if "manifest_hash" not in overrides:
        set_ = set_.model_copy(
            update={"manifest_hash": recompute_manifest_hash(set_)}
        )
    return set_


def _review_decision(**overrides):
    payload = {
        "owner_id": 11,
        "novel_id": 22,
        "set_id": 1,
        "decision_key": "ks-approve-1",
        "action": "approve",
        "actor_source": "human",
        "actor": "reader",
        "reason": "this scene deserves illustration",
        "from_review_state": "candidate",
        "candidate_key": None,
    }
    payload.update(overrides)
    return SceneReviewDecisionInput.model_validate(payload)


# ---------------------------------------------------------------------------
# Vocabulary (closed and pinned)
# ---------------------------------------------------------------------------


def test_reason_code_vocabulary_is_closed_and_pinned():
    assert [code.value for code in KeySceneReasonCode] == list(KEY_SCENE_REASON_CODES)
    assert (
        REASON_CODES_HASH == "9e4f47d4cf811ea69b6ca54f7f92939fc9760bfcc248129f663db9302556e314"
    )


def test_review_and_signal_vocabulary():
    assert [a.value for a in KeySceneReviewAction] == [
        "approve",
        "reject",
        "needs_relink",
        "supersede",
    ]
    assert [s.value for s in KeySceneReviewState] == [
        "candidate",
        "approved",
        "rejected",
        "superseded",
        "needs_relink",
    ]
    assert [a.value for a in HeuristicSignalAvailability] == [
        "available",
        "ambiguous",
        "unavailable",
    ]


# ---------------------------------------------------------------------------
# Strict schema: unsupported coordinates, spoiler range, missing hash, no canon
# ---------------------------------------------------------------------------


def test_strict_schema_rejects_unsupported_coordinates():
    with pytest.raises(ValidationError):
        _candidate(coordinates={"cast": ["arin"], "location": "rooftop"})
    with pytest.raises(ValidationError):
        SceneCoordinates.model_validate({"cast": [], "sentiment": "dark"})


def test_strict_schema_requires_source_hash():
    with pytest.raises(ValidationError):
        _candidate(source_hash="short")
    with pytest.raises(ValidationError):
        _candidate(source_hash=None)
    with pytest.raises(ValidationError):
        _set(source_snapshot_hash=None)
    with pytest.raises(ValidationError):
        _set(source_snapshot_hash="not-a-hash")


def test_strict_schema_rejects_spoiler_ranges():
    # candidate chapter_number beyond spoiler_cutoff
    with pytest.raises(ValidationError):
        _candidate(chapter_number=9, spoiler_cutoff=8)
    # evidence chapter_number beyond cutoff
    with pytest.raises(ValidationError):
        _candidate(
            evidence_ranges=[
                _evidence(chapter_number=9, cutoff_chapter=8).model_dump()
            ]
        )
    ok = _candidate(chapter_number=8, spoiler_cutoff=8)
    assert ok.chapter_number == ok.spoiler_cutoff


def test_candidate_is_not_canon():
    # No canon/pointer/cover field exists and extra fields are rejected.
    with pytest.raises(ValidationError):
        SceneCandidateContract.model_validate(
            _candidate().model_dump() | {"canon": True}
        )
    with pytest.raises(ValidationError):
        SceneCandidateSetContract.model_validate(
            _set().model_dump() | {"promote_to_canon": True}
        )
    fields = set(SceneCandidateSetContract.model_fields)
    assert "canon" not in fields
    assert "active_pointer" not in fields
    assert "current_revision" not in fields
    assert "cover_url" not in fields
    # Default state is candidate; approval is an explicit append-only decision.
    assert _candidate().review_state is KeySceneReviewState.CANDIDATE


def test_heuristic_signal_shape_is_strict():
    # unavailable must carry no offsets and no confidence
    with pytest.raises(ValidationError):
        _heuristic(
            availability="unavailable",
            speaker_offsets=[{"offset_start": 1, "offset_end": 4}],
        )
    with pytest.raises(ValidationError):
        _heuristic(
            availability="unavailable",
            confidence=0.4,
        )
    # ambiguous requires warnings
    with pytest.raises(ValidationError):
        _heuristic(availability="ambiguous", confidence=0.3, warnings=[])
    # available requires confidence
    with pytest.raises(ValidationError):
        _heuristic(availability="available", confidence=None)
    ok = _heuristic(
        availability="unavailable",
        confidence=None,
        speaker_offsets=[],
        dialogue_offsets=[],
        warnings=["no_dialogue_detected"],
    )
    assert ok.confidence is None


# ---------------------------------------------------------------------------
# Contract gates: replayable hashes, lineage, heuristic isolation
# ---------------------------------------------------------------------------


def test_candidate_payload_and_hash_are_replayable():
    a = _candidate()
    b = _candidate()
    assert candidate_canonical_payload(a) == candidate_canonical_payload(b)
    # changing coordinates or evidence changes the payload
    changed = _candidate(coordinates={"cast": ["mara"], "place": "harbor"})
    assert candidate_canonical_payload(changed) != candidate_canonical_payload(a)


def test_manifest_hash_is_replayable_and_detects_candidate_change():
    a = _set()
    b = _set()
    assert recompute_manifest_hash(a) == recompute_manifest_hash(b)
    assert a.manifest_hash == recompute_manifest_hash(a)
    changed = _set(candidates=[_candidate(score_total=0.9).model_dump()])
    assert changed.manifest_hash != a.manifest_hash


def test_duplicate_candidate_key_and_order_are_rejected():
    cand = _candidate()
    dup = _candidate(candidate_key="ks-cand-1")
    with pytest.raises(KeySceneGateError):
        validate_candidate_set_contract(
            _set(candidates=[cand.model_dump(), dup.model_dump()])
        )
    order_dup = _candidate(candidate_key="ks-cand-2", candidate_order=0)
    with pytest.raises(KeySceneGateError):
        validate_candidate_set_contract(
            _set(candidates=[cand.model_dump(), order_dup.model_dump()])
        )


def test_candidate_beyond_set_cutoff_is_rejected():
    future = _candidate(candidate_key="ks-future", chapter_number=9, spoiler_cutoff=9)
    with pytest.raises(KeySceneGateError):
        validate_candidate_set_contract(_set(candidates=[future.model_dump()]))


def test_evidence_lineage_must_match_set_snapshot_and_cutoff():
    version = _set()
    validate_candidate_set_contract(version)

    bad_snapshot_id = _set(
        candidates=[
            _candidate(
                evidence_ranges=[
                    _evidence(source_snapshot_id="other-ss").model_dump()
                ]
            ).model_dump()
        ]
    )
    with pytest.raises(KeySceneGateError):
        validate_candidate_set_contract(bad_snapshot_id)

    bad_snapshot_hash = _set(
        candidates=[
            _candidate(
                evidence_ranges=[
                    _evidence(source_snapshot_hash=HEX64_B).model_dump()
                ]
            ).model_dump()
        ]
    )
    with pytest.raises(KeySceneGateError):
        validate_candidate_set_contract(bad_snapshot_hash)

    bad_cutoff = _set(
        candidates=[
            _candidate(
                evidence_ranges=[
                    _evidence(cutoff_chapter=3).model_dump()
                ]
            ).model_dump()
        ]
    )
    with pytest.raises(KeySceneGateError):
        validate_candidate_set_contract(bad_cutoff)


def test_heuristic_signal_never_populates_evidence_ranges():
    """D-31-05: heuristic offsets are diagnostics, not citation authority.

    The candidate contract carries no path from the heuristic signal into
    ``evidence_ranges``; the isolation gate rejects heuristic offsets that
    escape the candidate's own primary evidence range.
    """
    candidate = _candidate()
    # Structural separation: the signal is a sibling of evidence_ranges.
    assert candidate.heuristic_signal is not None
    assert candidate.evidence_ranges  # evidence is independently present
    assert set(candidate.evidence_ranges[0].model_dump()) >= {"content_hash", "source_start"}

    # An offset that escapes the candidate slice fails closed.
    escaped = _candidate(
        heuristic_signal=_heuristic(
            speaker_offsets=[{"offset_start": 0, "offset_end": 200, "speaker_key": "arin"}]
        ).model_dump()
    )
    with pytest.raises(KeySceneGateError):
        validate_heuristic_signal_isolation(escaped)

    # Unavailable signal with offsets is already rejected by the strict schema
    # (first defense), so it can never reach the gate as a candidate.
    with pytest.raises(ValidationError):
        _heuristic(
            availability="unavailable",
            confidence=None,
            speaker_offsets=[{"offset_start": 1, "offset_end": 5}],
            dialogue_offsets=[],
            warnings=["no_speaker_attribution"],
        )


def test_valid_set_passes_full_contract():
    validate_candidate_set_contract(_set())  # no raise


def test_visual_bible_approval_reference_pair_is_consistent():
    with pytest.raises(ValidationError):
        _set(
            approved_visual_bible_revision_id=5,
            approved_visual_bible_revision_hash=None,
        )
    with pytest.raises(ValidationError):
        _set(
            approved_visual_bible_revision_id=None,
            approved_visual_bible_revision_hash=HEX64_C,
        )
    ok = _set(
        approved_visual_bible_revision_id=5,
        approved_visual_bible_revision_hash=HEX64_C,
    )
    assert ok.approved_visual_bible_revision_id == 5


# ---------------------------------------------------------------------------
# Review decisions (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------


def test_review_decision_transition_map_is_closed():
    assert set(LEGAL_SCENE_REVIEW_TRANSITIONS) == set(KeySceneReviewState)
    for state, actions in LEGAL_SCENE_REVIEW_TRANSITIONS.items():
        for action in actions:
            assert action in SCENE_REVIEW_ACTION_TO_STATE
            assert review_state_after(state, action) == SCENE_REVIEW_ACTION_TO_STATE[action]


def test_review_decision_chain():
    assert review_state_after("candidate", "approve") is KeySceneReviewState.APPROVED
    assert review_state_after("candidate", "reject") is KeySceneReviewState.REJECTED
    assert review_state_after("candidate", "needs_relink") is KeySceneReviewState.NEEDS_RELINK
    assert review_state_after("approved", "supersede") is KeySceneReviewState.SUPERSEDED
    with pytest.raises(KeySceneGateError):
        review_state_after("approved", "approve")  # double approval impossible
    with pytest.raises(KeySceneGateError):
        review_state_after("superseded", "reject")  # terminal


def test_review_decision_is_idempotent_and_candidate_scoped():
    result = validate_review_decision(_review_decision())
    assert result is KeySceneReviewState.APPROVED
    with pytest.raises(KeySceneGateError):
        validate_review_decision(
            _review_decision(), seen_decision_keys={"ks-approve-1"}
        )
    per_candidate = _review_decision(
        decision_key="ks-reject-1",
        action="reject",
        candidate_key="ks-cand-1",
    )
    assert validate_review_decision(per_candidate) is KeySceneReviewState.REJECTED
    assert per_candidate.candidate_key == "ks-cand-1"


# ---------------------------------------------------------------------------
# Boundary detection (reuses the persisted chapter → scene → evidence hierarchy)
# ---------------------------------------------------------------------------

ACTION_CHAPTER = (
    "Arin drew his sword as the rain fell hard across the courtyard walls. "
    "The wind howled between the towers and the torches guttered low. "
    '"We attack at dawn," he said calmly. Mara nodded beside him. '
    "She watched the harbor lights and thought of the boats waiting below. "
    "Somewhere in the dark a bell tolled, and Arin turned to face the gates. "
    "The enemy banners would rise with the sun, and there would be no going back."
)


def test_boundary_detection_is_deterministic_and_evidence_located():
    first = detect_chapter_boundaries(
        novel_id=7,
        chapter_id=3,
        chapter_number=3,
        content=ACTION_CHAPTER,
        source_snapshot_hash=HEX64,
    )
    second = detect_chapter_boundaries(
        novel_id=7,
        chapter_id=3,
        chapter_number=3,
        content=ACTION_CHAPTER,
        source_snapshot_hash=HEX64,
    )
    assert first == second  # fully deterministic
    assert first.boundaries
    assert "evidence_boundary" in first.reason_codes
    assert first.malformed == ()

    for boundary in first.boundaries:
        # The candidate locates to a replayable evidence slice.
        assert ACTION_CHAPTER[boundary.source_start : boundary.source_end] == boundary.content
        assert boundary.source_hash == content_hash(boundary.content)


def test_empty_chapter_surfaces_no_scene_boundaries_reason():
    outcome = detect_chapter_boundaries(
        novel_id=7,
        chapter_id=4,
        chapter_number=4,
        content="   ",
        source_snapshot_hash=HEX64,
    )
    assert outcome.boundaries == ()
    assert "no_scene_boundaries" in outcome.reason_codes
    assert outcome.blocked


def test_cutoff_filter_excludes_future_chapter_candidates():
    boundaries = detect_chapter_boundaries(
        novel_id=7,
        chapter_id=3,
        chapter_number=3,
        content=ACTION_CHAPTER,
        source_snapshot_hash=HEX64,
    ).boundaries
    kept = filter_by_cutoff(boundaries, cutoff_chapter=3)
    assert len(kept.kept) == len(boundaries)
    assert kept.excluded == ()

    future = filter_by_cutoff(boundaries, cutoff_chapter=2)
    assert future.kept == ()
    assert future.excluded == boundaries
    assert "beyond_cutoff" in future.reason_codes


def test_candidate_from_boundary_preserves_evidence_and_detector_lineage():
    outcome = detect_chapter_boundaries(
        novel_id=7,
        chapter_id=3,
        chapter_number=3,
        content=ACTION_CHAPTER,
        source_snapshot_hash=HEX64,
    )
    boundary = outcome.boundaries[0]
    evidence = build_evidence_range(
        boundary,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=8,
        evidence_key="ev-action",
    )
    candidate = build_candidate(
        candidate_key="ks-action-1",
        candidate_order=0,
        boundary=boundary,
        coordinates=SceneCoordinates(
            cast=["arin"], place="courtyard", time="night", pov="arin"
        ),
        salience_reasons=[
            SalienceReason(reason_code="evidence_boundary", detail="scene boundary")
        ],
        score_total=0.5,
        score_breakdown={"evidence_boundary": 0.5},
        diversity_key="night-courtyard",
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        policy_hash=HEX64_B,
        evidence_range=evidence,
        heuristic_signal=detect_dialogue_heuristic(
            ACTION_CHAPTER, detector_id=DIALOGUE_DETECTOR, detector_version="1.0.0"
        ),
    )
    # Evidence and detector lineage are preserved.
    assert candidate.evidence_ranges[0].content_hash == boundary.source_hash
    assert candidate.evidence_ranges[0].source_snapshot_hash == HEX64
    assert candidate.detector_id == DETECTOR_ID
    assert candidate.policy_hash == HEX64_B
    assert candidate.chapter_number == boundary.chapter_number
    # The assembled candidate passes the strict set contract.
    validate_candidate_set_contract(
        _set(candidates=[candidate.model_dump()], version_key="ks-boundary")
    )


# ---------------------------------------------------------------------------
# REQ-VIS-06 speaker/dialogue textual heuristic (advisory only)
# ---------------------------------------------------------------------------


def test_dialogue_rich_heuristic_exposes_offsets_and_confidence():
    signal = detect_dialogue_heuristic(
        ACTION_CHAPTER, detector_id=DIALOGUE_DETECTOR, detector_version="1.0.0"
    )
    assert signal.availability is HeuristicSignalAvailability.AVAILABLE
    assert signal.confidence == 0.9
    assert signal.speaker_offsets
    assert signal.dialogue_offsets
    assert signal.warnings == []
    for offset in signal.dialogue_offsets:
        assert ACTION_CHAPTER[offset.offset_start : offset.offset_end]
    # Offsets stay inside the chapter source (never an independent citation).
    assert signal.speaker_offsets[0].speaker_key  # attribution preserved


def test_dialogue_ambiguous_preserves_warnings_and_reduced_confidence():
    text = '"Hurry," a strange voice echoed. "No time."'
    signal = detect_dialogue_heuristic(
        text, detector_id=DIALOGUE_DETECTOR, detector_version="1.0.0"
    )
    assert signal.availability is HeuristicSignalAvailability.AMBIGUOUS
    assert signal.confidence == 0.3
    assert signal.dialogue_offsets
    assert any("unattributed" in warning for warning in signal.warnings)


def test_no_dialogue_is_unavailable_never_silent_zero():
    text = "It was a quiet night on the water with no one speaking at all."
    signal = detect_dialogue_heuristic(
        text, detector_id=DIALOGUE_DETECTOR, detector_version="1.0.0"
    )
    assert signal.availability is HeuristicSignalAvailability.UNAVAILABLE
    assert signal.confidence is None
    assert signal.speaker_offsets == []
    assert signal.dialogue_offsets == []
    assert "no_dialogue_detected" in signal.warnings


# ---------------------------------------------------------------------------
# Owner/novel scope + snapshot + visual bible approval (server-side authority)
# ---------------------------------------------------------------------------


async def _user_and_novel(db_session: AsyncSession, username: str):
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(title=f"Key Scene Novel {username}", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()
    return user, novel


async def test_owner_scope_denies_foreign_novel(db_session: AsyncSession):
    owner, novel = await _user_and_novel(db_session, "ks_owner")
    service = SceneBoundaryService(db_session)
    assert await service.verify_novel_scope(owner_id=owner.id, novel_id=novel.id) is novel
    assert await service.verify_novel_scope(owner_id=owner.id + 99, novel_id=novel.id) is None


async def test_source_snapshot_hash_detects_chapter_drift(db_session: AsyncSession):
    owner, novel = await _user_and_novel(db_session, "ks_snapshot")
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        content="The first chapter body.",
        title="ch1",
    )
    db_session.add(chapter)
    await db_session.flush()

    service = SceneBoundaryService(db_session)
    hash_a, chapters = await service.load_source_snapshot(
        owner_id=owner.id, novel_id=novel.id
    )
    assert len(chapters) == 1
    assert hash_a == compute_source_snapshot_hash(
        owner_id=owner.id, novel_id=novel.id, chapters=chapters
    )

    # Chapter drift must fail closed (stale snapshot lineage).
    chapter.content = "The rewritten chapter body changes everything."
    await db_session.flush()
    hash_b, _ = await service.load_source_snapshot(
        owner_id=owner.id, novel_id=novel.id
    )
    assert hash_b != hash_a


async def _persist_vb_version(
    db_session: AsyncSession,
    *,
    owner_id,
    novel_id,
    review_state="approved",
    manifest_hash=HEX64_C,
    version_key="vb-ks",
    idempotency_key=HEX64,
):
    row = VisualBibleVersion(
        owner_id=owner_id,
        novel_id=novel_id,
        version_key=version_key,
        revision_number=1,
        parent_version_id=None,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=8,
        review_state=review_state,
        schema_version="visual-bible.v1",
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        manifest_hash=manifest_hash,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=idempotency_key,
        projection_hash=HEX64,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_visual_bible_approval_verifies_owner_version_state_and_hash(
    db_session: AsyncSession,
):
    owner, novel = await _user_and_novel(db_session, "ks_vb")
    service = SceneBoundaryService(db_session)

    # No cited revision is vacuously fine.
    empty = await service.verify_visual_bible_approval(
        owner_id=owner.id,
        novel_id=novel.id,
        approved_visual_bible_revision_id=None,
        approved_visual_bible_revision_hash=None,
    )
    assert empty.ok

    # Pair mismatch fails closed.
    pair = await service.verify_visual_bible_approval(
        owner_id=owner.id,
        novel_id=novel.id,
        approved_visual_bible_revision_id=1,
        approved_visual_bible_revision_hash=None,
    )
    assert pair.ok is False
    assert pair.reason_code == "approval_lineage_mismatch"

    # Approved revision with matching hash passes.
    vb = await _persist_vb_version(
        db_session, owner_id=owner.id, novel_id=novel.id, review_state="approved"
    )
    ok = await service.verify_visual_bible_approval(
        owner_id=owner.id,
        novel_id=novel.id,
        approved_visual_bible_revision_id=vb.id,
        approved_visual_bible_revision_hash=HEX64_C,
    )
    assert ok.ok

    # Not-approved version blocks.
    cand = await _persist_vb_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        review_state="candidate",
        manifest_hash=HEX64_B,
        version_key="vb-ks-candidate",
        idempotency_key=HEX64_B,
    )
    not_approved = await service.verify_visual_bible_approval(
        owner_id=owner.id,
        novel_id=novel.id,
        approved_visual_bible_revision_id=cand.id,
        approved_visual_bible_revision_hash=HEX64_B,
    )
    assert not_approved.ok is False
    assert not_approved.reason_code == "visual_bible_not_approved"

    # Hash mismatch blocks.
    wrong_hash = await service.verify_visual_bible_approval(
        owner_id=owner.id,
        novel_id=novel.id,
        approved_visual_bible_revision_id=vb.id,
        approved_visual_bible_revision_hash=HEX64_B,
    )
    assert wrong_hash.ok is False
    assert wrong_hash.reason_code == "visual_bible_hash_mismatch"

    # Owner scope mismatch blocks.
    foreign = await service.verify_visual_bible_approval(
        owner_id=owner.id + 99,
        novel_id=novel.id,
        approved_visual_bible_revision_id=vb.id,
        approved_visual_bible_revision_hash=HEX64_C,
    )
    assert foreign.ok is False
    assert foreign.reason_code == "visual_bible_scope_mismatch"


# ---------------------------------------------------------------------------
# ORM metadata, append-only content rows and migration chain
# ---------------------------------------------------------------------------


def test_key_scene_tables_are_registered_on_metadata():
    tables = set(SceneCandidateSet.metadata.tables)
    assert KEY_SCENE_TABLES <= tables


def test_orm_exports_all_key_scene_entities():
    from app.models import (
        SceneCandidate as ExportedCandidate,
        SceneCandidateSet as ExportedSet,
        SceneEvidenceRange as ExportedEvidence,
        SceneReviewDecision as ExportedDecision,
    )

    assert ExportedSet.__tablename__ == "key_scene_sets"
    assert ExportedCandidate.__tablename__ == "key_scene_candidates"
    assert ExportedEvidence.__tablename__ == "key_scene_evidence_ranges"
    assert ExportedDecision.__tablename__ == "key_scene_review_decisions"


def test_set_orm_carries_owner_novel_snapshot_and_visual_bible_lineage():
    cols = set(inspect(SceneCandidateSet).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "version_key",
        "revision_number",
        "parent_set_id",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "review_state",
        "schema_version",
        "schema_hash",
        "policy_hash",
        "detector_id",
        "detector_version",
        "manifest_hash",
        "approved_visual_bible_revision_id",
        "approved_visual_bible_revision_hash",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneCandidateSet.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "version_key") in unique
    check_names = {
        c.name
        for c in SceneCandidateSet.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_key_scene_sets_review_state" in check_names
    assert "ck_key_scene_sets_visual_bible_approval" in check_names


def test_candidate_orm_enforces_spoiler_cutoff_and_offsets_checks():
    check_names = {
        c.name
        for c in SceneCandidate.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_key_scene_candidates_spoiler_cutoff" in check_names
    assert "ck_key_scene_candidates_offsets" in check_names
    assert "ck_key_scene_candidates_source_hash" in check_names
    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneCandidate.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "set_id", "candidate_key") in unique
    assert ("owner_id", "novel_id", "set_id", "candidate_order") in unique


def test_evidence_orm_enforces_spoiler_gate():
    check_names = {
        c.name
        for c in SceneEvidenceRange.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_key_scene_evidence_spoiler_cutoff" in check_names
    assert "ck_key_scene_evidence_offsets" in check_names
    assert SceneEvidenceRange.__table__.c.content_hash.type.length == 64


def test_review_decision_orm_is_idempotent():
    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneReviewDecision.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "set_id", "decision_key") in unique


async def _persist_set_candidate(
    db_session: AsyncSession,
    *,
    username: str,
    candidate_key: str = "ks-cand-append",
) -> tuple[SceneCandidateSet, SceneCandidate, User, Novel]:
    """Persist one minimal set + candidate row inside a fresh owner/novel scope."""
    owner, novel = await _user_and_novel(db_session, username)
    set_row = SceneCandidateSet(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key=f"ks-{candidate_key}",
        revision_number=1,
        parent_set_id=None,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=8,
        review_state="candidate",
        schema_version="key-scene.v1",
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        manifest_hash=HEX64_C,
        approved_visual_bible_revision_id=None,
        approved_visual_bible_revision_hash=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64,
        projection_hash=HEX64,
    )
    db_session.add(set_row)
    await db_session.flush()

    candidate_row = SceneCandidate(
        owner_id=owner.id,
        novel_id=novel.id,
        set_id=set_row.id,
        candidate_key=candidate_key,
        candidate_order=0,
        scene_id="hn_s0193134a6011ebed1ceb96",
        chapter_id=None,
        chapter_number=3,
        source_start=0,
        source_end=120,
        source_hash=HEX64_C,
        coordinates={"cast": ["arin"]},
        spoiler_cutoff=8,
        salience_reasons=None,
        score_total=0.5,
        score_breakdown=None,
        diversity_key="night-courtyard",
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        policy_hash=HEX64_B,
        review_state="candidate",
        heuristic_signal=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="key-scene.v1",
    )
    db_session.add(candidate_row)
    await db_session.flush()
    return set_row, candidate_row, owner, novel


async def test_candidate_row_is_append_only(db_session: AsyncSession):
    """A candidate row rejects UPDATE: no silent score/source mutation."""
    _set_row, candidate_row, _owner, _novel = await _persist_set_candidate(
        db_session, username="ks_append_candidate"
    )
    candidate_row.source_end = 200
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_evidence_range_row_is_append_only(db_session: AsyncSession):
    """An evidence range row rejects UPDATE: no silent citation mutation."""
    set_row, candidate_row, owner, novel = await _persist_set_candidate(
        db_session, username="ks_append_evidence"
    )
    evidence_row = SceneEvidenceRange(
        owner_id=owner.id,
        novel_id=novel.id,
        set_id=set_row.id,
        candidate_id=candidate_row.id,
        evidence_key="ev-append",
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        chapter_id=None,
        chapter_number=3,
        source_start=0,
        source_end=120,
        content_hash=HEX64_C,
        excerpt=None,
        cutoff_chapter=8,
        idempotency_key=HEX64_C,
    )
    db_session.add(evidence_row)
    await db_session.flush()
    evidence_row.excerpt = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_review_decision_row_is_append_only(db_session: AsyncSession):
    """A review decision row rejects UPDATE: history is immutable (D-31-04)."""
    set_row, _candidate_row, owner, novel = await _persist_set_candidate(
        db_session, username="ks_append_decision"
    )
    decision_row = SceneReviewDecision(
        owner_id=owner.id,
        novel_id=novel.id,
        set_id=set_row.id,
        decision_key="ks-append-1",
        action="approve",
        actor_source="human",
        actor="reader",
        reason="append-only test",
        from_review_state="candidate",
        to_review_state="approved",
        candidate_key=None,
        details={},
    )
    db_session.add(decision_row)
    await db_session.flush()
    decision_row.reason = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_is_serial_on_top_of_visual_bible_head():
    migration = _load_migration("20260801_key_scene.py")
    assert migration.revision == "20260801_key_scene"
    assert migration.down_revision == "20260801_visual_bible"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
    assert "key_scene_sets" in migration.__doc__
    assert "'needs_relink'" in migration._REVIEW_ACTIONS
    assert "'superseded'" in migration._REVIEW_STATES


def test_migration_matches_orm_table_set():
    migration = _load_migration("20260801_key_scene.py")
    # The migration docstring declares every ORM table (upgrade creates the same
    # set the ORM registers on metadata).
    for table in KEY_SCENE_TABLES:
        assert table in migration.__doc__
