"""RAG quality SUT scoring, metrics, and deterministic policy arbiter (06-04).

Consumes signed frozen fixtures + calibrated Judge lineage from 06-03.
Does NOT generate fixtures. Judge alone cannot promote; arbiter is final gate.

D-06..D-08: retrieval+answer, four quality metrics, thresholds, fail-closed.

Refactored from a single 1962-line module into a package:
``types.py`` (SUT/Judge protocols + DependencyOutage),
``lineage.py`` (hash helpers + shared status constants),
``policy.py`` (versioned policy loading),
``metrics.py`` (retrieval/claim metrics),
``bootstrap.py`` (bootstrap/consistency stats),
``stubs.py`` (offline stub SUT + judge),
``scoring.py`` (run_case_once + aggregate),
``arbiter.py`` (deterministic policy arbiter),
``core.py`` (fixture/lineage validation + run_quality_evaluation),
``baseline.py`` (durable baseline prepare/commit + cross-chunker report),
``health.py`` (operational probes).

This ``__init__.py`` re-exports the full original public surface so that
``from app.services.rag_quality import ...`` keeps working unchanged.
"""

from __future__ import annotations

from .arbiter import apply_policy_arbiter
from .baseline import (
    BaselineServiceError,
    _active_public as _active_public,
    _candidate_public as _candidate_public,
    _run_lineage_complete as _run_lineage_complete,
    _validate_run_for_baseline as _validate_run_for_baseline,
    _BASELINE_ELIGIBLE as _BASELINE_ELIGIBLE,
    build_cross_chunker_report,
    commit_baseline_candidate,
    compute_prepare_fingerprint,
    get_active_baseline,
    prepare_baseline_candidate,
)
from .bootstrap import (
    bootstrap_lower_bound,
    case_repeat_consistency,
    verdict_consistency,
)
from .core import (
    logger,
    make_baseline_from_metrics,
    run_quality_evaluation,
    validate_calibrated_lineage,
    validate_dependency_health,
    validate_fixtures_for_scoring,
)
from .health import default_healthy, probe_ollama_health
from .lineage import (
    COMPARABLE_STATUSES,
    NON_COMPARABLE_TERMINAL,
    SUT_STAGES,
    _SHA256_HEX_LEN as _SHA256_HEX_LEN,
    build_stage_cache_key,
    build_quality_input_hash,
    canonicalize_chunker_lineage,
    lineage_five_tuple,
    recompute_chunker_config_hash,
)
from .metrics import (
    _gold_content_hashes as _gold_content_hashes,
    _normalize_tokens as _normalize_tokens,
    _retrieved_hashes as _retrieved_hashes,
    claim_supported_by_evidence,
    context_precision_at_k,
    context_recall_at_k,
    deterministic_claim_metrics,
)
from .policy import (
    ANSWER_JUDGE_PROMPT_VERSION,
    POLICY_VERSION,
    answer_judge_prompt_hash,
    load_policy,
    policy_hash,
    policy_path,
)
from .scoring import (
    CaseRunArtifact,
    aggregate_run_metrics,
    run_case_once,
)
from .stubs import (
    default_stub_answer,
    default_stub_answer_judge,
    default_stub_retrieve,
)
from .types import (
    AnswerFn,
    AnswerJudgeFn,
    DependencyOutage,
    HealthProbeFn,
    RetrieveFn,
)

__all__ = [
    # constants
    "POLICY_VERSION",
    "ANSWER_JUDGE_PROMPT_VERSION",
    "COMPARABLE_STATUSES",
    "NON_COMPARABLE_TERMINAL",
    "SUT_STAGES",
    # protocol types
    "RetrieveFn",
    "AnswerFn",
    "AnswerJudgeFn",
    "HealthProbeFn",
    # exceptions
    "DependencyOutage",
    "BaselineServiceError",
    # lineage / hashing
    "recompute_chunker_config_hash",
    "canonicalize_chunker_lineage",
    "lineage_five_tuple",
    "build_quality_input_hash",
    "build_stage_cache_key",
    # policy
    "policy_path",
    "load_policy",
    "policy_hash",
    "answer_judge_prompt_hash",
    # retrieval / claim metrics
    "context_precision_at_k",
    "context_recall_at_k",
    "claim_supported_by_evidence",
    "deterministic_claim_metrics",
    # bootstrap / consistency
    "bootstrap_lower_bound",
    "verdict_consistency",
    "case_repeat_consistency",
    # stub SUT + judge
    "default_stub_retrieve",
    "default_stub_answer",
    "default_stub_answer_judge",
    # input validation
    "validate_fixtures_for_scoring",
    "validate_calibrated_lineage",
    "validate_dependency_health",
    # scoring
    "CaseRunArtifact",
    "run_case_once",
    "aggregate_run_metrics",
    # arbiter
    "apply_policy_arbiter",
    # orchestration
    "run_quality_evaluation",
    "make_baseline_from_metrics",
    # durable baseline service
    "compute_prepare_fingerprint",
    "prepare_baseline_candidate",
    "commit_baseline_candidate",
    "get_active_baseline",
    "build_cross_chunker_report",
    # health probes
    "default_healthy",
    "probe_ollama_health",
    # module logger (original module-level name)
    "logger",
]
