"""Frozen-fixture identity/style consistency evaluation (Phase 33-03, REQ-VIS-04).

D-33-04: identity/style consistency is evidence, not canon. This module is the
frozen-fixture evaluator (the ``narrative_memory/qualification_*`` /
``rag_quality.py`` analog):

- ``ConsistencyEvaluator`` — a deterministic, fixture-driven evaluator that
  compares a candidate asset's declared identity/style/negative-constraint
  attributes against a frozen per-character fixture set and emits a versioned
  ``ConsistencyReportContract``. The three distinguishable outcomes are
  identity drift, style drift and unsupported detail; when no evaluator/fixture
  is configured the report is ``unavailable``. A score is a review signal with
  evaluator/model/fixture lineage — it never auto-approves and never rewrites
  the Visual Bible (D-33-04).
- ``ConsistencyReportService`` — durable persistence of versioned reports
  (append-only ``illustration_consistency_reports`` rows) with idempotent
  replay by ``report_key`` + ``idempotency_key``, plus owner-scoped read
  envelopes. Re-evaluating the same asset/key with different evidence fails
  closed instead of silently overwriting the audit trail.
- ``mock_consistency_fixture_registry`` — the deterministic
  ``illustration-consistency`` fixture set (same character across 3 scenes with
  deliberate identity drift, style drift and unsupported detail) used by tests
  and the API default seam.

Nothing here writes to the job/asset tables and nothing promotes a candidate to
canon; approval remains a human append-only review event (D-33-03).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from pydantic import ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.illustration import AssetRevision, ConsistencyReport
from app.schemas.illustration import (
    ConsistencyReportContract,
    ILLUSTRATION_CONSISTENCY_ARTIFACT_KIND,
    ILLUSTRATION_CONSISTENCY_SCHEMA_VERSION,
    IllustrationConsistencyVerdict,
    StrictIllustrationModel,
    canonical_illustration_hash,
)

CONSISTENCY_EVALUATOR_ID = "illustration-consistency.fixture.v1"
CONSISTENCY_EVALUATOR_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Frozen evaluation contracts (strict, immutable, replayable)
# ---------------------------------------------------------------------------


class ConsistencyPolicy(StrictIllustrationModel):
    """Frozen verdict thresholds; a report is a review signal, never canon.

    ``identity_*``/``style_*`` are overlap scores in [0, 1]; a candidate whose
    score drops below ``*_fail_below`` fails, below ``*_concern_below`` is a
    concern. ``negative_constraints_zero_tolerance`` hard-fails on any violated
    constraint; ``unsupported_detail_is_concern`` records added detail as a
    concern (human review) instead of silently passing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_concern_below: float = Field(default=0.9, ge=0, le=1)
    identity_fail_below: float = Field(default=0.6, ge=0, le=1)
    style_concern_below: float = Field(default=0.9, ge=0, le=1)
    style_fail_below: float = Field(default=0.6, ge=0, le=1)
    negative_constraints_zero_tolerance: bool = True
    unsupported_detail_is_concern: bool = True

    @model_validator(mode="after")
    def thresholds_are_monotone(self) -> "ConsistencyPolicy":
        if self.identity_fail_below > self.identity_concern_below:
            raise ValueError(
                "identity_fail_below must be <= identity_concern_below"
            )
        if self.style_fail_below > self.style_concern_below:
            raise ValueError("style_fail_below must be <= style_concern_below")
        return self


def _policy_dict(policy: ConsistencyPolicy) -> dict[str, Any]:
    return {
        "identity_concern_below": policy.identity_concern_below,
        "identity_fail_below": policy.identity_fail_below,
        "style_concern_below": policy.style_concern_below,
        "style_fail_below": policy.style_fail_below,
        "negative_constraints_zero_tolerance": policy.negative_constraints_zero_tolerance,
        "unsupported_detail_is_concern": policy.unsupported_detail_is_concern,
    }


