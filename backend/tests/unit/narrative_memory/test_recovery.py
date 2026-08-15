"""Unit tests for Phase 28-01 failure classification and idempotent resume.

Pure-logic coverage of ``recovery.py``: stable reason codes, terminal-state
derivation, silent-pending detection, resume planning (no whole-book restart),
and the exact-cache checksum gate. DB-backed recovery behaviour is covered by
the integration checkpoint test.
"""

from __future__ import annotations

from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.builder_budget import (
    BudgetExceeded,
    UnknownPricing,
)
from app.services.narrative_memory.builder_contracts import (
    ModelLineage,
    ReasonCode,
    StageLineage,
    TerminalState,
)
from app.services.narrative_memory.builder_gateway import (
    CancelledBeforePersist,
    GatewayError,
    SchemaValidationExhausted,
)
from app.services.narrative_memory.builder_packages import PackageBuildError
from app.services.narrative_memory.builder_repository import BuilderRepositoryError
from app.services.narrative_memory.recovery import (
    RecoveryError,
    build_resume_plan,
    classify_failure,
    is_silently_pending,
    terminal_state_for_status,
    validate_cache_reuse,
)

pytestmark = __import__("pytest").mark.unit

HEX = "a" * 64


def _stage(
    *,
    stage_key: str,
    status: str,
    stage_kind: str = "chapter_state",
    dependency_keys: list[str] | None = None,
    reason_code: str | None = None,
    checkpoint: dict | None = None,
    attempt_count: int = 0,
) -> NarrativeMemoryBuildStage:
    return NarrativeMemoryBuildStage(
        owner_id=1,
        novel_id=1,
        version_id=1,
        run_id=1,
        stage_key=stage_key,
        stage_kind=stage_kind,
        dependency_keys=dependency_keys or [],
        status=status,
        status_reason=reason_code,
        reason_code=reason_code,
        terminal_state=terminal_state_for_status(status),
        checkpoint=checkpoint or {},
        attempt_count=attempt_count,
    )


# ---------------------------------------------------------------------------
# classify_failure: stable reason codes
# ---------------------------------------------------------------------------


def test_classify_cancelled() -> None:
    code, cls = classify_failure(CancelledBeforePersist("stop"))
    assert code == ReasonCode.CANCELLED_BEFORE_PERSIST
    assert cls.value == "cancelled"


def test_classify_budget() -> None:
    code, cls = classify_failure(BudgetExceeded("over"))
    assert code == ReasonCode.BUDGET_EXCEEDED
    assert cls.value == "budget"
    code2, _ = classify_failure(UnknownPricing("no price"))
    assert code2 == ReasonCode.UNKNOWN_PRICING


def test_classify_schema() -> None:
    code, cls = classify_failure(PackageBuildError("bad package"))
    assert code == ReasonCode.SCHEMA_INVALID
    assert cls.value == "schema"
    code2, _ = classify_failure(ValueError("bad value"))
    assert code2 == ReasonCode.SCHEMA_INVALID


def test_classify_provider() -> None:
    code, cls = classify_failure(GatewayError("transport down"))
    assert code == ReasonCode.PROVIDER_TRANSPORT_ERROR
    assert cls.value == "provider"


def test_classify_schema_exhausted_gateway() -> None:
    """Schema-invalid GatewayError (repair exhausted) must not read as transport."""
    code, cls = classify_failure(
        SchemaValidationExhausted("schema_or_business_invalid after repair exhausted")
    )
    assert code == ReasonCode.SCHEMA_INVALID
    assert cls.value == "schema"


def test_classify_source_drift_and_owner() -> None:
    code, _ = classify_failure(
        BuilderRepositoryError("eligibility report checksum mismatch")
    )
    assert code == ReasonCode.SOURCE_DRIFT
    code2, cls2 = classify_failure(BuilderRepositoryError("owner mismatch"))
    assert code2 == ReasonCode.OWNER_MISMATCH or cls2.value == "internal"


def test_classify_internal_fallback() -> None:
    code, cls = classify_failure(RuntimeError("boom"))
    assert code == ReasonCode.INTERNAL_ERROR
    assert cls.value == "internal"


def test_classify_owner_mismatch_recovery() -> None:
    code, cls = classify_failure(RecoveryError(ReasonCode.OWNER_MISMATCH.value))
    assert code == ReasonCode.OWNER_MISMATCH
    assert cls.value == "owner_mismatch"


# ---------------------------------------------------------------------------
# terminal states: completed / isolated / blocked, never silent pending
# ---------------------------------------------------------------------------


def test_terminal_state_mapping() -> None:
    assert terminal_state_for_status("completed") == TerminalState.COMPLETED.value
    assert terminal_state_for_status("failed") == TerminalState.ISOLATED.value
    assert terminal_state_for_status("paused_budget") == TerminalState.ISOLATED.value
    assert terminal_state_for_status("cancelled") == TerminalState.ISOLATED.value
    assert (
        terminal_state_for_status("blocked_dependency") == TerminalState.BLOCKED.value
    )
    assert terminal_state_for_status("pending") is None
    assert terminal_state_for_status("running") is None


