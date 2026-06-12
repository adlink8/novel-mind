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
| POST | `/api/novels/upload` | required | PARTIAL | Synchronous TXT import; progress is process-local |
| GET | `/api/novels` | required | VERIFIED | Owner-scoped paginated list |
| GET | `/api/novels/{id}` | required | VERIFIED | Owner-scoped metadata and chapter summaries |
| DELETE | `/api/novels/{id}` | required | VERIFIED | Owner-scoped delete with file/DB compensation |
| GET | `/api/novels/{id}/chapters` | required | VERIFIED | Summaries only; no chapter body |
| GET | `/api/novels/{id}/chapters/{chapter_id}` | required | VERIFIED | Single chapter body |
| PATCH | `/api/novels/{id}/progress` | required | PARTIAL | Owner-isolated, but stored on Novel rather than per-user history |
| GET | `/api/novels/{id}/import-status` | required | PARTIAL | Process-local status; not restart-safe |
| POST | `/api/novels/{novel_id}/search` | Bearer (optional) | implemented | 语义搜索（RAG） |
| POST | `/api/novels/{novel_id}/index` | Bearer | implemented | 触发小说索引 |
| GET | `/api/novels/{novel_id}/index-status` | Bearer | implemented | 查询索引进度 |

Cross-owner resource access returns 404. Public responses do not include `source_path` or provider secrets.

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
