"""
应用配置管理

使用 pydantic-settings 从环境变量和 .env 文件加载配置。
所有配置项以 NOVELMIND_ 为前缀（如 NOVELMIND_DEBUG=true）。

典型用法:
  from app.config import settings
  if settings.debug:
      ...
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用全局配置，自动从环境变量 / .env 文件读取"""

    # ── 应用基础 ──
    app_name: str = "NovelMind"           # 应用名称（用于日志、API 文档标题）
    debug: bool = True                    # 调试模式：True 时启用详细日志和 SQL 回显
    cors_origins: list[str] = [           # 允许的跨域来源（前端开发服务器地址）
        "http://localhost:3000",          # Next.js 默认端口
        "http://localhost:3002",          # 备用前端端口
    ]

    # ── 数据库 ──
    # 异步 PostgreSQL 连接串，使用 asyncpg 驱动
    # 格式: postgresql+asyncpg://用户名:密码@主机:端口/数据库名
    database_url: str = "postgresql+asyncpg://novelmind:novelmind@localhost:5432/novelmind"

    # ── AI 模型 API 密钥 ──
    # 各提供商的密钥和端点，用于 LiteLLM 统一调用
    openai_api_key: str = ""              # OpenAI API Key（sk-xxx）
    openai_base_url: str = "https://api.openai.com/v1"  # OpenAI 兼容 API 地址
    anthropic_api_key: str = ""           # Anthropic API Key（sk-ant-xxx）
    ollama_base_url: str = "http://localhost:11434"  # 本地 Ollama 服务地址

    # ── 文件存储 ──
    upload_dir: str = "./uploads"         # 小说上传文件存储目录
    max_upload_size: int = 50 * 1024 * 1024  # 最大上传大小: 50MB

    # ── Embedding 配置 ──
    # 用于 RAG 向量检索的文本嵌入模型
    embedding_model: str = "text-embedding-3-small"  # 默认使用 OpenAI 小型嵌入模型
    embedding_dimensions: int = 1536      # 向量维度（与模型匹配）

    # pydantic-settings 配置：从 .env 文件加载，环境变量前缀为 NOVELMIND_
    model_config = {"env_file": ".env", "env_prefix": "NOVELMIND_"}


# 全局配置单例，整个应用通过 settings.xxx 访问配置
settings = Settings()
