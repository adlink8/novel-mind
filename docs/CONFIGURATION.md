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

## Agent Service（agent-service）

agent-service 从进程环境读取配置，缺失必填项时启动即失败（fail-fast），令牌绝不写日志。

| Variable | Purpose |
|---|---|
| `FASTAPI_BASE_URL` | FastAPI NovelMind Core 地址，默认 `http://127.0.0.1:8000` |
| `PORT` | agent-service HTTP 监听端口，默认 `3100` |
| `NOVELMIND_GATEWAY_TOKEN` | 网关共享令牌（必填），必须与后端 `NOVELMIND_GATEWAY_TOKEN` 一致 |
| `NOVELMIND_LOCAL_AUTH_SECRET` | Electron 主进程注入的本地会话 HMAC 密钥；配置后所有 run 请求强制校验会话令牌 |
| `POLL_ENABLED` | queued-run 轮询开关，`0` 关闭，默认开启 |
| `POLL_INTERVAL_MS` | 轮询间隔毫秒，默认 `2000` |
| `POLL_CONCURRENCY` | 轮询并发数，默认 `3` |
| `POLL_TIMEOUT_MS` | 单轮超时毫秒，默认 `600000` |
| `NOVELMIND_MODEL_CONTEXT_WINDOW` | 声明模型上下文窗口，默认 `128000` |
| `NOVELMIND_MODEL_MAX_OUTPUT_TOKENS` | 声明模型最大输出 token 数，默认 `4096` |
| `NOVELMIND_MODEL_REASONING` | 设为 `"1"` 声明模型支持 reasoning |

三个 `NOVELMIND_MODEL_*` 声明必须与网关侧真实模型能力一致——它们驱动 pi 的截断/压缩与 reasoning 处理策略；真实模型能力由后端 `AIModelConfig` 决定。

## 模型路由（网关）

agent-service 只用逻辑 id `reader-chat-default` 调用网关 `POST /api/gateway/v1/chat/completions`，不持有任何 provider key 或路由表。真实模型由后端按 per-run token 解析：

1. 请求头 `X-NovelMind-Run-Token` + `X-NovelMind-Novel-ID` 定位到 queued/running 的 `SkillRun`，得到 owner 与 skill。
2. `TASK_BY_SKILL` 把 skill 映射到 task（如 `build-visual-bible`→`deep_analysis`、`answer-reading-question`→`qa`、`continue-derivative-story`→`continuation`）。
3. `AgentTaskModelBinding` 按 `(owner_id, task)` 绑定到 active 的 `AIModelConfig`（task 取值：`qa`/`deep_analysis`/`continuation`/`illustration`/`rag_eval`/`embedding`）。
4. 无绑定（或 skill 不在映射表）时回落到 owner 的默认模型（`is_active` 且 `is_default`）；两者都没有则返回 409。

`AIModelConfig.extra_params` 原样透传为上游 `extra_body`，用于 provider 特定参数（例：`{"thinking": {"type": "disabled"}}`）。输出契约为结构化 JSON 的分析类 skill（`analyze-chapter`、`detect-key-scenes`、`propose-world-model-candidates`、`build-visual-bible`）由网关强制 `response_format=json_object`。

## Development Services

Docker Compose 的数据库凭据和宿主机端口仅用于本地开发。生产环境应使用专用 secrets、TLS、私有网络、备份和访问策略。
