"""
AI 调用日志 ORM 模型（成本追踪）

本表记录每次 AI API 调用的详细信息，用于:
1. 成本统计（cost_usd 按模型单价 × token 数计算）
2. 用量分析（按模型/任务类型聚合）
3. 性能监控（latency_ms 识别慢调用）
4. 预算告警（当累计费用超过阈值时告警）

任务类型 (task_type):
  - analysis      : 剧情分析
  - embedding     : 向量嵌入
  - fanfiction    : 同人文续写
  - nlp           : NLP 管线（NER、关系抽取）
  - summary       : 摘要生成
  - timeline      : 时间线事件提取
"""

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIUsageLog(TimestampMixin, Base):
    """
    AI 调用日志表：记录每次 AI API 调用的 token 用量和费用。

    每次 AI 调用（chat、embedding、stream_chat）都应写入一条日志，
    用于前端"用量概览"面板和成本监控。
    """

    __tablename__ = "ai_usage_logs"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 调用信息
    model_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )  # 模型名称
    provider: Mapped[str] = mapped_column(String(50), default="openai")  # 提供商
    task_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # 任务类型: analysis / embedding / fanfiction / nlp / summary / timeline

    # Token 用量
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 输入 token 数
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 输出 token 数
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)  # 费用（美元）

    # 性能指标
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)  # 响应延迟（毫秒）

    # 关联信息
    novel_id: Mapped[int | None] = mapped_column(
        Integer, index=True
    )  # 关联小说 ID（可选）
    status: Mapped[str] = mapped_column(
        String(20), default="success"
    )  # 调用状态: success / failed / timeout
