# API Reference

Base path: `/api`. OpenAPI is available at `/docs` while the backend is running.

## Authentication

Protected endpoints accept either `Authorization: Bearer <token>` or the HttpOnly `novelmind_session` cookie set by login. Cookie-authenticated write requests require an `Origin` present in the server CORS allowlist. Registration and login are public; logout clears the cookie.

| Method | Path | Auth | Status |
|---|---|---|---|
| POST | `/api/auth/register` | none | VERIFIED |
| POST | `/api/auth/login` | none | VERIFIED |
| POST | `/api/auth/logout` | none | VERIFIED |
| GET | `/api/auth/me` | required | VERIFIED |

The first active account becomes the bootstrap administrator and claims legacy unowned data. Usernames and emails are normalized. Passwords require at least eight characters and are stored as bcrypt hashes.

## Health

| Method | Path | Auth | Status |
|---|---|---|---|
| GET | `/api/health` | none | VERIFIED |

## Novels

| Method | Path | Auth | Status | Description |
|---|---|---|---|---|
| POST | `/api/novels/upload` | required | VERIFIED | TXT 导入，租约并发 + SHA-256 幂等 + 取消支持 |
| GET | `/api/novels` | required | VERIFIED | 所有者隔离的分页列表 |
| GET | `/api/novels/{id}` | required | VERIFIED | 小说元数据 + 章节摘要（无正文） |
| GET | `/api/novels/{id}/chapters` | required | VERIFIED | 章节摘要列表（无正文） |
| GET | `/api/novels/{id}/chapters/{ch_id}` | required | VERIFIED | 单个章节含完整正文 |
| PATCH | `/api/novels/{id}/progress` | required | VERIFIED | 更新阅读进度 |
| DELETE | `/api/novels/{id}` | required | VERIFIED | 删除小说（文件 + DB 补偿） |
| GET | `/api/novels/{id}/import-status` | required | VERIFIED | 轮询导入任务状态 |
| POST | `/api/novels/{id}/import-retry` | required | VERIFIED | 重试失败导入 |
| POST | `/api/novels/{id}/import-cancel` | required | VERIFIED | 取消运行中的导入 |
| POST | `/api/novels/{id}/search` | optional | VERIFIED | RAG 语义搜索 |
| POST | `/api/novels/{id}/index` | required | VERIFIED | 触发 RAG 索引 |
| GET | `/api/novels/{id}/index-status` | required | VERIFIED | 查询索引进度 |

## AI Models

| Method | Path | Auth | Status |
|---|---|---|---|
| GET/POST | `/api/models` | required | VERIFIED |
| PUT/DELETE | `/api/models/{id}` | required | VERIFIED |
| POST | `/api/models/{id}/default` | required | VERIFIED |
| POST | `/api/models/{id}/test` | required | VERIFIED |

Model configuration is owner-scoped. API keys are stored as versioned Fernet ciphertext and are never returned by list/detail responses. Custom base URLs require a server-side exact-host allowlist and pass scheme, credential, DNS, IPv4 and IPv6 checks before storage and again before provider access.

## Analysis and generation domains

Authenticated analysis, timeline, relationships and clues routes exist beyond pure placeholders; product completeness varies by phase (see `IMPLEMENTATION-STATUS.md`). Fanfiction create/continue still return HTTP 501. Empty graph/query state may still appear when KG/observations are missing or when spoiler filters hide data.

## Agent 工具门面（`/api/agent-tools/*`）

供 agent-service 调用的域工具门面（只读工具 + 候选 action 工具）。认证走 `require_agent_actor`：接受端用户 JWT 或 **per-run 内部令牌**（`Authorization: Bearer <token>`，按 `sha256(token)` 匹配 `skill_runs.internal_token_hash`，且 `novel_id` 一致、run 状态为 queued/running）。`novel_id` 走查询参数注入 `require_owned_novel`（owner 校验 + 404-hide），请求体只携带各工具的类型化参数（StrictPydantic `extra="forbid"`，未知字段直接 422）。

所有错误统一为信封 `{"error": {"code", "message"}}`，错误码为冻结契约（`backend/app/services/agent_tools/errors.py`，不得改名）：

