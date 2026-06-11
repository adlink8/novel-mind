# NovelMind

NovelMind 是一个 AI 辅助小说理解与同人创作平台。当前已具备安全的账户体系、小说 TXT 导入与阅读、用户级模型配置，以及可重复执行的测试和迁移基线；RAG、剧情分析、人物图谱、时间线和同人文生成仍在后续计划中。

实际实现状态以 [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md) 为准。

## Current Baseline

- 后端：FastAPI、SQLAlchemy async、PostgreSQL 16 + pgvector
- 前端：Next.js 16.3.0-canary.6、React 19、TypeScript、Tailwind CSS
- AI：LiteLLM 1.83.10+；项目支持 Python 3.11-3.13，不支持 Python 3.14
- 安全：HttpOnly Cookie/Bearer JWT、资源所有权隔离、版本化 Fernet 加密、出站主机白名单与 DNS/IP 校验
- 验证：后端 68 tests、前端 22 tests、生产构建、ESLint、Ruff、Bandit、pip-audit、npm audit、Alembic PostgreSQL 检查均通过
- GSD：`.gsd/` 是唯一 AI 状态目录；下一执行入口为 `02-03`

## Repository Layout

```text
novel-mind/
├── backend/              FastAPI、ORM、迁移和测试
├── frontend/             Next.js 应用和前端测试
├── docs/                 面向维护者的工程与产品文档
├── .gsd/                 AI 规划、状态和任务文档
├── docker-compose.yml    PostgreSQL 和 Chroma 开发服务
└── IMPLEMENTATION-STATUS.md
```

## Local Development

前置要求：Python 3.11-3.13、Node.js 20.9+、Docker Desktop。

```powershell
docker compose up -d db chroma

cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload

cd ..\frontend
npm install
npm run dev
```

- 前端：`http://localhost:3000`
- 后端：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`

首次注册的活跃账户成为引导管理员，并接管迁移前的历史小说和模型记录。生产环境必须替换 `.env.example` 中的 JWT 与数据加密密钥，并启用 Secure Cookie。

## Verification

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests migrations
.\.venv\Scripts\python.exe -m bandit -r app -ll -q
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check

cd ..\frontend
npm test
npm run lint
npm run build
npm audit --registry=https://registry.npmjs.org
```

文档入口：[docs/README.md](docs/README.md)。

## License

MIT
