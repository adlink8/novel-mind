"""Public user preference memory response contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserPreferenceMemoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_message_id: int
    kind: str
    value: str
    confidence: float
    explicit: bool
    created_at: datetime
    expires_at: datetime | None


class UserPreferenceMemoryList(BaseModel):
    items: list[UserPreferenceMemoryView]
    total: int