class FrozenCharacterFixture(StrictIllustrationModel):
    """Frozen per-character consistency fixture (D-33-04 lineage).

    ``reference_asset_ids`` are the approved reference outputs of the same
    character across scenes; ``identity_attributes``/``style_attributes`` freeze
    the canonical descriptor vocabulary and ``negative_constraints`` the
    forbidden elements. The evaluator/model/fixture lineage on the report
    replays exactly from this fixture.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    character_key: str = Field(min_length=1, max_length=120)
    evaluator_id: str = Field(min_length=1, max_length=120)
    evaluator_version: str = Field(min_length=1, max_length=64)
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    reference_asset_ids: tuple[str, ...] = ()
    identity_attributes: tuple[str, ...] = ()
    style_attributes: tuple[str, ...] = ()
    negative_constraints: tuple[str, ...] = ()
    policy: ConsistencyPolicy = Field(default_factory=ConsistencyPolicy)


class CandidateConsistencyEvidence(StrictIllustrationModel):
    """What the candidate asset claims to depict, for one scene evaluation.

    ``identity_attributes``/``style_attributes`` are the candidate's declared
    descriptors and ``negative_constraints_present`` the forbidden elements it
    actually contains. These declared attributes freeze the idempotency key so
    the same evidence always replays the same report.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    character_key: str = Field(min_length=1, max_length=120)
    scene_key: str = Field(min_length=1, max_length=120)
    identity_attributes: tuple[str, ...] = ()
    style_attributes: tuple[str, ...] = ()
    negative_constraints_present: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Deterministic scoring (review signal, never canon)
# ---------------------------------------------------------------------------


def _overlap_score(candidate: set[str], fixture: set[str]) -> float:
    """Fraction of the frozen canonical vocabulary the candidate preserves."""
    if not fixture:
        return 1.0
    return len(candidate & fixture) / len(fixture)


def _fixture_set_hash(fixture: FrozenCharacterFixture) -> str:
    return canonical_illustration_hash(
        {
            "character_key": fixture.character_key,
            "evaluator_id": fixture.evaluator_id,
            "evaluator_version": fixture.evaluator_version,
            "reference_asset_ids": sorted(fixture.reference_asset_ids),
            "identity_attributes": sorted(fixture.identity_attributes),
            "style_attributes": sorted(fixture.style_attributes),
            "negative_constraints": sorted(fixture.negative_constraints),
            "policy": _policy_dict(fixture.policy),
        }
    )


def _report_idempotency_key(
    *,
    owner_id: int,
    novel_id: int,
    asset_revision_id: int,
    report_key: str,
    fixture: FrozenCharacterFixture,
    fixture_set_hash: str,
    evidence: CandidateConsistencyEvidence,
) -> str:
    """Deterministic report idempotency key (D-33-04 replayable evidence)."""
    return canonical_illustration_hash(
        {
            "artifact_kind": ILLUSTRATION_CONSISTENCY_ARTIFACT_KIND,
            "schema_version": ILLUSTRATION_CONSISTENCY_SCHEMA_VERSION,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "asset_revision_id": asset_revision_id,
            "report_key": report_key,
            "evaluator_id": fixture.evaluator_id,
            "evaluator_version": fixture.evaluator_version,
            "fixture_set_hash": fixture_set_hash,
            "character_key": evidence.character_key,
            "scene_key": evidence.scene_key,
            "identity_attributes": sorted(evidence.identity_attributes),
            "style_attributes": sorted(evidence.style_attributes),
            "negative_constraints_present": sorted(evidence.negative_constraints_present),
            "policy": _policy_dict(fixture.policy),
        }
    )


def _unavailable_idempotency_key(
    *,
    owner_id: int,
    novel_id: int,
    asset_revision_id: int,
    report_key: str,
    evidence: CandidateConsistencyEvidence,
) -> str:
    return canonical_illustration_hash(
        {
            "artifact_kind": ILLUSTRATION_CONSISTENCY_ARTIFACT_KIND,
            "schema_version": ILLUSTRATION_CONSISTENCY_SCHEMA_VERSION,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "asset_revision_id": asset_revision_id,
            "report_key": report_key,
            "evaluator_id": CONSISTENCY_EVALUATOR_ID,
            "evaluator_version": CONSISTENCY_EVALUATOR_VERSION,
            "status": "unavailable",
            "character_key": evidence.character_key,
            "scene_key": evidence.scene_key,
            "identity_attributes": sorted(evidence.identity_attributes),
            "style_attributes": sorted(evidence.style_attributes),
            "negative_constraints_present": sorted(evidence.negative_constraints_present),
        }
    )


