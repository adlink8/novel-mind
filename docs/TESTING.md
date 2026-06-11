# Testing Guide

验证环境：Python 3.11-3.13、Node.js 20.9+、PostgreSQL 16。

## Backend

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests migrations
.\.venv\Scripts\python.exe -m bandit -r app -ll -q
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
```

2026-06-11 结果：68 tests passed；Ruff 0；Bandit 中高风险 0；pip-audit 0。

测试覆盖健康检查、认证、匿名拒绝、跨用户小说/模型隔离、上传编码、路径约束、SSR F、加密兼容和文件事务回滚。SQLite 测试不能替代 PostgreSQL migration 验证。

## Frontend

```powershell
cd frontend
npm test
npm run lint
npm run build
npm audit --registry=https://registry.npmjs.org
```

2026-06-11 结果：22 tests passed；ESLint 0；Next 16 Turbopack build passed；npm audit 0。

## PostgreSQL Migration

```powershell
docker compose up -d db
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

当前 head：`a91c4d7e5f20`。真实 PostgreSQL 16 验证通过。

## Remaining Test Gap

持久化导入 job 尚未实现，因此并发 worker、租约超时、重试和重启恢复测试仍缺失。
