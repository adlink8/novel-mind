"""Deterministic clue lifecycle gates (no persistence side effects).

Gate order (locked):
1. exact scope (owner/novel/build)
2. schema / enum
3. allowed evidence membership
4. offset / content hash
5. cue/later temporal order
6. state transition legality + role evidence
7. threshold / conflict policy
8. human-protection

Similarity, repeated motifs, shared people/location and vector scores alone
never pass. LLM confidence is one input and cannot bypass a hard gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.clue import (
    ClueConflictFlag,
    ClueLifecycleState,
    ClueSemanticClassification,
    ClueSemanticJudgment,
    is_legal_transition,
    validate_evidence_for_transition,
    LifecycleTransitionError,
    ClueEvidenceRef,
    ClueEvidenceRole,
)
from app.services.clues.evidence import (
    ClueEvidencePackage,
    ClueEvidenceUnit,
    sha256_json,
    validate_package_scope,
)

POLICY_VERSION = "clue-gate-policy.v1"
AUTO_ACCEPT_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.65

# Classifications that can support a given target state (necessary, not sufficient).
CLASSIFICATION_FOR_TARGET: dict[ClueLifecycleState, frozenset[ClueSemanticClassification]] = {
    # ACTIVE is the entry state for any cue-bearing clue. A reinforcement or
    # payoff classification still carries a cue (enforced by ClueSemanticJudgment),
    # so it legitimately enters the active lifecycle and later progresses to
    # REINFORCED / PAID_OFF. Only unrelated/ambiguous may not enter active.
    ClueLifecycleState.ACTIVE: frozenset(
        {
            ClueSemanticClassification.CUE_ONLY,
            ClueSemanticClassification.REINFORCEMENT,
            ClueSemanticClassification.PAYOFF,
        }
    ),
    ClueLifecycleState.REINFORCED: frozenset({ClueSemanticClassification.REINFORCEMENT}),
    ClueLifecycleState.PAID_OFF: frozenset({ClueSemanticClassification.PAYOFF}),
    ClueLifecycleState.DISMISSED: frozenset(
        {
            ClueSemanticClassification.UNRELATED,
            ClueSemanticClassification.AMBIGUOUS,
        }
    ),
}


@dataclass(slots=True)
class GateDecision:
    """Pure gate outcome for one proposed transition — no ORM mutation."""

    accepted: bool
    status: str
    gate_status: str
    from_status: str
    to_status: str
    gate_failures: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    proposed_evidence: list[dict[str, Any]] = field(default_factory=list)

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
            "gate_order": [
                "scope",
                "schema",
                "evidence",
                "offset_hash",
                "temporal_order",
                "transition",
                "threshold_conflict",
                "human_protection",
            ],
        }
    )


class ClueGateService:
    """Ordered local gates; returns GateDecision for lifecycle persistence later."""

    def evaluate_transition(
        self,
        *,
        package: ClueEvidencePackage,
        judgment: ClueSemanticJudgment | dict[str, Any] | None,
        from_status: ClueLifecycleState | str,
        to_status: ClueLifecycleState | str,
        owner_id: int,
        novel_id: int,
        hierarchy_build_id: str | None = None,
        consumed_evidence_ids: frozenset[str] | set[str] | None = None,
        human_protected_dismissed: bool = False,
        human_protected_confirm: bool = False,
        allow_machine_dismiss: bool = True,
        relation_ref_validation: str | None = None,
        # "valid" | "unresolved" | "source_unavailable" | "invalid" | None
    ) -> GateDecision:
        """Evaluate a proposed lifecycle transition without side effects."""

        src = ClueLifecycleState(from_status)
        dst = ClueLifecycleState(to_status)
        failures: list[str] = []
        reasons: list[str] = []

        # 1. scope
        scope_failures = validate_package_scope(
            package,
            owner_id=owner_id,
            novel_id=novel_id,
            hierarchy_build_id=hierarchy_build_id,
        )
        if scope_failures:
            return self._reject(src, dst, scope_failures, ["scope_failed"])

        # 8-early. human protection on dismissed / protected confirm
        if human_protected_dismissed and dst != ClueLifecycleState.DISMISSED:
            return self._reject(
                src,
                dst,
                ["human_protection:dismissed_locked"],
                ["human_protected"],
            )

        # 2–3. schema (judgment required for machine non-dismiss transitions
        # except human confirm shortcut).
        parsed: ClueSemanticJudgment | None = None
        if human_protected_confirm and dst == ClueLifecycleState.ACTIVE:
            # Human confirm may bypass semantic classification but not evidence.
            pass
        elif dst == ClueLifecycleState.DISMISSED and allow_machine_dismiss and judgment is None:
            # Machine conflict disposition without judgment is allowed only with codes.
            failures.append("schema_gate:dismiss_without_judgment")
            # still need disposition path — require judgment for machine
            return self._reject(src, dst, failures, ["schema_failed"])
        else:
            parsed = self._coerce_judgment(judgment)
            if parsed is None:
                return self._reject(
                    src,
                    dst,
                    ["schema_gate:unparseable"],
                    ["schema_failed"],
                    gate_status="schema_failed",
                )
            schema_failures = self._schema_failures(package, parsed, dst)
            if schema_failures:
                return self._reject(
                    src,
                    dst,
                    schema_failures,
                    ["schema_failed"],
                    gate_status="schema_failed",
                )

        # 3–4. evidence membership + offset/hash
        evidence_refs = self._collect_evidence_refs(package, parsed, dst)
        evidence_failures = self._evidence_and_hash_failures(package, evidence_refs, parsed)
        if evidence_failures:
            return self._reject(
                src,
                dst,
                evidence_failures,
                ["evidence_failed"],
                gate_status="evidence_failed",
            )

        # 5. temporal order
        temporal_failures = self._temporal_failures(package, parsed, dst)
        if temporal_failures:
            return self._reject(
                src,
                dst,
                temporal_failures,
                ["temporal_failed"],
                gate_status="temporal_failed",
            )

        # 6. transition legality + role evidence (pure schema helpers)
        transition_failures = self._transition_failures(
            src,
            dst,
            evidence_refs,
            consumed_evidence_ids=consumed_evidence_ids,
        )
        if transition_failures:
            return self._reject(
                src,
                dst,
                transition_failures,
                ["transition_failed"],
                gate_status="transition_failed",
            )

        # Unsupported / unavailable relation refs block publication of links that
        # depend on them; for state gates, treat as conflict when present.
        if relation_ref_validation in {"invalid", "source_unavailable"}:
            return self._reject(
                src,
                dst,
                [f"conflict:relation_ref_{relation_ref_validation}"],
                ["relation_ref_blocked"],
                gate_status="conflict_failed",
            )

        # 7. threshold / conflict
        return self._threshold_and_conflict_decision(
            src,
            dst,
            package,
            parsed,
            evidence_refs,
            human_protected_confirm=human_protected_confirm,
            failures=failures,
            reasons=reasons,
        )

    def evaluate_recall_only_rejection(
        self,
        *,
        package: ClueEvidencePackage,
        from_status: ClueLifecycleState | str = ClueLifecycleState.CANDIDATE,
        to_status: ClueLifecycleState | str = ClueLifecycleState.ACTIVE,
    ) -> GateDecision:
        """Prove that recall signals alone never accept a state transition."""

        src = ClueLifecycleState(from_status)
        dst = ClueLifecycleState(to_status)
        return self._reject(
            src,
            dst,
            ["threshold_conflict:recall_only_not_authority"],
            ["recall_only"],
            gate_status="threshold_failed",
        )

    def _threshold_and_conflict_decision(
        self,
        src: ClueLifecycleState,
        dst: ClueLifecycleState,
        package: ClueEvidencePackage,
        parsed: ClueSemanticJudgment | None,
        evidence_refs: list[ClueEvidenceRef],
        *,
        human_protected_confirm: bool,
        failures: list[str],
        reasons: list[str],
    ) -> GateDecision:
        if human_protected_confirm and dst == ClueLifecycleState.ACTIVE:
            # Human confirm still required cue evidence (checked earlier).
            return GateDecision(
                accepted=True,
                status="accepted",
                gate_status="accepted",
                from_status=src.value,
                to_status=dst.value,
                reason_codes=["human_confirm"],
                proposed_evidence=[e.model_dump(mode="json") for e in evidence_refs],
            )

        if parsed is None:
            return self._reject(
                src,
                dst,
                ["threshold_conflict:missing_judgment"],
                ["schema_failed"],
            )

        # Hard conflict flags → reject publication.
        hard_flags = {
            ClueConflictFlag.ORDER_CONFLICT,
            ClueConflictFlag.ENTITY_CONFLICT,
            ClueConflictFlag.INSUFFICIENT_PAYOFF,
        }
        flags = list(parsed.conflict_flags or [])
        hard_hit = [f.value for f in flags if f in hard_flags]
        if hard_hit:
            return self._reject(
                src,
                dst,
                [f"conflict:{code}" for code in hard_hit],
                ["conflict_failed"],
                gate_status="conflict_failed",
            )

        if ClueConflictFlag.MOTIF_ONLY in flags:
            return self._reject(
                src,
                dst,
                ["conflict:MOTIF_ONLY"],
                ["motif_only"],
                gate_status="conflict_failed",
            )

        if ClueConflictFlag.UNRESOLVED_REFERENCE in flags:
            return GateDecision(
                accepted=False,
                status="needs_human_review",
                gate_status="needs_human_review",
                from_status=src.value,
                to_status=dst.value,
                gate_failures=["conflict:UNRESOLVED_REFERENCE"],
                reason_codes=["unresolved_reference"],
                proposed_evidence=[e.model_dump(mode="json") for e in evidence_refs],
            )

        # Recall-only hard negatives: high vector score with unrelated/ambiguous.
        if parsed.classification in {
            ClueSemanticClassification.UNRELATED,
            ClueSemanticClassification.AMBIGUOUS,
        }:
            if dst != ClueLifecycleState.DISMISSED:
                return self._reject(
                    src,
                    dst,
                    [f"threshold_conflict:classification_{parsed.classification.value}"],
                    ["classification_blocks_transition"],
                    gate_status="threshold_failed",
                )

        # Motif-like package: only shared tokens / vector with no later semantic support
        # for payoff/active when classification is cue_only but confidence is from
        # similarity alone — enforced via conflict flags and confidence bands.

        confidence = float(parsed.confidence)
        if confidence < REVIEW_THRESHOLD:
            return self._reject(
                src,
                dst,
                [f"threshold_gate:reject_confidence:{confidence}"],
                ["below_review_threshold"],
                gate_status="threshold_failed",
            )
        if confidence < AUTO_ACCEPT_THRESHOLD:
            return GateDecision(
                accepted=False,
                status="needs_human_review",
                gate_status="needs_human_review",
                from_status=src.value,
                to_status=dst.value,
                gate_failures=[f"threshold_gate:review_confidence:{confidence}"],
                reason_codes=["review_band"],
                proposed_evidence=[e.model_dump(mode="json") for e in evidence_refs],
            )

        # Similarity-alone guard: vector/bm25 without semantic classification match
        # already handled; additionally reject paid_off when later units empty.
        if dst == ClueLifecycleState.PAID_OFF and not package.later_units:
            return self._reject(
                src,
                dst,
                ["conflict:INSUFFICIENT_PAYOFF"],
                ["no_later_evidence"],
                gate_status="conflict_failed",
            )

        return GateDecision(
            accepted=True,
            status="accepted",
            gate_status="accepted",
            from_status=src.value,
            to_status=dst.value,
            reason_codes=["auto_accept"],
            proposed_evidence=[e.model_dump(mode="json") for e in evidence_refs],
        )

    def _coerce_judgment(
        self, judgment: ClueSemanticJudgment | dict[str, Any] | None
    ) -> ClueSemanticJudgment | None:
        if judgment is None:
            return None
        if isinstance(judgment, ClueSemanticJudgment):
            return judgment
        try:
            return ClueSemanticJudgment.model_validate(judgment)
        except Exception:
            return None

    def _schema_failures(
        self,
        package: ClueEvidencePackage,
        judgment: ClueSemanticJudgment,
        dst: ClueLifecycleState,
    ) -> list[str]:
        failures: list[str] = []
        if judgment.candidate_id != package.candidate_id:
            failures.append("schema_gate:candidate_id_mismatch")
        if judgment.confidence < 0 or judgment.confidence > 1:
            failures.append("schema_gate:confidence_out_of_range")
        allowed = CLASSIFICATION_FOR_TARGET.get(dst)
        if allowed is not None and judgment.classification not in allowed:
            failures.append(
                f"schema_gate:classification_mismatch:{judgment.classification.value}"
            )
        return failures

    def _collect_evidence_refs(
        self,
        package: ClueEvidencePackage,
        judgment: ClueSemanticJudgment | None,
        dst: ClueLifecycleState,
    ) -> list[ClueEvidenceRef]:
        units = package.unit_by_id()
        selected: list[ClueEvidenceUnit] = []

        if judgment is not None:
            for eid in judgment.cue_evidence_ids:
                unit = units.get(eid)
                if unit is not None:
                    selected.append(unit)
            for eid in judgment.later_evidence_ids:
                unit = units.get(eid)
                if unit is not None:
                    selected.append(unit)
        else:
            selected = list(package.cue_units)

        refs: list[ClueEvidenceRef] = []
        seen: set[str] = set()
        for unit in selected:
            if unit.identity_key() in seen:
                continue
            seen.add(unit.identity_key())
            role = self._role_for_unit(unit, package, dst, judgment)
            refs.append(
                ClueEvidenceRef(
                    evidence_id=unit.evidence_id,
                    role=role,
                    chapter_id=unit.chapter_id,
                    narrative_chapter_number=unit.narrative_chapter_number,
                    source_start=unit.source_start,
                    source_end=unit.source_end,
                    content_hash=unit.content_hash,
                    excerpt=unit.text[:500] if unit.text else None,
                )
            )
        return refs

    def _role_for_unit(
        self,
        unit: ClueEvidenceUnit,
        package: ClueEvidencePackage,
        dst: ClueLifecycleState,
        judgment: ClueSemanticJudgment | None,
    ) -> ClueEvidenceRole:
        if unit.evidence_id in package.cue_ids():
            return ClueEvidenceRole.CUE
        if dst == ClueLifecycleState.PAID_OFF:
            return ClueEvidenceRole.PAYOFF
        if dst == ClueLifecycleState.REINFORCED:
            return ClueEvidenceRole.REINFORCEMENT
        if dst == ClueLifecycleState.DISMISSED:
            return ClueEvidenceRole.DISPOSITION
        return ClueEvidenceRole.REINFORCEMENT

    def _evidence_and_hash_failures(
        self,
        package: ClueEvidencePackage,
        evidence_refs: list[ClueEvidenceRef],
        judgment: ClueSemanticJudgment | None,
    ) -> list[str]:
        failures: list[str] = []
        allowed = set(package.allowed_evidence_ids())
        units = package.unit_by_id()

        if judgment is not None:
            cited = list(judgment.cue_evidence_ids) + list(judgment.later_evidence_ids)
            out = sorted({eid for eid in cited if eid not in allowed})
            if out:
                failures.append(f"evidence_gate:out_of_package:{','.join(out)}")
                return failures

        if not evidence_refs:
            failures.append("evidence_gate:empty")
            return failures

        for ref in evidence_refs:
            unit = units.get(ref.evidence_id)
            if unit is None:
                failures.append(f"evidence_gate:missing:{ref.evidence_id}")
                continue
            if (
                ref.source_start != unit.source_start
                or ref.source_end != unit.source_end
                or ref.content_hash != unit.content_hash
                or ref.chapter_id != unit.chapter_id
            ):
                failures.append(f"offset_hash_gate:mismatch:{ref.evidence_id}")
        return failures

    def _temporal_failures(
        self,
        package: ClueEvidencePackage,
        judgment: ClueSemanticJudgment | None,
        dst: ClueLifecycleState,
    ) -> list[str]:
        if dst not in {
            ClueLifecycleState.REINFORCED,
            ClueLifecycleState.PAID_OFF,
        }:
            return []
        if judgment is None:
            return []
        units = package.unit_by_id()
        cues = [units[i] for i in judgment.cue_evidence_ids if i in units]
        laters = [units[i] for i in judgment.later_evidence_ids if i in units]
        if not cues:
            return ["temporal_gate:missing_cue"]
        if dst in {ClueLifecycleState.REINFORCED, ClueLifecycleState.PAID_OFF} and not laters:
            return ["temporal_gate:missing_later"]
        earliest_cue = min(u.narrative_key() for u in cues)
        for later in laters:
            if later.narrative_key() <= earliest_cue:
                return [
                    f"temporal_gate:order_conflict:{later.evidence_id}",
                ]
        return []

    def _transition_failures(
        self,
        src: ClueLifecycleState,
        dst: ClueLifecycleState,
        evidence_refs: list[ClueEvidenceRef],
        *,
        consumed_evidence_ids: frozenset[str] | set[str] | None,
    ) -> list[str]:
        if not is_legal_transition(src, dst):
            return [f"transition_gate:illegal:{src.value}->{dst.value}"]
        try:
            validate_evidence_for_transition(
                src,
                dst,
                evidence_refs,
                consumed_evidence_ids=consumed_evidence_ids,
            )
        except LifecycleTransitionError as exc:
            return [f"transition_gate:{exc}"]
        return []

    def _reject(
        self,
        src: ClueLifecycleState,
        dst: ClueLifecycleState,
        failures: list[str],
        reasons: list[str],
        *,
        gate_status: str = "rejected",
    ) -> GateDecision:
        return GateDecision(
            accepted=False,
            status="rejected",
            gate_status=gate_status,
            from_status=src.value,
            to_status=dst.value,
            gate_failures=list(failures),
            reason_codes=list(reasons),
        )


clue_gate_service = ClueGateService()