def test_silent_pending_detection() -> None:
    untouched = _stage(stage_key="c1", status="pending")
    assert is_silently_pending(untouched) is True
    touched = _stage(
        stage_key="c1",
        status="pending",
        reason_code=ReasonCode.BUDGET_EXCEEDED.value,
    )
    assert is_silently_pending(touched) is False
    terminal = _stage(stage_key="c1", status="failed")
    assert is_silently_pending(terminal) is False


def test_resume_plan_never_reruns_terminal_stages() -> None:
    stages = [
        _stage(
            stage_key="chapter_state:1",
            status="completed",
            reason_code=ReasonCode.COMPLETED_CANDIDATE.value,
        ),
        _stage(
            stage_key="chapter_state:2",
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR.value,
        ),
        _stage(
            stage_key="chapter_state:3",
            status="blocked_dependency",
            reason_code=ReasonCode.DEPENDENCY_FAILED.value,
        ),
        _stage(stage_key="chapter_state:4", status="pending"),
    ]
    plan = build_resume_plan(stages)
    runnable = {item.stage_key for item in plan.runnable}
    assert runnable == {"chapter_state:4"}
    assert plan.has_silent_pending is True
    assert plan.silent_pending_keys == ("chapter_state:4",)
    terminal_keys = {item.stage_key for item in plan.terminal}
    assert terminal_keys == {"chapter_state:1", "chapter_state:2", "chapter_state:3"}


def test_resume_plan_no_silent_pending_after_recovery() -> None:
    stages = [
        _stage(
            stage_key="chapter_state:1",
            status="failed",
            reason_code=ReasonCode.PROVIDER_TRANSPORT_ERROR.value,
        ),
        _stage(
            stage_key="chapter_state:2",
            status="completed",
            reason_code=ReasonCode.COMPLETED_CANDIDATE.value,
        ),
    ]
    plan = build_resume_plan(stages)
    assert plan.has_silent_pending is False
    assert plan.runnable == ()
    # A failed chapter alone does not trigger whole-book restart.
    assert {i.stage_key for i in plan.terminal} == {
        "chapter_state:1",
        "chapter_state:2",
    }


def test_dependents_are_blocked_not_restarted() -> None:
    """Chapter failure blocks dependents transitively; never whole-book restart."""
    stages = [
        _stage(stage_key="chapter_state:1", status="completed"),
        _stage(stage_key="chapter_state:2", status="failed"),
        _stage(
            stage_key="arc_volume_aggregate:arc",
            status="pending",
            stage_kind="arc_volume_aggregate",
            dependency_keys=["chapter_state:1", "chapter_state:2"],
        ),
        _stage(
            stage_key="global_story:book",
            status="pending",
            stage_kind="global_aggregate",
            dependency_keys=["arc_volume_aggregate:arc"],
        ),
    ]
    from app.services.narrative_memory.builder_repository import BuilderRepository

    dependents = BuilderRepository._transitive_dependents(stages, "chapter_state:2")
    assert "arc_volume_aggregate:arc" in dependents
    assert "global_story:book" in dependents
    # The transitive closure does not include the completed sibling.
    assert "chapter_state:1" not in dependents


# ---------------------------------------------------------------------------
# exact cache reuse gate: checksum-identical only (D-04)
# ---------------------------------------------------------------------------


def _lineage(*, provider: str = "p", revision: str = "1") -> StageLineage:
    return StageLineage(
        model_lineage=ModelLineage(
            provider=provider, model="m", deployment="d", revision=revision
        ),
        prompt_hash=HEX,
        schema_hash=HEX,
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
    )


def test_cache_reuse_identical_inputs() -> None:
    lineage = _lineage()
    ok, reason = validate_cache_reuse(
        stored_source_checksum=HEX,
        stored_lineage=lineage.model_dump(mode="json"),
        stored_package_checksum=HEX,
        current_source_checksum=HEX,
        current_lineage=lineage,
        current_package_checksum=HEX,
    )
    assert ok is True
    assert reason is None


def test_cache_reuse_rejects_source_drift() -> None:
    ok, reason = validate_cache_reuse(
        stored_source_checksum=HEX,
        stored_lineage=_lineage().model_dump(mode="json"),
        stored_package_checksum=HEX,
        current_source_checksum="b" * 64,
        current_lineage=_lineage(),
        current_package_checksum=HEX,
    )
    assert ok is False
    assert reason == ReasonCode.SOURCE_DRIFT


def test_cache_reuse_rejects_lineage_drift() -> None:
    ok, reason = validate_cache_reuse(
        stored_source_checksum=HEX,
        stored_lineage=_lineage().model_dump(mode="json"),
        stored_package_checksum=HEX,
        current_source_checksum=HEX,
        current_lineage=_lineage(provider="p2", revision="2"),
        current_package_checksum=HEX,
    )
    assert ok is False
    assert reason == ReasonCode.STALE_CACHE


def test_cache_reuse_rejects_package_drift() -> None:
    ok, reason = validate_cache_reuse(
        stored_source_checksum=HEX,
        stored_lineage=_lineage().model_dump(mode="json"),
        stored_package_checksum=HEX,
        current_source_checksum=HEX,
        current_lineage=_lineage(),
        current_package_checksum="c" * 64,
    )
    assert ok is False
    assert reason == ReasonCode.STALE_CACHE
