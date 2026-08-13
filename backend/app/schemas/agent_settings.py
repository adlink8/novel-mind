"""Typed public contract for owner-scoped Agent settings."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class AgentTaskModelBindings(BaseModel):
    """One optional owner-owned model ID per supported Agent task."""

    model_config = ConfigDict(extra="forbid")

    qa: StrictInt | None = None
    deep_analysis: StrictInt | None = None
    continuation: StrictInt | None = None
    illustration: StrictInt | None = None
    rag_eval: StrictInt | None = None
    embedding: StrictInt | None = None


class AgentSettingsResponse(BaseModel):
    """The complete public Agent settings projection."""

    model_config = ConfigDict(extra="forbid")

    auto_deep_analysis: StrictBool
    memory_enabled: StrictBool
    memory_retention_days: StrictInt | None = Field(default=None, ge=1)
    show_analysis_progress: StrictBool
    notify_analysis_complete: StrictBool
    auto_create_candidate_artifacts: StrictBool
    task_model_bindings: AgentTaskModelBindings


class AgentSettingsUpdate(AgentSettingsResponse):
    """Full replacement payload for the owner-scoped Agent config."""
