# Development Guide

## Source Ownership

| Area | Path |
|---|---|
| API routers | `backend/app/api/` |
| Services | `backend/app/services/` |
| ORM models | `backend/app/models/` |
| Migrations | `backend/migrations/versions/` |
| Frontend routes | `frontend/src/app/` |
| Frontend components | `frontend/src/components/` |
| Human documentation | `README.md`, `IMPLEMENTATION-STATUS.md`, `docs/` |
| AI planning/execution state | `.planning/` |

## Change Rules

1. 代码和可执行验证是完成状态的事实来源。
2. 持久模型变更必须附带 Alembic migration。
3. 集合响应不得携带章节正文或 provider secret。
4. 路径、URL、身份和 owner 必须在信任边界校验。
5. 文件/数据库双写必须测试失败补偿。
6. 修改共享契约后运行完整后端、前端、构建和 migration 检查。

## GSD Workflow

`.planning/` 是唯一 AI 读写状态目录（GSD 工作流）。当前 active milestone 是 v0.2 安全与架构修复；`02-01`、`02-02` 已完成，自动执行从 `02-03` 开始。

每个 task plan 必须包含 `Steps / Must-Haves / Verification`。每个 implementation slice 的最后一步必须是 `Test, Fix, and Confirm`。

## Database Changes

```powershell
cd backend
.\.venv311\Scripts\python.exe -m alembic upgrade head
.\.venv311\Scripts\python.exe -m alembic current
.\.venv311\Scripts\python.exe -m alembic check
```

当前 PostgreSQL 16 验证 head 为 `a91c4d7e5f20`，三条命令均通过。

## Definition Of Done

- 聚焦测试和受影响的完整测试通过；
- Ruff、ESLint、typecheck 和 production build 通过；
- migration 在真实 PostgreSQL 上通过；
- pip-audit、npm audit、Bandit 无未接受的高风险问题；
- `IMPLEMENTATION-STATUS.md`、`docs/` 和 `.planning/` 与结果一致。
