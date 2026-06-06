"""AI 模型配置模型"""

from sqlalchemy import Boolean, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIModelConfig(TimestampMixin, Base):
    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # openai / anthropic / ollama / custom
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)  # "gpt-4o" "claude-3.5-sonnet" etc.
    api_key: Mapped[str | None] = mapped_column(Text)  # 加密存储
    base_url: Mapped[str | None] = mapped_column(String(500))
    tier: Mapped[str] = mapped_column(String(20), default="balanced")  # quality / balanced / budget
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(default=0.7)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_params: Mapped[dict | None] = mapped_column(JSON, default=dict)
