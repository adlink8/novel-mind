# NovelMind Architecture

本文描述 2026-06-11 的实际代码架构。目标架构见 `技术架构.md`，其中未落地的 RAG、图谱和生成模块不能视为已实现。

## Runtime Topology

```text
Browser
  -> Next.js 16 App Router
  -> Axios with HttpOnly cookie credentials
  -> FastAPI authentication and owner checks
  -> service layer
  -> SQLAlchemy async
  -> PostgreSQL 16
```

Docker Compose 还提供 Chroma 和可选 Neo4j，但应用代码尚未形成对应业务链路。

## Implemented Components

| Layer | Current implementation |
|---|---|
| Frontend | Next.js App Router、认证门禁、小说列表/上传/阅读器、AI 设置骨架 |
| API | FastAPI auth、novels、models，以及受认证保护的占位业务路由 |
| Security | JWT Bearer + HttpOnly Cookie、owner 隔离、SSRF allowlist/DNS/IP 校验、Fernet keyring |
| Domain services | TXT 编码检测、清洗、分章、同步导入、LiteLLM 包装 |
| Persistence | 12 个 SQLAlchemy ORM 表、PostgreSQL、Alembic migrations |
| Tests | pytest + SQLite、真实 PostgreSQL migration smoke、Vitest + jsdom |

## Trust Boundaries

1. 除 health、register、login、logout 外，API 默认要求认证；Cookie 写请求还校验 Origin。
2. 小说和模型配置按 owner 查询；跨用户资源统一返回 404。
3. provider key 使用独立加密密钥，支持旧密钥和旧明文兼容读取。
4. 自定义 provider host 必须由服务端精确放行；私网地址需要独立显式白名单。
5. 上传使用随机存储名和根目录 containment，文件与数据库失败执行补偿。

## Current Request Flows

### Novel Import

`multipart upload -> bounded streaming write -> encoding detection -> chapter parsing -> database transaction -> response`

该流程仍为同步执行。进度保存在进程内字典中，服务重启后丢失，也没有 worker、租约、幂等重试或取消机制。这是当前 active plan `02-03` 的唯一主要架构缺口。

### Reader

- 小说详情只返回元数据和章节摘要。
- 章节列表不返回正文。
- 单章接口是正文的常规读取入口。
- 阅读进度受 owner 隔离，但仍直接存于 Novel，不支持多设备历史。

### AI Provider Access

模型配置按 owner 存储。API key 写入时生成 `enc:v1` 密文；读取兼容旧 Fernet 密文和旧明文。自定义 URL 在配置时和调用前重复校验，provider 错误对外脱敏。

## Data Consistency Rules

- Alembic 是 schema 权威；测试中的 `create_all` 不能替代 migration 验证。
- 文件和数据库双写必须有失败补偿。
- 持久化 import job 应使用唯一 ID、状态机、幂等键、租约和可恢复重试。
- RAG 引入多个存储前必须定义唯一权威源和 reconciliation 机制。

## Remaining Architecture Debt

- 持久化 import job/worker：MISSING。
- reading progress 用户历史模型：PARTIAL。
- RAG、索引、检索和引用链路：MISSING。
- Chroma/Neo4j 业务集成：MISSING。
- 生产 TLS、秘密管理、限流、备份和监控：MISSING/PARTIAL。

当前安全、迁移、路由和依赖门槛已经验证；完成 `02-03` 后才能激活 RAG milestone。
