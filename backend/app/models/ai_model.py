"""
AI 模型配置 ORM 模型

本表存储用户配置的 AI 模型信息，用于:
1. LiteLLM 调用时的参数来源（provider、model_id、api_key、base_url）
2. AI 路由器选择模型时的依据（tier）
3. 前端设置页面的模型管理

层级 (tier):
  - quality  : 质量优先（GPT-4o、Claude Opus）→ 深度分析、续写创作
  - balanced : 均衡（GPT-4o-mini、Claude Sonnet）→ 摘要、抽取
  - budget   : 经济（Ollama 本地模型）→ 嵌入、简单任务

安全注意:
  api_key 使用 Fernet 对称加密存储，通过 property 自动加解密。
  数据库中存储的是密文，内存中访问时自动解密。
"""

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.core.crypto import encrypt_text, decrypt_text


class AIModelConfig(TimestampMixin, Base):
    """
    AI 模型配置表：存储用户添加的 AI 模型及其调用参数。

    每条记录代表一个可用的 AI 模型实例（如 "我的 GPT-4o"、"本地 Qwen2"）。
    is_default=True 的模型为默认模型，用于未指定模型时的自动选择。

    安全特性:
      - api_key 使用 Fernet 对称加密存储（数据库中存密文）
      - 通过 api_key property 自动加解密，业务代码无感知
    """

    __tablename__ = "ai_model_configs"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_ai_model_owner_name"),
    )

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 模型标识
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 提供商: openai / anthropic / ollama / custom
    model_id: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # 模型标识: gpt-4o / claude-3.5-sonnet / qwen2:7b

    # 连接参数（api_key 加密存储）
    _api_key: Mapped[str | None] = mapped_column(
        "api_key", Text
    )  # API Key（加密后的密文）
    base_url: Mapped[str | None] = mapped_column(
        String(500)
    )  # 自定义 API 地址（Ollama/自部署服务）

    # 路由参数
    tier: Mapped[str] = mapped_column(
        String(20), default="balanced"
    )  # 层级: quality / balanced / budget
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)  # 最大输出 token 数
    temperature: Mapped[float] = mapped_column(
        default=0.7
    )  # 生成温度（0-2，越高越随机）

    # 状态标记
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否为默认模型
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True
    )  # 是否启用（软删除标记）

    # 扩展参数
    extra_params: Mapped[dict | None] = mapped_column(
        JSON, default=dict
    )  # LiteLLM 额外参数

    # ─────────── api_key 自动加解密 property ───────────

    @property
    def api_key(self) -> str | None:
        """获取解密后的 API Key"""
        return decrypt_text(self._api_key)

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        """设置 API Key（自动加密）"""
        self._api_key = encrypt_text(value)
