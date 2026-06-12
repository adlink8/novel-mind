# backend/app/core — 基础设施层

跨业务复用的底层设施，无业务逻辑依赖，被上层 service/api 共同引用。

## 模块

| 文件 | 职责 |
|---|---|
| `database.py` | SQLAlchemy async 引擎创建、异步会话工厂、FastAPI `get_db` 依赖注入 |
| `security.py` | JWT 签发与验证、bcrypt 密码哈希（拒绝 >72 字节超长密码）、Bearer token 提取、HttpOnly Cookie 设置/清除 |
| `crypto.py` | Fernet 版本化对称加密（`enc:v1` 前缀），支持 key rotation（previous_key）与旧明文兼容迁移 |
| `url_security.py` | SSRF 防护 — 协议过滤、凭据检测、DNS 解析、IPv4/IPv6 内网地址校验，管理员白名单 + 私网显式配置 |
| `logging.py` | 请求日志中间件、全局 loguru 格式配置（含数据库 URL 脱敏） |

## 依赖关系

```
api/ ──────────────────────┐
services/ ─────────────────┤
models/ ───────────────────┼── 引用 core/
                            │
core/ (无上层依赖)         │
├── database.py            │  ← services / api 共用 get_db
├── security.py            │  ← api/dependencies 用 JWT 验证
├── crypto.py              │  ← services/ai_service 用密钥加密
├── url_security.py        │  ← services/novel_service 用 URL 校验
└── logging.py             │  ← main.py 注册为中间件
```

## 注意事项

- 数据库 host 必须用 `127.0.0.1`，不能用 `localhost`（Windows + asyncpg + Docker IPv6 陷阱）
- Fernet 密钥通过 `NOVELMIND_ENCRYPTION_KEY` 环境变量注入，生产模式强制校验长度
- SSRF 白名单通过 `NOVELMIND_SSRF_ALLOWLIST` 配置，默认可信域名列表
