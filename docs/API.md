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

## Placeholder Domains

Analysis, timeline, characters and fanfiction routes require authentication. Query endpoints may return empty state; unimplemented generation/write endpoints return HTTP 501. They are not complete AI engines.

## Current Verification

- Backend pytest: 70 passed on Python 3.11.15.
- Cross-user novel and model isolation tests pass.
- SSRF, legacy-key decryption and upload/delete rollback tests pass.
- RAG 管线核心功能已实现：文本分块、向量存储、索引管线、搜索 API。
- Persistent import jobs remain MISSING and are the active `02-03` work.
