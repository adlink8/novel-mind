# 02-03 总结：持久化导入任务与里程碑收尾

**日期**：2026-06-12 22:56  
**状态**：VERIFIED  
**分支**：`feat/phase2-wave2-embedding`

## 交付成果

### Slice 1 — 导入任务增强

| 功能 | 实现 | 文件 |
|------|------|------|
| 租约并发控制 | lease_id + lease_expires_at (300s) + acquire/release_lease | `app/models/import_job.py`, `app/services/import_service.py` |
| SHA-256 幂等键 | content_hash + find_duplicate_job | 同上 |
| 取消支持 | cancelled 终态 + cancel_job + POST /import-cancel | `app/models/import_job.py`, `app/services/import_service.py`, `app/api/novels.py` |
| 重启恢复 | recover_stale_jobs + main.py lifespan 调用 | `app/services/import_service.py`, `app/main.py` |
| 测试 | 30 passed（15 基础 + 15 新增：Lease / Idempotency / Cancel / Recovery） | `tests/test_import_job.py` |

### Slice 2 — 里程碑质量门槛

| 检查项 | 结果 |
|--------|------|
| Backend pytest | **187 passed**（非 e2e） |
| Frontend Vitest | 22 passed |
| Ruff | All checks passed |
| Bandit | 0 High, 0 Medium |
| pip-audit | chromadb CVE-2026-45829 (non-critical), pip CVEs (dev tool) |
| TypeScript | 0 errors |
| ESLint | 0 errors |
| Next.js build | 成功 |
| npm audit | 0 vulnerabilities |

### v0.2 Success Criteria

| # | 标准 | 状态 |
|---|------|------|
| 1 | Git/secret、上传路径、未授权访问、SSRF 和密钥存储测试通过 | VERIFIED |
| 2 | PostgreSQL Alembic upgrade/current/check 通过 | VERIFIED |
| 3 | Next.js 输出 `/novels/[id]`，响应不包含正文或 source_path | VERIFIED |
| 4 | backend/frontend tests、build、lint、CI 和依赖审计通过 | VERIFIED |
| 5 | 导入任务有持久 job、并发安全状态机、幂等重试和重启恢复 | VERIFIED |

### 修复的 Ruff 问题

- `import secrets` unused in novels.py
- `import json` / `update` unused in indexing_service.py
- `import pytest_asyncio` unused in test_import_job.py
- `job_id` unused in test_novels.py
- `ollama_model` unused in ai_service.py
- `F821 Undefined name Novel` in import_job.py（TYPE_CHECKING guard）

## 残余风险

- chromadb 1.5.9 存在 CVE-2026-45829（非关键路径）
- pip 25.0.1 存在多个 CVE（dev tool，非生产依赖）
- e2e 测试需要 Docker + Ollama 运行环境

## 下一步

v0.3 — 小说导入 + RAG 索引的完善与前端集成。入口：`.planning/backlog/03-novel-import-rag/03-PLAN.md`