# ---------------------------------------------------------------------------
# Evaluator (D-33-04): evidence, not canon
# ---------------------------------------------------------------------------


class ConsistencyEvaluator:
    """Deterministic frozen-fixture consistency evaluator.

    A score is a review signal: identity/style drift, unsupported detail and
    the ``unavailable`` state are all distinguishable, and no report can
    approve a candidate or rewrite the Visual Bible.
    """

    evaluator_id = CONSISTENCY_EVALUATOR_ID

    def __init__(
        self, fixtures: Mapping[str, FrozenCharacterFixture] | None = None
    ) -> None:
        self._fixtures = dict(fixtures or {})

    def configured_characters(self) -> tuple[str, ...]:
        return tuple(sorted(self._fixtures))

    def is_configured(self, character_key: str) -> bool:
        return character_key in self._fixtures

    def evaluate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        asset_revision_id: int,
        report_key: str,
        evidence: CandidateConsistencyEvidence,
    ) -> ConsistencyReportContract:
        fixture = self._fixtures.get(evidence.character_key)
        if fixture is None:
            return self._unavailable(
                owner_id=owner_id,
                novel_id=novel_id,
                asset_revision_id=asset_revision_id,
                report_key=report_key,
                evidence=evidence,
            )
        return self._evaluate_against_fixture(
            fixture=fixture,
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset_revision_id,
            report_key=report_key,
            evidence=evidence,
        )

    def _evaluate_against_fixture(
        self,
        *,
        fixture: FrozenCharacterFixture,
        owner_id: int,
        novel_id: int,
        asset_revision_id: int,
        report_key: str,
        evidence: CandidateConsistencyEvidence,
    ) -> ConsistencyReportContract:
        fixture_identity = set(fixture.identity_attributes)
        fixture_style = set(fixture.style_attributes)
        fixture_negative = set(fixture.negative_constraints)
        candidate_identity = set(evidence.identity_attributes)
        candidate_style = set(evidence.style_attributes)
        candidate_negative = set(evidence.negative_constraints_present)

        identity_score = _overlap_score(candidate_identity, fixture_identity)
        style_score = _overlap_score(candidate_style, fixture_style)
        violated = sorted(candidate_negative & fixture_negative)
        unsupported = sorted(
            (candidate_identity | candidate_style)
            - (fixture_identity | fixture_style)
        )

        policy = fixture.policy
        if violated and policy.negative_constraints_zero_tolerance:
            verdict = IllustrationConsistencyVerdict.FAIL
        elif (
            identity_score < policy.identity_fail_below
            or style_score < policy.style_fail_below
        ):
            verdict = IllustrationConsistencyVerdict.FAIL
        elif (
            identity_score < policy.identity_concern_below
            or style_score < policy.style_concern_below
        ):
            verdict = IllustrationConsistencyVerdict.CONCERN
        elif unsupported and policy.unsupported_detail_is_concern:
            verdict = IllustrationConsistencyVerdict.CONCERN
        else:
            verdict = IllustrationConsistencyVerdict.PASS

        fixture_set_hash = _fixture_set_hash(fixture)
        details: dict[str, Any] = {
            "status": "evaluated",
            "character_key": fixture.character_key,
            "scene_key": evidence.scene_key,
            "fixture": {
                "fixture_set_hash": fixture_set_hash,
                "reference_asset_ids": list(fixture.reference_asset_ids),
                "evaluator_id": fixture.evaluator_id,
                "evaluator_version": fixture.evaluator_version,
                "policy": _policy_dict(policy),
            },
            "drift": {
                "identity": {
                    "missing": sorted(fixture_identity - candidate_identity),
                    "added": sorted(candidate_identity - fixture_identity),
                },
                "style": {
                    "missing": sorted(fixture_style - candidate_style),
                    "added": sorted(candidate_style - fixture_style),
                },
                "negative_constraints": {"violated": violated},
                "unsupported_details": unsupported,
            },
        }
        scores = {
            "identity": round(identity_score, 4),
            "style": round(style_score, 4),
            "negative_constraint_violations": len(violated),
            "unsupported_detail_count": len(unsupported),
        }
        idempotency_key = _report_idempotency_key(
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset_revision_id,
            report_key=report_key,
            fixture=fixture,
            fixture_set_hash=fixture_set_hash,
            evidence=evidence,
        )
        return ConsistencyReportContract(
            schema_version=ILLUSTRATION_CONSISTENCY_SCHEMA_VERSION,
            artifact_kind=ILLUSTRATION_CONSISTENCY_ARTIFACT_KIND,
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset_revision_id,
            report_key=report_key,
            evaluator_id=fixture.evaluator_id,
            evaluator_version=fixture.evaluator_version,
            model_lineage=dict(fixture.model_lineage),
            fixture_set_hash=fixture_set_hash,
            reference_asset_ids=list(fixture.reference_asset_ids),
            scores=scores,
            verdict=verdict,
            details=details,
            idempotency_key=idempotency_key,
        )

    def _unavailable(
        self,
        *,
        owner_id: int,
        novel_id: int,
        asset_revision_id: int,
        report_key: str,
        evidence: CandidateConsistencyEvidence,
    ) -> ConsistencyReportContract:
        """No evaluator/fixture configured: explicit unavailable, fail closed.

        The report is still versioned evidence (the reviewer can see why no
        score exists) but it can never approve anything.
        """
        fixture_set_hash = canonical_illustration_hash(
            {
                "kind": "illustration-consistency.unavailable",
                "character_key": evidence.character_key,
            }
        )
        details: dict[str, Any] = {
            "status": "unavailable",
            "reason_code": "fixture_missing",
            "message": (
                f"no frozen consistency fixture for character "
                f"{evidence.character_key!r}; the report cannot approve anything"
            ),
            "character_key": evidence.character_key,
            "scene_key": evidence.scene_key,
            "evaluator_id": CONSISTENCY_EVALUATOR_ID,
            "evaluator_version": CONSISTENCY_EVALUATOR_VERSION,
        }
        idempotency_key = _unavailable_idempotency_key(
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset_revision_id,
            report_key=report_key,
            evidence=evidence,
        )
        return ConsistencyReportContract(
            schema_version=ILLUSTRATION_CONSISTENCY_SCHEMA_VERSION,
            artifact_kind=ILLUSTRATION_CONSISTENCY_ARTIFACT_KIND,
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset_revision_id,
            report_key=report_key,
            evaluator_id=CONSISTENCY_EVALUATOR_ID,
            evaluator_version=CONSISTENCY_EVALUATOR_VERSION,
            model_lineage={},
            fixture_set_hash=fixture_set_hash,
            reference_asset_ids=[],
            scores={},
            verdict=IllustrationConsistencyVerdict.UNAVAILABLE,
            details=details,
            idempotency_key=idempotency_key,
        )


