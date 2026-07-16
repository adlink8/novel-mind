"""Unit tests for optional source signal DTOs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.builder_contracts import (
    OptionalSourceSignal,
    SourceStatus,
)


pytestmark = pytest.mark.unit


def test_optional_signal_round_trip_and_forbidden() -> None:
    signal = OptionalSourceSignal(
        source_kind="timeline",
        status=SourceStatus.HEALTHY_EMPTY,
        reason_code="no_events",
        signal_keys=(),
        lineage={"version_id": 1},
    )
    assert (
        OptionalSourceSignal.model_validate_json(signal.model_dump_json()) == signal
    )
    with pytest.raises(ValidationError):
        OptionalSourceSignal.model_validate(
            {
                "source_kind": "timeline",
                "status": "healthy_empty",
                "reader_chat": True,
            }
        )


def test_statuses_are_closed() -> None:
    assert {s.value for s in SourceStatus} == {
        "non_empty",
        "healthy_empty",
        "unavailable",
        "lineage_mismatch",
    }
