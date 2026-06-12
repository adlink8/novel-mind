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

结果（2026-06-12）：187 tests passed；Ruff 0；Bandit 0 High/Medium；pip-audit chromadb+pipe（非阻断）。

测试覆盖健康检查、认证、匿名拒绝、跨用户小说/模型隔离、上传编码、路径约束、SSRF、加密兼容、文件事务回滚、语义分块、向量存储、RAG 端到端。SQLite 测试不能替代 PostgreSQL migration 验证。

## Frontend

```powershell
cd frontend
npm test
npm run lint
npm run build
npm audit --registry=https://registry.npmjs.org
```

结果（2026-06-12）：22 tests passed；ESLint 0；Next 16 Turbopack build passed；npm audit 0。

## PostgreSQL Migration

```powershell
docker compose up -d db
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

当前 head：`f3c8b7b2dbf7`。真实 PostgreSQL 16 验证通过。

## Smoke Test Checklist

手动验收流程，每次发布前执行：

1. 注册并登录，确认浏览器通过 HttpOnly Cookie 保持会话。
2. Cookie 写请求只接受允许的 Origin。
3. 用户 A 上传小说并创建模型配置。
4. 用户 B 的列表不出现用户 A 数据，直接访问资源 ID 返回 404。
5. 数据库写入失败时不留下上传文件；删除提交失败时文件恢复。
6. 未列入白名单或解析到私网的模型地址返回 400。
7. 导入《龙族Ⅰ·火之晨曦》（539KB GB18030），验证 11 章 / 274,011 字。
8. 触发 RAG 索引，执行语义搜索，验证结果相关性。

## Remaining Test Gap

持久化导入 job 尚未实现，因此并发 worker、租约超时、重试和重启恢复测试仍缺失。
