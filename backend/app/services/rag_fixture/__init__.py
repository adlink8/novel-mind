"""RAG quality frozen fixture, adversarial gates, G/J isolation, Judge calibration.

Phase 06-03 (D-01..D-03, D-11, D-15). Does NOT score SUT answers (that is 06-04).

Offline contract tests use deterministic fake transcripts / stub judges — no live Ollama.

Refactored from a single 1481-line module into a package:
``_hashing.py`` (hash/sign utilities, shared by 8+ chunking modules),
``core.py`` (snapshot / lineage / deterministic checks / freeze pipeline),
``adversarial.py`` (adversarial gates + Judge calibration).
This ``__init__.py`` re-exports the full original public surface so that
``from app.services.rag_fixture import ...`` keeps working unchanged.
"""

from ._hashing import (
    content_hash,
    fail_closed,
    prompt_file_hash,
    quote_hash,
    schema_contract_hash,
    sign_payload,
    stable_hash,
    text_hash,
    verify_signature,
)
from .adversarial import (
    ADVERSARIAL_INJECTION_MARKERS,
    SCHEMA_SMUGGLING_KEYS,
    CalibrationJudgeFn,
    MAX_QUESTION_LEN,
    MAX_QUOTE_LEN,
    _contains_injection as _contains_injection,
    _find_smuggled_keys as _find_smuggled_keys,
    assert_calibration_benchmark_isolation,
    calibration_suite_hash,
    default_stub_calibration_judge,
    evaluate_adversarial_suite,
    freeze_calibration_suite,
    load_adversarial_suite,
    run_judge_calibration,
    validate_adversarial_payload,
    verify_calibration_suite,
)
from .core import (
    DEFAULT_SIGNING_SECRET,
    JUDGE_MIN_SCORE,
    MAX_ATTEMPTS,
    MAX_CHUNK_TEXT_LEN,
    MAX_REGENERATE,
    FailedPolicyError,
    GeneratorFn,
    InvalidFixtureError,
    InvalidLineageError,
    JudgeFn,
    RagFixtureError,
    _all_refs as _all_refs,
    _chunk_manifest_entries as _chunk_manifest_entries,
    _claim_ids_unique as _claim_ids_unique,
    build_chunk,
    build_source_snapshot,
    compute_fixture_hash,
    create_fixture_job,
    default_stub_generator,
    default_stub_judge,
    eval_case_hash_payload,
    evals_dir,
    freeze_eval_case,
    judge_accepts,
    load_json,
    make_evidence_ref,
    package_benchmark_suite,
    prompts_dir,
    resolve_lineage,
    run_deterministic_checks,
    run_fixture_pipeline,
    snapshot_chunk_map,
    validate_generator_judge_isolation,
    verify_evidence_ref,
    verify_frozen_case,
    verify_source_snapshot,
)

__all__ = [
    # constants
    "MAX_REGENERATE",
    "MAX_ATTEMPTS",
    "JUDGE_MIN_SCORE",
    "MAX_QUESTION_LEN",
    "MAX_QUOTE_LEN",
    "MAX_CHUNK_TEXT_LEN",
    "ADVERSARIAL_INJECTION_MARKERS",
    "SCHEMA_SMUGGLING_KEYS",
    "DEFAULT_SIGNING_SECRET",
    "GeneratorFn",
    "JudgeFn",
    "CalibrationJudgeFn",
    # exceptions
    "RagFixtureError",
    "InvalidLineageError",
    "InvalidFixtureError",
    "FailedPolicyError",
    # hashing / signing
    "stable_hash",
    "text_hash",
    "content_hash",
    "quote_hash",
    "prompt_file_hash",
    "schema_contract_hash",
    "sign_payload",
    "verify_signature",
    "fail_closed",
    # source snapshot
    "build_chunk",
    "build_source_snapshot",
    "verify_source_snapshot",
    "snapshot_chunk_map",
    "make_evidence_ref",
    "verify_evidence_ref",
    # model lineage
    "resolve_lineage",
    "validate_generator_judge_isolation",
    # deterministic checks
    "run_deterministic_checks",
    "judge_accepts",
    # fixture hash / freeze
    "eval_case_hash_payload",
    "compute_fixture_hash",
    "freeze_eval_case",
    "verify_frozen_case",
    # freeze pipeline
    "create_fixture_job",
    "default_stub_generator",
    "default_stub_judge",
    "run_fixture_pipeline",
    # adversarial validation
    "validate_adversarial_payload",
    "load_adversarial_suite",
    "evaluate_adversarial_suite",
    # calibration
    "calibration_suite_hash",
    "freeze_calibration_suite",
    "verify_calibration_suite",
    "assert_calibration_benchmark_isolation",
    "default_stub_calibration_judge",
    "run_judge_calibration",
    # fixture suite loaders
    "load_json",
    "package_benchmark_suite",
    "prompts_dir",
    "evals_dir",
]