| Code | HTTP | 含义 |
|---|---|---|
| `forbidden` | 403 | 无权限访问目标资源（当前走 404-hide 约定，通常不直接出现） |
| `not_found` | 404 | 资源不存在（章节不属于该小说等） |
| `beyond_cutoff` | 422 | 请求范围超出当前阅读进度截止点（防剧透） |
| `budget_exceeded` | 429 | 预算策略在调用前拦截（fail closed） |
| `timeout` | 504 | 上游执行超过 per-tool 超时 |
| `output_too_large` | 413 | 序列化响应超过 per-tool 字节上限 |
| `invalid_input` | 422 | 请求参数校验失败 |
| `upstream_error` | 502 | 未预期/未分类的上游错误 |

请求校验失败（FastAPI 422）在 `/api/agent-tools` 与 `/api/gateway` 路径上包装为 `{"error": {"code": "invalid_input", "message": ...}}`，`message` 携带字段级明细（`loc: msg`，最多前 5 条），供模型修正参数而非盲目重试。

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/agent-tools/search_novel_text` | JWT 或 per-run 内部令牌 | 小说内全文检索：`query`（1–500 字符）、`top_k`（1–50，默认 10）、`mode`（`auto`/`chunks`/`units`/`hybrid`）。cutoff 过滤语义：先过取 `min(top_k*4, 50)` 条，再按阅读 cutoff 过滤超截止章节的命中，最后截断回 `top_k`；持久化每本小说开关 `timeline_full_book` 为例外（开启时不过滤） |
| POST | `/api/agent-tools/get_evidence_span` | JWT 或 per-run 内部令牌 | leaf 证据跨度物化：`chapter_id` 必填；`chunk_id` 可选通道——携带 search_novel_text 命中行的 `chunk_id` 时，服务端在章节原文中定位 chunk 内容并确定性推导 offsets（含空白规范化回退），`source_start`/`source_end` 可省略；未携带 `chunk_id` 时 offsets 必填。`content_hash` 可选：省略时服务端计算并返回，提供时校验与切片一致（不匹配即拒绝，防漂移） |

## Agent 运行（service 端点与模型网关）

agent-service poller 端点使用 **gateway token** 认证（`Authorization: Bearer` 与共享环境令牌常量时间比较，fail-closed 401），无用户 JWT；finalize/cancel 走 `require_agent_actor`（用户 JWT 或 per-run 内部令牌）。

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/agent/queued-runs` | gateway token | 列出 queued 的 Pi-backed 运行（origin ∈ `chat_backfill`/`reader_chat`/`chapter_batch`），返回运行上下文（input/skill_version_id/input_hash/branch 等），不返回 internal_token |
| POST | `/api/agent/queued-runs/{run_id}/claim` | gateway token | 原子 claim：queued → running（冲突者 409；running 且超过 30 分钟 lease 窗口可重新 claim 以恢复 poller crash），铸造新 per-run internal_token（库中只存 hash）并明文返回给 poller |
| POST | `/api/agent/novels/{novel_id}/skill-runs/{run_id}/finalize` | JWT 或 per-run 内部令牌 | 确定性 finalizer（agent-service 在 agent_end 时触发）：唯一写 artifact/revision 的入口，引证按冻结 manifest 白名单校验，幂等 |
| POST | `/api/agent/novels/{novel_id}/skill-runs/{run_id}/cancel` | JWT 或 per-run 内部令牌 | 请求取消：queued/running 直接转 cancelled 终态（无写入） |
| POST | `/api/gateway/v1/chat/completions` | gateway token + per-run 上下文 | OpenAI 兼容补全（`stream=true` 返回 SSE）。除 gateway token 外，逻辑模型 `reader-chat-default` 需携带 `X-NovelMind-Run-Token` 与 `X-NovelMind-Novel-ID` header：服务端按 per-run token 定位活跃 SkillRun，把模型解析为该 owner 的 task 绑定模型（按 skill 名映射 task）或默认模型；key/路由/计价全部留在服务端，不接受客户端自定义上游地址 |

## Current Verification

- Backend pytest: 70 passed on Python 3.11.15.
- Cross-user novel and model isolation tests pass.
- SSRF, legacy-key decryption and upload/delete rollback tests pass.
- RAG 管线核心功能已实现：文本分块、向量存储、索引管线、搜索 API。
- Persistent import jobs remain MISSING and are the active `02-03` work.
