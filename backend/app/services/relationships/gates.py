"""Deterministic relationship observation gates and threshold policy.

LLM outputs never choose status. Scripts own ordered gates, thresholds, reason
codes, and the candidate -> judged -> gated -> terminal state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.relationship import (
    RELATIONSHIP_EDGE_TYPES,
    RELATIONSHIP_TRANSITIONS,
)
from app.schemas.relationship import RelationshipSemanticJudgment
from app.services.relationships.evidence import (
    RelationshipEvidencePackage,
    evidence_checksum_for,
    sha256_json,
)

# Locked thresholds (D-15). Policy hash changes if these change.
AUTO_ACCEPT_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.65
POLICY_VERSION = "relationship-gate-policy.v1"

ACCEPTABLE_TRANSITIONS = frozenset({"establish", "change", "end"})
REVIEW_ONLY_TRANSITIONS = frozenset({"uncertain"})


@dataclass(slots=True)
class GateDecision:
    """Result of the ordered gate chain for one judgment."""

    status: str
    gate_status: str
    gate_failures: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    pipeline_status: str = "gated"

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"

    @property
    def needs_review(self) -> bool:
        return self.status == "needs_human_review"


def policy_hash() -> str:
    return sha256_json(
        {
            "policy_version": POLICY_VERSION,
            "auto_accept_threshold": AUTO_ACCEPT_THRESHOLD,
            "review_threshold": REVIEW_THRESHOLD,
            "edge_types": list(RELATIONSHIP_EDGE_TYPES),
            "transitions": list(RELATIONSHIP_TRANSITIONS),
            "gate_order": [
                "source_acceptance",
                "fiction",
                "scope",
                "schema",
                "evidence",
                "interval",
                "conflict",
                "threshold",
            ],
        }
    )


class RelationshipGateService:
    """Apply ordered source/fiction/scope/schema/evidence/interval/conflict/threshold gates."""

    def evaluate(
        self,
        *,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment | dict[str, Any],
        source_still_accepted: bool,
        fiction_domain: bool = True,
        existing_idempotency_keys: set[str] | None = None,
        proposed_idempotency_key: str | None = None,
    ) -> GateDecision:
        """Pure evaluation — no ORM mutation, no network."""

        failures: list[str] = []
        reason_codes: list[str] = []

        # 1. source_acceptance_gate
        if not source_still_accepted:
            failures.append("source_acceptance_gate:revoked")
            reason_codes.append("source_revoked")
            return self._reject(failures, reason_codes, gate_status="rejected")

        # 2. fiction_gate
        if not fiction_domain:
            failures.append("fiction_gate:non_fiction")
            reason_codes.append("non_fiction")
            return self._reject(failures, reason_codes, gate_status="rejected")

        parsed = self._coerce_judgment(judgment)
        if parsed is None:
            failures.append("schema_gate:unparseable")
            reason_codes.append("schema_failed")
            return self._reject(failures, reason_codes, gate_status="schema_failed")

        # 3. scope_gate
        scope_failures = self._scope_failures(package, parsed)
        if scope_failures:
            failures.extend(scope_failures)
            reason_codes.append("scope_failed")
            return self._reject(failures, reason_codes, gate_status="rejected")

        # 4. schema_gate
        schema_failures = self._schema_failures(package, parsed)
        if schema_failures:
            failures.extend(schema_failures)
            reason_codes.append("schema_failed")
            return self._reject(failures, reason_codes, gate_status="schema_failed")

        # 5. evidence_gate
        evidence_failures = self._evidence_failures(package, parsed)
        if evidence_failures:
            failures.extend(evidence_failures)
            reason_codes.append("evidence_failed")
            return self._reject(failures, reason_codes, gate_status="evidence_failed")

        # 6. interval_gate
        interval_failures = self._interval_failures(package, parsed)
        if interval_failures:
            failures.extend(interval_failures)
            reason_codes.append("interval_failed")
            return self._reject(failures, reason_codes, gate_status="rejected")

        # 7. conflict_gate
        conflict_failures = self._conflict_failures(
            package,
            parsed,
            existing_idempotency_keys=existing_idempotency_keys or set(),
            proposed_idempotency_key=proposed_idempotency_key,
        )
        if conflict_failures:
            # Hard conflicts reject; soft conflicts review.
            hard = [f for f in conflict_failures if f.startswith("hard:")]
            if hard:
                failures.extend(conflict_failures)
                reason_codes.append("conflict_failed")
                return self._reject(
                    failures, reason_codes, gate_status="conflict_failed"
                )
            failures.extend(conflict_failures)
            reason_codes.append("conflict_review")
            return GateDecision(
                status="needs_human_review",
                gate_status="needs_human_review",
                gate_failures=failures,
                reason_codes=reason_codes,
                pipeline_status="needs_human_review",
            )

        # 8. threshold_gate (and transition/uncertain rules)
        return self._threshold_decision(parsed, failures, reason_codes)

    def _threshold_decision(
        self,
        judgment: RelationshipSemanticJudgment,
        failures: list[str],
        reason_codes: list[str],
    ) -> GateDecision:
        confidence = float(judgment.confidence)
        transition = judgment.transition.value if hasattr(judgment.transition, "value") else str(judgment.transition)

        if transition in REVIEW_ONLY_TRANSITIONS:
            failures.append("threshold_gate:uncertain_transition")
            reason_codes.append("uncertain_transition")
            return GateDecision(
                status="needs_human_review",
                gate_status="needs_human_review",
                gate_failures=failures,
                reason_codes=reason_codes,
                pipeline_status="needs_human_review",
            )

        if confidence < REVIEW_THRESHOLD:
            failures.append(f"threshold_gate:reject_confidence:{confidence}")
            reason_codes.append("below_review_threshold")
            return GateDecision(
                status="rejected",
                gate_status="threshold_failed",
                gate_failures=failures,
                reason_codes=reason_codes,
                pipeline_status="rejected",
            )

        if confidence < AUTO_ACCEPT_THRESHOLD:
            failures.append(f"threshold_gate:review_confidence:{confidence}")
            reason_codes.append("review_band")
            return GateDecision(
                status="needs_human_review",
                gate_status="needs_human_review",
                gate_failures=failures,
                reason_codes=reason_codes,
                pipeline_status="needs_human_review",
            )

        # confidence >= AUTO_ACCEPT_THRESHOLD
        if judgment.risk_flags:
            failures.append(f"threshold_gate:risk_flags:{','.join(judgment.risk_flags)}")
            reason_codes.append("risk_flags_review")
            return GateDecision(
                status="needs_human_review",
                gate_status="needs_human_review",
                gate_failures=failures,
                reason_codes=reason_codes,
                pipeline_status="needs_human_review",
            )

        return GateDecision(
            status="accepted",
            gate_status="accepted",
            gate_failures=[],
            reason_codes=["auto_accept"],
            pipeline_status="accepted",
        )

    def _coerce_judgment(
        self, judgment: RelationshipSemanticJudgment | dict[str, Any]
    ) -> RelationshipSemanticJudgment | None:
        if isinstance(judgment, RelationshipSemanticJudgment):
            return judgment
        try:
            return RelationshipSemanticJudgment.model_validate(judgment)
        except Exception:
            return None

    def _scope_failures(
        self,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment,
    ) -> list[str]:
        failures: list[str] = []
        if judgment.candidate_key != package.candidate_key:
            failures.append("scope_gate:candidate_key_mismatch")
        if judgment.source_ref != package.source_ref:
            failures.append("scope_gate:source_ref_mismatch")
        if judgment.target_ref != package.target_ref:
            failures.append("scope_gate:target_ref_mismatch")
        return failures

    def _schema_failures(
        self,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment,
    ) -> list[str]:
        failures: list[str] = []
        rel = (
            judgment.relation_type.value
            if hasattr(judgment.relation_type, "value")
            else str(judgment.relation_type)
        )
        if rel not in package.allowed_relation_types:
            failures.append(f"schema_gate:relation_type_not_allowed:{rel}")
        transition = (
            judgment.transition.value
            if hasattr(judgment.transition, "value")
            else str(judgment.transition)
        )
        if transition not in package.allowed_transitions:
            failures.append(f"schema_gate:transition_not_allowed:{transition}")
        if judgment.confidence < 0 or judgment.confidence > 1:
            failures.append("schema_gate:confidence_out_of_range")
        if not judgment.supporting_evidence_ids:
            failures.append("schema_gate:empty_supporting_evidence")
        if len(judgment.supporting_evidence_ids) > 8:
            failures.append("schema_gate:too_many_supporting_evidence")
        return failures

    def _evidence_failures(
        self,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment,
    ) -> list[str]:
        allowed = set(package.allowed_evidence_ids())
        cited = list(judgment.supporting_evidence_ids)
        cited.append(judgment.valid_from_evidence_id)
        if judgment.valid_to_evidence_id:
            cited.append(judgment.valid_to_evidence_id)
        out_of_package = sorted({eid for eid in cited if eid not in allowed})
        if out_of_package:
            return [f"evidence_gate:out_of_package:{','.join(out_of_package)}"]
        return []

    def _interval_failures(
        self,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment,
    ) -> list[str]:
        units = package.unit_by_id()
        from_unit = units.get(judgment.valid_from_evidence_id)
        if from_unit is None:
            return ["interval_gate:valid_from_missing"]

        if judgment.valid_to_evidence_id:
            to_unit = units.get(judgment.valid_to_evidence_id)
            if to_unit is None:
                return ["interval_gate:valid_to_missing"]
            if (to_unit.chapter_number, to_unit.narrative_index) < (
                from_unit.chapter_number,
                from_unit.narrative_index,
            ):
                return ["interval_gate:valid_to_precedes_from"]
            if (
                to_unit.chapter_number == from_unit.chapter_number
                and to_unit.narrative_index == from_unit.narrative_index
                and to_unit.source_end < from_unit.source_start
            ):
                return ["interval_gate:offset_order"]
        return []

    def _conflict_failures(
        self,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment,
        *,
        existing_idempotency_keys: set[str],
        proposed_idempotency_key: str | None,
    ) -> list[str]:
        failures: list[str] = []
        if package.source_character_id == package.target_character_id:
            failures.append("hard:self_edge")
        if proposed_idempotency_key and proposed_idempotency_key in existing_idempotency_keys:
            # Duplicate key is handled as idempotent reuse by the worker, not a hard fail.
            # Surface as soft conflict metadata only when caller treats it as conflict.
            failures.append("soft:duplicate_idempotency_key")
        return failures

    def _reject(
        self,
        failures: list[str],
        reason_codes: list[str],
        *,
        gate_status: str,
    ) -> GateDecision:
        return GateDecision(
            status="rejected",
            gate_status=gate_status,
            gate_failures=list(failures),
            reason_codes=list(reason_codes),
            pipeline_status="rejected",
        )

    def build_idempotency_key(
        self,
        *,
        analysis_version_id: int,
        source_judgment_id: int,
        source_character_id: int,
        target_character_id: int,
        relation_type: str,
        valid_from_chapter: int,
        valid_from_narrative_index: int,
        valid_to_chapter: int | None,
        valid_to_narrative_index: int | None,
        evidence_checksum: str,
        policy_hash_value: str,
    ) -> str:
        payload = {
            "analysis_version_id": analysis_version_id,
            "source_judgment_id": source_judgment_id,
            "source_character_id": source_character_id,
            "target_character_id": target_character_id,
            "relation_type": relation_type,
            "valid_from": [valid_from_chapter, valid_from_narrative_index],
            "valid_to": [valid_to_chapter, valid_to_narrative_index],
            "evidence_checksum": evidence_checksum,
            "policy_hash": policy_hash_value,
        }
        return sha256_json(payload)

    def interval_from_package(
        self,
        package: RelationshipEvidencePackage,
        judgment: RelationshipSemanticJudgment,
    ) -> dict[str, Any]:
        units = package.unit_by_id()
        from_unit = units[judgment.valid_from_evidence_id]
        to_unit = (
            units[judgment.valid_to_evidence_id]
            if judgment.valid_to_evidence_id
            else None
        )
        return {
            "valid_from_chapter": from_unit.chapter_number,
            "valid_from_narrative_index": from_unit.narrative_index,
            "valid_to_chapter": to_unit.chapter_number if to_unit else None,
            "valid_to_narrative_index": to_unit.narrative_index if to_unit else None,
            "valid_from_evidence_id": judgment.valid_from_evidence_id,
            "valid_to_evidence_id": judgment.valid_to_evidence_id,
            "evidence_checksum": evidence_checksum_for(
                [units[eid] for eid in judgment.supporting_evidence_ids if eid in units]
            ),
        }


relationship_gate_service = RelationshipGateService()
