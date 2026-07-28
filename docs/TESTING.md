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

结果（2026-06-13）：236 tests passed；Ruff 0；Bandit 0 High/Medium；pip-audit 0。已知的 `chromadb` 非关键风险单独跟踪，不阻断当前基线。

测试覆盖健康检查、认证、匿名拒绝、跨用户小说/模型隔离、上传编码、路径约束、SSRF、加密兼容、文件事务回滚、持久化导入任务、语义分块、向量存储、混合检索、RAG 评测和端到端流程。SQLite 测试不能替代 PostgreSQL migration 验证。

## Frontend

```powershell
cd frontend
npm test
npm run lint
npm run build
npm audit --registry=https://registry.npmjs.org
```

结果（2026-06-13）：22 tests passed；ESLint 0；Next 16 Turbopack build passed；npm audit 0。当前自动化测试只覆盖 API 客户端和工具函数，页面与组件依赖构建检查和浏览器验收。

## PostgreSQL Migration

```powershell
docker compose up -d db
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

当前 head：`518675fa18f8`。真实 PostgreSQL 16 验证通过。

## Smoke Test Checklist

手动验收流程，每次发布前执行：

1. 注册并登录，确认浏览器通过 HttpOnly Cookie 保持会话。
2. Cookie 写请求只接受允许的 Origin。
3. 用户 A 上传小说并创建模型配置。
4. 用户 B 的列表不出现用户 A 数据，直接访问资源 ID 返回 404。
5. 数据库写入失败时不留下上传文件；删除提交失败时文件恢复。
6. 未列入白名单或解析到私网的模型地址返回 400。
7. 导入《龙族Ⅰ·火之晨曦》（539KB GB18030），验证 11 章 / 274,011 字。
8. 触发 RAG 索引，执行混合检索，验证关键词与语义结果及章节跳转。
9. 创建 RAG 评测数据集并运行评测，验证指标与趋势展示。
10. 在 1280px 桌面端和 390px 移动端检查工作台、书架与核心导航，无控制台错误。

## Remaining Test Gap

- 前端页面、组件、Hook 和 Zustand store 尚缺自动化测试。
- 浏览器端完整业务 E2E 尚未纳入 CI。
- 评测金标数据和 pgvector 降级路径仍需扩大覆盖。
