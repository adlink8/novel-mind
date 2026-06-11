# Roadmap: NovelMind

## Milestones

- [x] v0.1 审计与启动修复 - 建立可信实现基线和启动级契约。
- [ ] v0.2 安全与架构修复 - 关闭安全、迁移、路由、依赖和导入可靠性阻断项。
- [ ] v0.3 小说导入 + RAG 索引 - 在 v0.2 门槛通过后激活。

第一个 active milestone 已按要求设为并完成“审计与启动修复”。当前 active milestone 为 v0.2。

## v0.2 Plans

- [x] 02-01: 仓库与上传边界修复
- [x] 02-02: 认证、SSRF 与密钥保护
- [ ] 02-03: 持久化导入任务与里程碑收尾

## v0.2 Success Criteria

1. Git/secret、上传路径、未授权访问、SSRF 和密钥存储测试通过。 VERIFIED
2. PostgreSQL Alembic upgrade/current/check 通过。 VERIFIED
3. Next.js 输出 `/novels/[id]`，集合响应不包含正文或 `source_path`。 VERIFIED
4. backend/frontend tests、build、lint、CI 和依赖审计通过。 VERIFIED
5. 导入任务有持久 job、并发安全状态机、幂等重试和重启恢复。 MISSING

## Progress

| Milestone | Plans Complete | Status |
|---|---:|---|
| v0.1 审计与启动修复 | 3/3 | Complete |
| v0.2 安全与架构修复 | 2/3 | Active |
| v0.3 小说导入 + RAG 索引 | 0/TBD | Backlog |

## Auto Start

`/gsd auto` must start from:

`.gsd/phases/02-security-and-architecture-remediation/02-03-PLAN.md`
