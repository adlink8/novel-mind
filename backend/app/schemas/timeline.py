"""
时间线事件请求/响应 Pydantic 模型

事件类型:
  - plot     : 剧情推进事件
  - character: 角色相关事件
  - world    : 世界观事件
  - conflict : 冲突事件
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictTimelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimePrecision(StrEnum):
    EXACT = "exact"
    RELATIVE = "relative"
    FUZZY = "fuzzy"
    UNKNOWN = "unknown"


class Participant(StrictTimelineModel):
    mention: str = Field(min_length=1, max_length=100)
    entity_id: int | None = None


class EvidenceRef(StrictTimelineModel):
    chapter_id: int = Field(gt=0)
    evidence_id: str = Field(min_length=1, max_length=80)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_offsets(self) -> "EvidenceRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class StoryTime(StrictTimelineModel):
    precision: TimePrecision
    expression: str | None = Field(default=None, min_length=1, max_length=120)
    exact_time: datetime | None = None
    anchor_event_id: str | None = Field(default=None, min_length=1, max_length=80)
    relation: Literal["before", "after", "simultaneous"] | None = None
    fuzzy_start: datetime | None = None
    fuzzy_end: datetime | None = None

    @model_validator(mode="after")
    def validate_precision_shape(self) -> "StoryTime":
        if self.precision == TimePrecision.EXACT:
            if self.expression is None or self.exact_time is None:
                raise ValueError(
                    "exact time requires an explicit expression and exact_time"
                )
            forbidden = (
                self.anchor_event_id,
                self.relation,
                self.fuzzy_start,
                self.fuzzy_end,
            )
        elif self.precision == TimePrecision.RELATIVE:
            if (
                self.expression is None
                or self.anchor_event_id is None
                or self.relation is None
            ):
                raise ValueError(
                    "relative time requires expression, anchor_event_id, and relation"
                )
            forbidden = (self.exact_time, self.fuzzy_start, self.fuzzy_end)
        elif self.precision == TimePrecision.FUZZY:
            if self.expression is None:
                raise ValueError("fuzzy time requires an explicit source expression")
            if (
                self.fuzzy_start
                and self.fuzzy_end
                and self.fuzzy_end < self.fuzzy_start
            ):
                raise ValueError("fuzzy_end must not precede fuzzy_start")
            forbidden = (self.exact_time, self.anchor_event_id, self.relation)
        else:
            forbidden = (
                self.exact_time,
                self.anchor_event_id,
                self.relation,
                self.fuzzy_start,
                self.fuzzy_end,
            )
        if any(value is not None for value in forbidden):
            raise ValueError(f"fields do not match {self.precision} precision")
        return self


class StoryTimeConstraint(StrictTimelineModel):
    source_candidate_id: str = Field(min_length=1, max_length=80)
    target_candidate_id: str = Field(min_length=1, max_length=80)
    relation: Literal["before", "after", "simultaneous"]
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_distinct_events(self) -> "StoryTimeConstraint":
        if self.source_candidate_id == self.target_candidate_id:
            raise ValueError("a story-time constraint requires two distinct events")
        return self


class EventCandidate(StrictTimelineModel):
    candidate_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    event_type: Literal["plot", "character", "world", "conflict"]
    narrative_chapter_number: int = Field(ge=0)
    narrative_index: int = Field(ge=0)
    participants: list[Participant] = Field(default_factory=list)
    story_time: StoryTime
    evidence: list[EvidenceRef] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TimelineExtraction(StrictTimelineModel):
    events: list[EventCandidate]
    story_time_constraints: list[StoryTimeConstraint] = Field(default_factory=list)


class TimelineEventCreate(BaseModel):
    """创建时间线事件请求"""

    novel_id: int
    chapter_id: Optional[int] = None  # 关联章节（可选）
    event_title: str = Field(..., min_length=1, max_length=200, description="事件标题")
    event_description: Optional[str] = Field(None, description="事件描述")
    event_type: str = Field(
        default="plot", description="事件类型: plot / character / world / conflict"
    )
    sort_order: float = Field(default=0.0, description="排序权重（越小越靠前）")
    characters_involved: Optional[str] = Field(None, description="涉及角色（逗号分隔）")
    location: Optional[str] = Field(None, max_length=200, description="地点")
    time_reference: Optional[str] = Field(None, max_length=200, description="时间参考")


class TimelineEventUpdate(BaseModel):
    """更新时间线事件请求（所有字段可选）"""

    event_title: Optional[str] = Field(None, min_length=1, max_length=200)
    event_description: Optional[str] = None
    event_type: Optional[str] = None
    sort_order: Optional[float] = None
    characters_involved: Optional[str] = None
    location: Optional[str] = Field(None, max_length=200)
    time_reference: Optional[str] = Field(None, max_length=200)
    chapter_id: Optional[int] = None


class TimelineEventResponse(BaseModel):
    """时间线事件响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_id: Optional[int] = None
    event_title: str
    event_description: Optional[str] = None
    event_type: str
    sort_order: float
    characters_involved: Optional[str] = None
    location: Optional[str] = None
    time_reference: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TimelineVersionSource(StrEnum):
    ACTIVE = "active"
    RUNNING_CANDIDATE = "running_candidate"


class TimelineOrdering(StrEnum):
    NARRATIVE = "narrative"
    STORY = "story"


class TimelineRunResponse(StrictTimelineModel):
    id: int
    novel_id: int
    version_id: int | None = None
    status: str
    status_reason: str | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool = False
    updated_at: datetime | None = None


class TimelineVisibleEvent(StrictTimelineModel):
    id: int
    logical_event_id: str
    title: str
    description: str
    event_type: str
    narrative_chapter_number: int
    source_start: int = Field(ge=0)
    narrative_index: int
    story_rank: int | None = None
    time_precision: TimePrecision
    time_expression: str | None = None
    confidence: float
    participants: list[Participant] = Field(default_factory=list)
    provenance: dict[str, Literal["machine", "manual"]] = Field(default_factory=dict)


class TimelineVisibleEdge(StrictTimelineModel):
    source_event_id: int
    target_event_id: int
    edge_type: str
    confidence: float


class TimelineCounts(StrictTimelineModel):
    events: int = 0
    participants: int = 0
    causal_edges: int = 0


class TimelineVersionView(StrictTimelineModel):
    source: TimelineVersionSource
    version_id: int
    status: str
    progress: dict[str, Any] = Field(default_factory=dict)
    events: list[TimelineVisibleEvent] = Field(default_factory=list)
    causal_edges: list[TimelineVisibleEdge] = Field(default_factory=list)
    counts: TimelineCounts = Field(default_factory=TimelineCounts)
    aggregates: dict[str, int] = Field(default_factory=dict)
    previews: list[str] = Field(default_factory=list)


class TimelineEnvelope(StrictTimelineModel):
    active: TimelineVersionView | None = None
    running_candidate: TimelineVersionView | None = None


class TimelineRollbackRequest(StrictTimelineModel):
    target_version_id: int
    expected_revision: int = Field(ge=0)


class TimelineEditRequest(StrictTimelineModel):
    field_name: Literal["title", "description", "event_type", "time_expression"]
    value: Any


class TimelinePreferenceRequest(StrictTimelineModel):
    full_book: bool
