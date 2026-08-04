"""Deterministic cross-chapter identity/style consistency scoring (Phase 38-03).

D-38-03: cross-chapter identity/style consistency is **computable but never
auto-publishes**. This module is the deterministic, DB-free scorer (the Phase
33-03 frozen-fixture evaluator analog, but comparing chapters of the same
derivative identity instead of a fixed fixture set):

- ``score_cross_chapter_consistency`` — a pure function over the frozen per-
  chapter evidence tuple. It emits a ``DerivativeConsistencyReport`` whose
  per-chapter ``identity_score``/``style_score``, stable ``reasons`` and
  ``verdict`` are fully deterministic (same evidence -> same report).
- Missing input is explicit: fewer than two chapters or absent identity/style
  evidence yields ``unavailable`` with a stable reason code, so a score can
  never silently pass.
- The verdict only drives the candidate ``review_state`` chain (fail ->
  blocked, concern/unavailable -> needs_review, pass -> candidate); it can
  never approve anything and never touches the Original Visual Bible rows.

Reason codes: ``insufficient_chapters``, ``missing_identity_evidence``,
``missing_style_evidence``, ``identity_drift``, ``style_divergence_undeclared``,
``style_divergence_declared``.
"""

from __future__ import annotations

from app.schemas.derivative_visual_asset import (
    DERIVATIVE_CONSISTENCY_EVALUATOR_ID,
    DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION,
    ChapterConsistencyEvidence,
    ChapterScoreView,
    DerivativeConsistencyReport,
    DerivativeConsistencyVerdict,
)


def _unavailable(reason: str, detail: str, chapters: tuple[ChapterConsistencyEvidence, ...]) -> DerivativeConsistencyReport:
    return DerivativeConsistencyReport(
        schema_version="derivative-visual-asset.v1",
        evaluator_id=DERIVATIVE_CONSISTENCY_EVALUATOR_ID,
        evaluator_version=DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION,
        chapters=[],
        reasons=[reason],
        verdict=DerivativeConsistencyVerdict.UNAVAILABLE,
        details={"status": "unavailable", "reason_code": reason, "message": detail},
    )


def score_cross_chapter_consistency(
    chapters: tuple[ChapterConsistencyEvidence, ...],
) -> DerivativeConsistencyReport:
    """Deterministic cross-chapter identity/style consistency review signal.

    Fail-closed rules:
    - fewer than two chapters -> ``unavailable`` (``insufficient_chapters``);
    - any chapter with missing identity/style evidence -> ``unavailable`` with
      the explicit missing-evidence reason code;
    - identity drift (different Original entity hash) -> ``fail``;
    - a style change that the fork did not declare -> ``fail``
      (``style_divergence_undeclared``); a declared style change -> ``concern``
      (``style_divergence_declared``) and still requires human review;
    - otherwise ``pass``.
    """
    if len(chapters) < 2:
        return _unavailable(
            "insufficient_chapters",
            "cross-chapter consistency requires at least two chapter inputs",
            chapters,
        )
    if any(chapter.missing_identity_evidence for chapter in chapters):
        return _unavailable(
            "missing_identity_evidence",
            "a chapter carries no pinned Original identity evidence; "
            "the score cannot be computed",
            chapters,
        )
    if any(chapter.missing_style_evidence for chapter in chapters):
        return _unavailable(
            "missing_style_evidence",
            "a chapter carries no frozen style profile; the score cannot "
            "be computed",
            chapters,
        )

    ordered = sorted(chapters, key=lambda chapter: chapter.chapter_number)
    reference = ordered[0]
    chapter_scores: list[ChapterScoreView] = []
    reasons: list[str] = []
    fail = False
    concern = False

    for chapter in ordered:
        identity_consistent = (
            chapter.identity_source_hash == reference.identity_source_hash
        )
        style_consistent = chapter.style_hash == reference.style_hash
        if not identity_consistent:
            fail = True
            reasons.append(
                f"identity_drift:chapter{chapter.chapter_number}"
            )
        if not style_consistent:
            if chapter.declared_style_divergence:
                concern = True
                reasons.append(
                    f"style_divergence_declared:chapter{chapter.chapter_number}"
                )
            else:
                fail = True
                reasons.append(
                    f"style_divergence_undeclared:chapter{chapter.chapter_number}"
                )
        chapter_scores.append(
            ChapterScoreView(
                chapter_number=chapter.chapter_number,
                identity_score=1.0 if identity_consistent else 0.0,
                style_score=1.0 if style_consistent else 0.0,
                identity_consistent=identity_consistent,
                style_consistent=style_consistent,
            )
        )

    if fail:
        verdict = DerivativeConsistencyVerdict.FAIL
    elif concern:
        verdict = DerivativeConsistencyVerdict.CONCERN
    else:
        verdict = DerivativeConsistencyVerdict.PASS

    return DerivativeConsistencyReport(
        schema_version="derivative-visual-asset.v1",
        evaluator_id=DERIVATIVE_CONSISTENCY_EVALUATOR_ID,
        evaluator_version=DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION,
        chapters=chapter_scores,
        reasons=reasons,
        verdict=verdict,
        details={
            "status": "evaluated",
            "identity_key": reference.identity_key,
            "reference_chapter": reference.chapter_number,
            "evaluator": {
                "evaluator_id": DERIVATIVE_CONSISTENCY_EVALUATOR_ID,
                "evaluator_version": DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION,
            },
        },
    )


__all__ = [
    "DERIVATIVE_CONSISTENCY_EVALUATOR_ID",
    "DERIVATIVE_CONSISTENCY_EVALUATOR_VERSION",
    "score_cross_chapter_consistency",
]
