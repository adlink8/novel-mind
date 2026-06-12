# Requirements: 安全与架构修复

## Requirements Status

| ID | Requirement | Priority | Status |
|---|---|---|---|
| REQ-AUDIT-01 | 以 VERIFIED / PARTIAL / MISSING 建立实际实现基线 | P0 | VERIFIED |
| REQ-AUDIT-02 | 修复启动级前后端契约 | P0 | VERIFIED |
| REQ-AUDIT-03 | 未实现核心端点不得伪装成功 | P0 | VERIFIED |
| REQ-AUDIT-04 | 建立可重复 smoke/test/build 命令 | P0 | VERIFIED |
| REQ-AUDIT-05 | 文档状态与代码现实一致 | P0 | VERIFIED |
| REQ-SEC-01 | Git 忽略敏感配置、依赖、构建和上传数据 | P0 | VERIFIED |
| REQ-SEC-02 | 上传文件限制在根目录并具备失败补偿 | P0 | VERIFIED |
| REQ-SEC-03 | API 建立身份和资源授权边界 | P0 | VERIFIED |
| REQ-SEC-04 | 自定义模型 URL 阻断 SSRF，错误对外脱敏 | P0 | VERIFIED |
| REQ-SEC-05 | provider key 加密存储并支持旧密钥轮换 | P1 | VERIFIED |
| REQ-DATA-01 | ORM/Alembic 对齐并在 PostgreSQL 执行检查 | P0 | VERIFIED |
| REQ-ARCH-01 | reader 动态路由和章节响应模型正确 | P1 | VERIFIED |
| REQ-ARCH-02 | 导入进度使用持久化、并发安全的 job 模型 | P1 | MISSING |
| REQ-CI-01 | CI 覆盖默认分支，检查非交互且依赖风险受控 | P1 | VERIFIED |

## Traceability

| Requirement | Plan | Evidence |
|---|---|---|
| REQ-SEC-01, REQ-SEC-02 | 02-01 | `.gitignore` checks; upload/delete rollback tests |
| REQ-SEC-03..05 | 02-02 | auth/ownership/SSRF/crypto tests |
| REQ-DATA-01, REQ-ARCH-01, REQ-CI-01 | 02-03 completed slices | PostgreSQL migration, Next build, CI and audits |
| REQ-ARCH-02 | 02-03 active slice | No import job table or worker exists |

## Current Evidence

- 70 backend tests and 22 frontend tests pass.
- pip-audit and npm audit report zero known vulnerabilities.
- Bandit medium/high, Ruff and ESLint report zero findings.
- PostgreSQL `upgrade/current/check` passes at `a91c4d7e5f20`.
- Import progress is still stored in `NovelService._import_status`; therefore REQ-ARCH-02 remains MISSING.
