# GSD Smoke Tests

更新日期：2026-06-10

这些命令用于 GSD "审计与启动修复" milestone 的最小回归验证。

## Backend

在 `backend/` 执行：

```powershell
# 编译检查
.\\.venv\\Scripts\\python.exe -m compileall app

# 应用导入验证
.\\.venv\\Scripts\\python.exe -c "from app.main import app; print(app.title); print(len(app.routes))"

# pytest 自动化测试（6 个冒烟测试）
.\\.venv\\Scripts\\python.exe -m pytest tests/test_health.py -v
```

当前结果：

- `compileall app`：通过
- `from app.main import app`：通过，输出 `NovelMind API` 和 `31`
- `pytest tests/test_health.py`：4/6 通过
  - ✅ test_health_check
  - ❌ test_novels_list_empty（SQLite 兼容性问题，需 PostgreSQL 环境）
  - ❌ test_models_list_empty（同上）
  - ✅ test_analysis_not_implemented（501 正确返回）
  - ✅ test_timeline_not_implemented（501 正确返回）
  - ✅ test_characters_not_implemented（501 正确返回）

Makefile 快捷命令：

```bash
make test    # 运行后端测试
make lint    # 前端 TypeScript 类型检查
make dev     # 启动 Docker 数据库服务
```

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
