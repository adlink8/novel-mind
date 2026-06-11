# GSD Smoke Tests

本文件记录当前可重复验证命令，不保存过期的历史通过状态。

## Backend

```powershell
cd backend
.\.venv311\Scripts\python.exe -m pytest -q
.\.venv311\Scripts\python.exe -m ruff check app tests
.\.venv311\Scripts\python.exe -m bandit -r app -lll -q
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'
.\.venv311\Scripts\python.exe -m pip_audit
```

期望：70 passed；静态和依赖安全检查为 0。

## Migration

```powershell
docker compose up -d db
cd backend
.\.venv311\Scripts\python.exe -m alembic upgrade head
.\.venv311\Scripts\python.exe -m alembic current
.\.venv311\Scripts\python.exe -m alembic check
```

期望：head `a91c4d7e5f20`，`No new upgrade operations detected.`

## Frontend

```powershell
cd frontend
npm test
npm run lint
npm run build
npm audit --registry=https://registry.npmjs.org
```

期望：22 passed；ESLint 0；构建包含 `/novels/[id]`；audit 0。

## Runtime

1. 注册并登录，确认浏览器通过 HttpOnly Cookie 保持会话，Cookie 写请求只接受允许的 Origin。
2. 用户 A 上传小说并创建模型配置。
3. 用户 B 的列表不出现用户 A 数据，直接访问资源 ID 返回 404。
4. 数据库写入失败时不留下上传文件；删除提交失败时文件恢复。
5. 未列入白名单或解析到私网的模型地址返回 400。
