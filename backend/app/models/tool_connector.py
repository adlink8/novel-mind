"""Owner-scoped restricted HTTPS Tool connector persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin


TOOL_CONNECTOR_STATUSES = ("draft", "validated", "active", "disabled")


class ToolConnector(TimestampMixin, Base):
    """Stable owner-scoped identity for an append-only connector version chain."""

    __tablename__ = "tool_connectors"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_tool_connectors_owner_name"),
        Index("idx_tool_connectors_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class ToolConnectorVersion(Base):
    """Immutable connector configuration snapshot."""

    __tablename__ = "tool_connector_versions"
    __table_args__ = (
        UniqueConstraint("connector_id", "version", name="uq_tool_connector_versions_version"),
        CheckConstraint(
            "status IN ('draft','validated','active','disabled')",
            name="ck_tool_connector_versions_status",
        ),
        CheckConstraint("method IN ('GET','POST')", name="ck_tool_connector_versions_method"),
        Index("idx_tool_connector_versions_owner", "owner_id", "connector_id", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connector_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tool_connectors.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    request_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
