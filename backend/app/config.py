"""应用配置管理"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用
    app_name: str = "NovelMind"
    debug: bool = True
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3002"]

    # 数据库
    database_url: str = "postgresql+asyncpg://novelmind:novelmind@localhost:5432/novelmind"

    # AI 模型默认配置
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # 文件存储
    upload_dir: str = "./uploads"
    max_upload_size: int = 50 * 1024 * 1024  # 50MB

    # Embedding
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    model_config = {"env_file": ".env", "env_prefix": "NOVELMIND_"}


settings = Settings()
