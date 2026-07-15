"""Phase 11 clue and foreshadow tracking services.

Deterministic candidate recall, bounded evidence packages, strict semantic
judgment and local gates. Lifecycle persistence and durable workers live in
later plans; this package has no write authority over lifecycle rows.
"""

from app.services.clues.candidates import (
    CandidateRecallConfig,
    ClueCandidateDraft,
    ClueCandidateRecallService,
    HierarchyEvidenceNode,
    TimelineEventRef,
    clue_candidate_recall_service,
    stable_candidate_id,
)
from app.services.clues.evidence import (
    ClueEvidencePackage,
    ClueEvidenceScopeError,
    ClueEvidenceUnit,
    build_clue_evidence_package,
    make_clue_evidence_unit,
    package_hash_for,
    sha256_json,
    sha256_text,
)
from app.services.clues.gates import (
    AUTO_ACCEPT_THRESHOLD,
    REVIEW_THRESHOLD,
    ClueGateService,
    GateDecision,
    clue_gate_service,
    policy_hash,
)
from app.services.clues.llm_judge import (
    ClueJudgmentResult,
    ClueLLMJudgeService,
    clue_llm_judge_service,
)
from app.services.clues.sources import (
    NullRelationshipObservationSource,
    Phase09BoundRelationshipSource,
    PrimarySelectionCitationRef,
    RelationshipObservationRef,
    RelationshipSourceResult,
    StaticRelationshipObservationSource,
    UnavailableRelationshipObservationSource,
    VersionedRelationshipObservationSource,
    accept_primary_selection_citation_refs,
    reject_freeform_chat_as_evidence,
)

__all__ = [
    "AUTO_ACCEPT_THRESHOLD",
    "REVIEW_THRESHOLD",
    "CandidateRecallConfig",
    "ClueCandidateDraft",
    "ClueCandidateRecallService",
    "ClueEvidencePackage",
    "ClueEvidenceScopeError",
    "ClueEvidenceUnit",
    "ClueGateService",
    "ClueJudgmentResult",
    "ClueLLMJudgeService",
    "GateDecision",
    "HierarchyEvidenceNode",
    "NullRelationshipObservationSource",
    "Phase09BoundRelationshipSource",
    "PrimarySelectionCitationRef",
    "RelationshipObservationRef",
    "RelationshipSourceResult",
    "StaticRelationshipObservationSource",
    "TimelineEventRef",
    "UnavailableRelationshipObservationSource",
    "VersionedRelationshipObservationSource",
    "accept_primary_selection_citation_refs",
    "build_clue_evidence_package",
    "clue_candidate_recall_service",
    "clue_gate_service",
    "clue_llm_judge_service",
    "make_clue_evidence_unit",
    "package_hash_for",
    "policy_hash",
    "reject_freeform_chat_as_evidence",
    "sha256_json",
    "sha256_text",
    "stable_candidate_id",
]
