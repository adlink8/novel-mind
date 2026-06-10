# GSD Smoke Tests

更新日期：2026-06-06

这些命令用于 GSD “审计与启动修复” milestone 的最小回归验证。

## Backend

在 `backend/` 执行：

```powershell
.\.venv\Scripts\python.exe -m compileall app
.\.venv\Scripts\python.exe -c "from app.main import app; print(app.title); print(len(app.routes))"
```

当前结果：

- `compileall app`：通过
- `from app.main import app`：通过，输出 `NovelMind API` 和 `31`
- Docker Compose `db`：运行中，PostgreSQL healthy
- 临时后端 `127.0.0.1:8003`：health 通过
- 上传 `test_novel.txt`：通过，返回 `uploaded_id=4`、`chapter_count=3`
- 小说列表：通过，返回 `list_total=2`
- 章节查询：通过，返回 `fetched_chapters=3`

注意：系统全局 Python 3.14 当前未安装 `sqlalchemy`，直接运行 `python -c "from app.main import app"` 会失败。请使用 `backend/.venv/Scripts/python.exe`。

注意：本机 PowerShell `Invoke-RestMethod` 对 localhost 请求可能受到代理影响并返回 502。运行 HTTP smoke 时使用：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8003/api/health
```

临时后端启动命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

## Frontend

在 `frontend/` 执行：

```powershell
npx tsc --noEmit
npm run build
```

当前结果：

- `npx tsc --noEmit`：通过
- `npm run build`：通过，Next.js 生产构建成功

## Contract Checks Covered

- 小说列表前端读取后端分页响应 `items`。
- AI 模型新增表单提交后端必需字段 `name` 和 `model_id`。
- AI 模型测试 UI 使用后端 `success` / `error` 结果。
- 未实现的生成/抽取端点返回 HTTP 501，不再伪装成功。
