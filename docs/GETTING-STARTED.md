# Getting Started

## Prerequisites

- Python 3.11-3.13。LiteLLM 安全版本不支持 Python 3.14。
- Node.js 20.9+
- Docker Desktop with Compose

## Start Data Services

```powershell
docker compose up -d db chroma
docker compose ps
```

## Start Backend

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8000/api/health
```

## Start Frontend

```powershell
cd frontend
npm install
npm run dev
```

打开 Next.js 输出的地址。首次注册的活跃账户成为引导管理员，并接管迁移前的历史数据。

## Verify

完整命令见 `docs/TESTING.md`。最低提交门槛：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check

cd ..\frontend
npm test
npm run lint
npm run build
```

生产部署前必须设置不同的 JWT/加密密钥，并将 `NOVELMIND_AUTH_COOKIE_SECURE` 设为 `true`。
