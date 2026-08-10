"""
应用配置管理

使用 pydantic-settings 从环境变量和 .env 文件加载配置。
所有配置项以 NOVELMIND_ 为前缀（如 NOVELMIND_DEBUG=true）。

典型用法:
  from app.config import settings
  if settings.debug:
      ...
"""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


DEV_JWT_SECRET = "dev-only-jwt-secret-change-before-production"
DEV_ENCRYPTION_KEY = "dev-only-encryption-key-change-before-production"


class Settings(BaseSettings):
    """应用全局配置，自动从环境变量 / .env 文件读取"""

    # ── 应用基础 ──
    app_name: str = "NovelMind"  # 应用名称（用于日志、API 文档标题）
    debug: bool = True  # 调试模式：True 时启用详细日志和 SQL 回显
    cors_origins: list[str] = [  # 允许的跨域来源（前端开发服务器 + 公网隧道）
        "http://localhost:3000",  # Next.js 默认端口
        "http://localhost:3001",  # 备用前端端口
        "http://localhost:3002",
        "http://localhost:3003",
        "http://localhost:3004",
        "http://localhost:3005",  # 3000 被占用时的开发端口
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:3003",
        "http://127.0.0.1:3004",
        "http://127.0.0.1:3005",
        # Cloudflare Tunnel 公网域名（登录 Cookie / CSRF Origin 校验需要）
        # 生产域名通过环境变量 NOVELMIND_CORS_ORIGINS 覆盖，此处仅保留开发来源
    ]

    # ── 数据库 ──
    # 异步 PostgreSQL 连接串，使用 asyncpg 驱动
    # 格式: postgresql+asyncpg://用户名:密码@主机:端口/数据库名
    database_url: str = (
        "postgresql+asyncpg://novelmind:novelmind@localhost:5432/novelmind"
    )

    # ── AI 模型 API 密钥 ──
    # 各提供商的密钥和端点，用于 LiteLLM 统一调用
    openai_api_key: str = ""  # OpenAI API Key（sk-xxx）
    openai_base_url: str = "https://api.openai.com/v1"  # OpenAI 兼容 API 地址
    anthropic_api_key: str = ""  # Anthropic API Key（sk-ant-xxx）
    gemini_api_key: str = ""  # Google AI Studio Key（仅 chat_provider=gemini 时使用）
    # 聊天提供商：vertex_google（与「数据分析」一致，推荐）| gemini | openai | ...
    chat_provider: str = "vertex_google"
    # 默认聊天模型；Vertex 下为裸模型名或 vertex_google/gemini-3.5-flash-lite
    default_chat_model: str = "vertex_google/gemini-3.5-flash-lite"
    # Google Cloud Vertex AI（gcloud token，非 AI Studio 免费 key）
    gcp_project: str = "project-c5cbd608-1b00-454e-80f"
    gcp_location: str = "us-central1"
    gcp_sdk_root: str = ""  # 留空；Vertex 集成需要时通过 NOVELMIND_GCP_SDK_ROOT 配置
    gcp_sdk_py: str = ""  # 留空；Vertex 集成需要时通过 NOVELMIND_GCP_SDK_PY 配置
    vertex_model: str = "gemini-3.5-flash-lite"
    # 访问 Vertex/Google API 的出站代理（国内环境常需；留空则读 HTTPS_PROXY 环境变量）
    https_proxy: str = ""
    ollama_base_url: str = "http://localhost:11434"  # 本地 Ollama 服务地址

    # ── 插图生成（Phase 33-02 provider seam）──
    illustration_provider: str = "mock"  # mock | hunyuan；默认 mock 保证不破坏现有测试
    illustration_model: str = "hunyuan-image"  # 腾讯混元生图模型（ZCodeProxy 代理）
    illustration_base_url: str = "http://127.0.0.1:3001"  # ZCodeProxy 本地代理
    illustration_timeout: float = 120.0  # 生图 10-60s，比文本默认放宽

    # ── 文件存储 ──
    upload_dir: str = "./uploads"  # 小说上传文件存储目录
    # 插图/衍生资产等「生成物」存储根目录。留空时服务回退到 CWD 下的 "storage"
    # （保持现有行为）；打包桌面模式通过 NOVELMIND_STORAGE_DIR 重定向到
    # %APPDATA%/NovelMind/data/storage，避免生成物写入安装目录。
    storage_dir: str = ""
    max_upload_size: int = 50 * 1024 * 1024  # 最大上传大小: 50MB
    streaming_threshold: int = (
        5 * 1024 * 1024
    )  # 流式读取阈值: 5MB（超过此大小使用分块读取）

    # ── Embedding 配置 ──
    # 与「数据分析」对齐：默认本地 sentence-transformers + bge-small-zh-v1.5（512 维）
    # provider:
    #   local_st — 本机 ST 模型（推荐，不依赖 Ollama）
    #   ollama   — HTTP 调 Ollama /api/embed
    #   openai   — LiteLLM aembedding
    embedding_provider: str = "local_st"
    embedding_model: str = "bge-small-zh-v1.5"
    embedding_model_path: str = r"D:\models\bge-small-zh-v1.5"
    embedding_dimensions: int = 512
    embedding_device: str = "cpu"  # cuda / cpu；无 CUDA torch 时自动回退 cpu
    embedding_batch_size: int = 64

    # ── 认证与敏感数据保护 ──
    secret_key: str = DEV_JWT_SECRET
    encryption_key: str = DEV_ENCRYPTION_KEY
    previous_encryption_keys: str = ""
    access_token_expire_minutes: int = 60 * 24 * 7  # Token 有效期: 7 天
    auth_cookie_secure: bool = False

    # 自定义 AI API 地址只允许访问管理员明确配置的主机。
    # 本地 Ollama 需显式加入，例如: localhost,127.0.0.1
    ai_allowed_hosts: str = "api.openai.com,api.anthropic.com"
    ai_allowed_private_hosts: str = ""

    # Phase 15: offline hierarchical retrieval experiment (default OFF).
    # Never enables a production Reader Chat consumer or active pointer path.
    narrative_memory_retrieval_experiment_enabled: bool = False

    # ── 智能体网关（25.2-02 / D-15）──
    # 服务到服务共享令牌：agent-service 调 POST /api/gateway/v1/chat/completions
    # 时须携带 `Authorization: Bearer <NOVELMIND_GATEWAY_TOKEN>`。为空时网关
    # fail-closed（401）。令牌绝不写日志、绝不下发浏览器（V6/T-25.2-02-04）。
    # 注意：字段名保留 `novelmind_gateway_token`（security.py 引用），但显式
    # 绑定环境变量 `NOVELMIND_GATEWAY_TOKEN`——否则 prefix 会让它变成
    # `NOVELMIND_NOVELMIND_GATEWAY_TOKEN` 而永远读不到。
    novelmind_gateway_token: str = Field(
        default="", validation_alias="NOVELMIND_GATEWAY_TOKEN"
    )

    # ── 引导管理员（bootstrap admin）──
    # 首次部署时，公开注册端点在 debug=False（生产）模式下默认 fail-closed，
    # 防止第一个外部注册者抢占 superuser。运维必须在部署前：
    #   1) 通过环境变量 NOVELMIND_BOOTSTRAP_ADMIN_TOKEN 提供一次性 token，或
    #   2) 通过离线脚本预置初始管理员。
    # 首个携带匹配 bootstrap_token 注册的用户成为 superuser；此后该 token
    # 不再生效（bootstrap 关闭）。debug=True（本地开发）时未配置 token 仍允许
    # 首个注册成为 admin，以保持开发便利。
    bootstrap_admin_token: str = Field(
        default="", validation_alias="NOVELMIND_BOOTSTRAP_ADMIN_TOKEN"
    )

    # ── 桌面本地会话认证（Phase 44-02 / D-44-04 / T-44-02-02）──
    # Electron main 进程为本地 FastAPI 铸造 audience/expiry 绑定的短命会话令牌
    # （iss=novelmind-desktop-main, aud=novelmind-desktop-local），HMAC 密钥通过
    # 环境变量 NOVELMIND_LOCAL_AUTH_SECRET 注入到受管进程环境；未配置/无效 →
    # desktop_local_auth 中间件 fail-closed（401），绝不降级放行。
    # 浏览器开发模式仍走现有 JWT/cookie 认证：本配置只接受显式字符串，
    # 留空即「本地会话认证不可用」（永远不是隐式绕过）。
    local_auth_secret: str = Field(
        default="", validation_alias="NOVELMIND_LOCAL_AUTH_SECRET"
    )

    # pydantic-settings 配置：从 .env 文件加载，环境变量前缀为 NOVELMIND_
    model_config = {"env_file": ".env", "env_prefix": "NOVELMIND_"}

    @property
    def allowed_ai_hosts(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.ai_allowed_hosts.split(",")
            if host.strip()
        }

    @property
    def allowed_private_ai_hosts(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.ai_allowed_private_hosts.split(",")
            if host.strip()
        }

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if not self.debug:
            if self.secret_key == DEV_JWT_SECRET or len(self.secret_key) < 32:
                raise ValueError(
                    "NOVELMIND_SECRET_KEY must be a unique value of at least 32 characters"
                )
            if (
                self.encryption_key == DEV_ENCRYPTION_KEY
                or len(self.encryption_key) < 32
            ):
                raise ValueError(
                    "NOVELMIND_ENCRYPTION_KEY must be a unique value of at least 32 characters"
                )
            if self.secret_key == self.encryption_key:
                raise ValueError("JWT and encryption keys must be different")
        return self


# 全局配置单例，整个应用通过 settings.xxx 访问配置
settings = Settings()
