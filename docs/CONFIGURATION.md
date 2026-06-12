# Configuration

后端从 `NOVELMIND_*` 环境变量和 `backend/.env` 加载配置。以 `.env.example` 为模板，真实密钥不得提交。

## Required Production Settings

| Variable | Purpose |
|---|---|
| `NOVELMIND_DEBUG=false` | 启用生产密钥强校验 |
| `NOVELMIND_DATABASE_URL` | PostgreSQL async URL（Windows 必须使用 `127.0.0.1` 而非 `localhost`） |
| `NOVELMIND_SECRET_KEY` | JWT 签名密钥，至少 32 字符 |
| `NOVELMIND_ENCRYPTION_KEY` | API Key 数据加密密钥，至少 32 字符且不得与 JWT 密钥相同 |
| `NOVELMIND_AUTH_COOKIE_SECURE=true` | 仅通过 HTTPS 发送会话 Cookie |

应用在非 debug 模式下会拒绝默认、过短或复用的 JWT/加密密钥。

## Key Rotation

1. 将旧数据密钥加入逗号分隔的 `NOVELMIND_PREVIOUS_ENCRYPTION_KEYS`。
2. 把新主密钥写入 `NOVELMIND_ENCRYPTION_KEY`。
3. 重启并验证模型连接；新写入值使用 `enc:v1:` 与新主密钥。
4. 完成数据重写后再移除旧密钥。

历史无前缀明文和旧版基于 JWT 密钥的 Fernet 值可以读取；更新记录时会写为新格式。

## Outbound AI Hosts

`NOVELMIND_AI_ALLOWED_HOSTS` 是逗号分隔的精确公网主机白名单，默认仅官方 OpenAI/Anthropic 主机。公网地址必须使用 HTTPS。

本地 Ollama 必须由管理员显式配置：

```env
NOVELMIND_AI_ALLOWED_HOSTS=api.openai.com,api.anthropic.com,localhost
NOVELMIND_AI_ALLOWED_PRIVATE_HOSTS=localhost
```

所有自定义地址会校验 URL 凭据、DNS 结果及 IPv4/IPv6 地址类别。不要把用户可控域名加入白名单。

## Development Services

Docker Compose 的数据库凭据和宿主机端口仅用于本地开发。生产环境应使用专用 secrets、TLS、私有网络、备份和访问策略。
