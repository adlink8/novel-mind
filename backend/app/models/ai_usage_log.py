"""AI 调用日志模型（成本追踪）"""

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIUsageLog(TimestampMixin, Base):
    __tablename__ = "ai_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), default="openai")
    task_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # analysis / embedding / fanfiction / nlp / summary / timeline
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    novel_id: Mapped[int | None] = mapped_column(Integer, index=True)  # 关联小说（可选）
    status: Mapped[str] = mapped_column(String(20), default="success")  # success / failed / timeout