# ---------------------------------------------------------------------------
# Read envelope (owner-scoped, evidence-only)
# ---------------------------------------------------------------------------


class ConsistencyReportView(StrictIllustrationModel):
    """Read envelope: versioned consistency evidence with full lineage."""

    id: int
    owner_id: int
    novel_id: int
    asset_revision_id: int
    report_key: str
    evaluator_id: str
    evaluator_version: str
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    fixture_set_hash: str
    reference_asset_ids: list[str] = Field(default_factory=list)
    scores: dict[str, Any] = Field(default_factory=dict)
    verdict: IllustrationConsistencyVerdict
    details: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    schema_version: str
    created_at: datetime | None = None


def report_view(row: ConsistencyReport) -> ConsistencyReportView:
    return ConsistencyReportView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        asset_revision_id=row.asset_revision_id,
        report_key=row.report_key,
        evaluator_id=row.evaluator_id,
        evaluator_version=row.evaluator_version,
        model_lineage=dict(row.model_lineage or {}),
        fixture_set_hash=row.fixture_set_hash,
        reference_asset_ids=list(row.reference_asset_ids or []),
        scores=dict(row.scores or {}),
        verdict=row.verdict,
        details=dict(row.details or {}),
        idempotency_key=row.idempotency_key,
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Durable report service (append-only, idempotent, owner-scoped)
# ---------------------------------------------------------------------------


