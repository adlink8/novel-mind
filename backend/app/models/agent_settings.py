"""Owner-scoped Agent settings and task model bindings."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentSettings(TimestampMixin, Base):
    """Typed Agent switches owned by exactly one user."""

    __tablename__ = "agent_settings"

    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    auto_deep_analysis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    memory_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    show_analysis_progress: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_analysis_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_create_candidate_artifacts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class AgentTaskModelBinding(TimestampMixin, Base):
    """Owner-scoped mapping from a supported task to an AI model config."""

    __tablename__ = "agent_task_model_bindings"
    __table_args__ = (
        UniqueConstraint("owner_id", "task", name="uq_agent_task_binding_owner_task"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_model_configs.id", ondelete="CASCADE"), nullable=False
    )