class ConsistencyReportNotFound(ValueError):
    """An asset/report outside the explicit owner/novel scope (404-equivalent)."""


class ConsistencyReportConflict(ValueError):
    """A report_key was already used by a different evaluation (fail closed)."""


class ConsistencyReportServiceError(RuntimeError):
    """Unexpected durable-layer failure while persisting a report."""


def _with_asset_lineage(
    contract: ConsistencyReportContract, asset: AssetRevision
) -> ConsistencyReportContract:
    """Attach the frozen source/prompt/model lineage the report evaluates.

    The report must replay against the exact candidate context (source
    snapshot, prompt revision, cutoff) so a reviewer can re-verify evidence.
    The idempotency key is unaffected: it is pinned to the asset revision id.
    """
    details = dict(contract.details)
    details["asset"] = {
        "asset_revision_id": asset.id,
        "scene_spec_hash": asset.scene_spec_hash,
        "prompt_revision_id": asset.prompt_revision_id,
        "prompt_revision_hash": asset.prompt_revision_hash,
        "visual_bible_revision_hash": asset.visual_bible_revision_hash,
        "source_snapshot_id": asset.source_snapshot_id,
        "source_snapshot_hash": asset.source_snapshot_hash,
        "cutoff_chapter": asset.cutoff_chapter,
        "config_hash": asset.config_hash,
        "model_lineage": dict(asset.model_lineage or {}),
        "provider": asset.provider,
        "provider_model": asset.provider_model,
    }
    return contract.model_copy(update={"details": details})


def _row_from_contract(contract: ConsistencyReportContract) -> ConsistencyReport:
    canonical = contract.model_dump(mode="json")
    return ConsistencyReport(
        owner_id=contract.owner_id,
        novel_id=contract.novel_id,
        asset_revision_id=contract.asset_revision_id,
        report_key=contract.report_key,
        evaluator_id=contract.evaluator_id,
        evaluator_version=contract.evaluator_version,
        model_lineage=dict(contract.model_lineage),
        fixture_set_hash=contract.fixture_set_hash,
        reference_asset_ids=list(contract.reference_asset_ids),
        scores=dict(contract.scores),
        verdict=contract.verdict.value,
        details=dict(contract.details),
        canonical_payload=canonical,
        canonical_payload_hash=canonical_illustration_hash(canonical),
        idempotency_key=contract.idempotency_key,
        projection_hash=contract.fixture_set_hash,
        schema_version=contract.schema_version,
    )


class ConsistencyReportService:
    """Owner-scoped durability for versioned consistency evidence."""

    def __init__(self, session: AsyncSession, *, evaluator: ConsistencyEvaluator) -> None:
        self._session = session
        self._evaluator = evaluator

    async def evaluate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        asset_revision_id: int,
        report_key: str,
        evidence: CandidateConsistencyEvidence,
    ) -> tuple[ConsistencyReport, bool]:
        """Run the evaluator and persist one versioned report (idempotent).

        A duplicate ``report_key`` replays the existing row when the evidence is
        identical; a different evaluation under the same key fails closed.
        """
        asset = await self._session.scalar(
            select(AssetRevision).where(
                AssetRevision.owner_id == owner_id,
                AssetRevision.novel_id == novel_id,
                AssetRevision.id == asset_revision_id,
            )
        )
        if asset is None:
            raise ConsistencyReportNotFound(
                "illustration asset not found in the owner/novel scope"
            )
        contract = self._evaluator.evaluate(
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset.id,
            report_key=report_key,
            evidence=evidence,
        )
        contract = _with_asset_lineage(contract, asset)

        existing = await self._report_by_key(owner_id, novel_id, asset.id, report_key)
        if existing is not None:
            if existing.idempotency_key != contract.idempotency_key:
                raise ConsistencyReportConflict(
                    "report_key is already used by a different evaluation"
                )
            return existing, True

        row = _row_from_contract(contract)
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._report_by_key(owner_id, novel_id, asset.id, report_key)
            if existing is None:
                raise ConsistencyReportServiceError(
                    "report create race; existing row not found after rollback"
                ) from None
            if existing.idempotency_key != contract.idempotency_key:
                raise ConsistencyReportConflict(
                    "report_key is already used by a different evaluation"
                )
            return existing, True
        return row, False

    async def get_latest(
        self, *, owner_id: int, novel_id: int, asset_revision_id: int
    ) -> ConsistencyReport | None:
        return await self._session.scalar(
            select(ConsistencyReport)
            .where(
                ConsistencyReport.owner_id == owner_id,
                ConsistencyReport.novel_id == novel_id,
                ConsistencyReport.asset_revision_id == asset_revision_id,
            )
            .order_by(ConsistencyReport.id.desc())
            .limit(1)
        )

    async def list_reports(
        self, *, owner_id: int, novel_id: int
    ) -> list[ConsistencyReport]:
        rows = (
            await self._session.scalars(
                select(ConsistencyReport)
                .where(
                    ConsistencyReport.owner_id == owner_id,
                    ConsistencyReport.novel_id == novel_id,
                )
                .order_by(ConsistencyReport.id.desc())
            )
        ).all()
        return list(rows)

    async def _report_by_key(
        self, owner_id: int, novel_id: int, asset_revision_id: int, report_key: str
    ) -> ConsistencyReport | None:
        return await self._session.scalar(
            select(ConsistencyReport).where(
                ConsistencyReport.owner_id == owner_id,
                ConsistencyReport.novel_id == novel_id,
                ConsistencyReport.asset_revision_id == asset_revision_id,
                ConsistencyReport.report_key == report_key,
            )
        )


# ---------------------------------------------------------------------------
# Deterministic mock fixture set (illustration-consistency fixture)
# ---------------------------------------------------------------------------


def mock_consistency_fixture_registry() -> dict[str, FrozenCharacterFixture]:
    """Deterministic per-character fixture registry for tests and the API seam.

    33-VALIDATION ``illustration-consistency``: the same character (Arin) is
    evaluated across scenes with deliberate identity drift, style drift and
    unsupported detail; the evaluator must keep those three distinguishable
    from the ``unavailable`` state.
    """
    return {
        "arin": FrozenCharacterFixture(
            character_key="arin",
            evaluator_id=CONSISTENCY_EVALUATOR_ID,
            evaluator_version=CONSISTENCY_EVALUATOR_VERSION,
            model_lineage={
                "kind": "fixture",
                "evaluator": CONSISTENCY_EVALUATOR_ID,
                "version": CONSISTENCY_EVALUATOR_VERSION,
            },
            reference_asset_ids=("ref-char-arin-1", "ref-char-arin-2"),
            identity_attributes=(
                "black_hair",
                "amber_eyes",
                "lean_build",
                "scar_left_brow",
            ),
            style_attributes=(
                "ink_painting",
                "warm_palette",
                "soft_lighting",
            ),
            negative_constraints=(
                "no_glasses",
                "no_text",
                "no_modern_clothing",
            ),
            policy=ConsistencyPolicy(),
        ),
    }


__all__ = [
    "CONSISTENCY_EVALUATOR_ID",
    "CONSISTENCY_EVALUATOR_VERSION",
    "CandidateConsistencyEvidence",
    "ConsistencyEvaluator",
    "ConsistencyPolicy",
    "ConsistencyReportConflict",
    "ConsistencyReportNotFound",
    "ConsistencyReportService",
    "ConsistencyReportServiceError",
    "ConsistencyReportView",
    "FrozenCharacterFixture",
    "mock_consistency_fixture_registry",
    "report_view",
]
