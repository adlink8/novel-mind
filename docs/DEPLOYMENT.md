# 部署指南

## 环境要求

| 组件 | 最低版本 | 备注 |
|---|---|---|
| Python | 3.12+ | 推荐 3.12 |
| Node.js | 20+ | 已验证 20/22/24 |
| PostgreSQL | 16+ | 需要 pgvector 扩展 |
| ChromaDB | latest | 向量数据库，可 Docker 运行 |
| AI 模型 | — | 至少一个可用模型端点 |

## 快速部署

### 1. 克隆项目

```bash
git clone <repo-url>
cd novel-mind
```

### 2. 后端配置

```bash
cd backend

python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或
.venv\Scripts\activate    # Windows

pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

```ini
NOVELMIND_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/novelmind
NOVELMIND_JWT_SECRET=<随机密钥>
NOVELMIND_STORAGE_ROOT=./data/novel_source
NOVELMIND_CHROMA_PATH=./data/chromadb

# AI 模型（至少配一个）
NOVELMIND_OPENAI_API_KEY=sk-...
NOVELMIND_OPENAI_BASE_URL=https://api.openai.com/v1
NOVELMIND_OPENAI_CHAT_MODEL=gpt-4o-mini
```

### 3. 初始化数据库

```bash
alembic upgrade head
createdb novelmind
```

### 4. 启动后端

```bash
# 开发
uvicorn app.main:app --reload --port 8010

# 生产（参考，当前未到生产阶段）
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8010
```

### 5. 前端

```bash
cd frontend
npm install
npm run build
npm start -- --port 3000
```

## 环境变量

### 后端 (.env)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `NOVELMIND_DATABASE_URL` | (必需) | PostgreSQL async 连接串 |
| `NOVELMIND_JWT_SECRET` | (必需) | JWT 签名密钥 |
| `NOVELMIND_JWT_ISSUER` | `novelmind` | JWT issuer |
| `NOVELMIND_JWT_AUDIENCE` | `novelmind-api` | JWT audience |
| `NOVELMIND_JWT_EXPIRE_MINUTES` | `1440` | Token 过期时间（分） |
| `NOVELMIND_STORAGE_ROOT` | `./data/novel_source` | 上传文件存储路径 |
| `NOVELMIND_CHROMA_PATH` | `./data/chromadb` | ChromaDB 持久化路径 |
| `NOVELMIND_CORS_ORIGINS` | `["http://localhost:3000", ...]` | 允许的前端源 |
| `NOVELMIND_SSRF_ALLOWED_HOSTS` | `["localhost", "127.0.0.1"]` | AI 请求白名单域名 |

## Docker Compose 开发环境

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: novelmind
      POSTGRES_USER: novelmind
      POSTGRES_PASSWORD: secret
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]

  chroma:
    image: chromadb/chroma:latest
    ports: ["8000:8000"]
    volumes: [chromadb:/chroma/chroma]
    environment: [IS_PERSISTENT=TRUE]
```

启动：`docker compose up -d db chroma`

Neo4j（仅关系图开发需要）：
```bash
docker compose --profile graph up -d neo4j
```

## 验证部署

```bash
# 后端健康
curl http://localhost:8010/api/health

# 注册
curl -X POST http://localhost:8010/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"password123"}'

# 上传小说
curl -X POST http://localhost:3000/api/novels/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@test.txt"

# 搜索
curl "http://localhost:3000/api/search/global?q=测试&strategy=hybrid_search" \
  -H "Authorization: Bearer <token>"
```

## 注意事项

1. **嵌入模型**：首次 `index_novel()` 需要 AI 模型可用。Ollama 建议预先拉取 `nomic-embed-text`
2. **JWT 密钥**：生产务必更换随机密钥
3. **文件存储**：`./data` 目录需足够空间（原文 + ChromaDB 持久化）
4. **数据备份**：NM 版本和 Timeline 数据在 PostgreSQL，建议定期备份
5. **CORS**：前后端不同域名时修改 `NOVELMIND_CORS_ORIGINS`
6. **SSRF 保护**：`NOVELMIND_SSRF_ALLOWED_HOSTS` 限制 AI 请求目标

## 当前状态

仓库支持**本地开发和安全基线验证**，尚不具备生产发布条件。

### 已实现的安全基线
- JWT Bearer 与 HttpOnly Cookie 会话，Cookie 写请求执行 Origin 校验
- 小说和模型配置 owner 隔离
- provider key 加密和轮换兼容
- provider URL allowlist、DNS 和 IP SSRF 防护
- 上传 containment、大小限制和文件/数据库补偿
- 生产模式拒绝弱 JWT/加密密钥
- Python/Node 依赖审计当前为 0 已知漏洞

### 生产就绪前置条件
- 应用生产镜像、TLS ingress、限流和 CSRF 策略
- 非默认数据库凭据
- 数据服务端口不映射到宿主机
- Chroma 固定版本镜像
- 备份恢复演练、监控和告警

### 生产拓扑参考

```
TLS ingress
  -> Next.js web
  -> authenticated FastAPI
  -> PostgreSQL on private network
  -> durable import worker/queue
  -> selected vector store on private network
  -> provider egress allowlist/proxy
```

## 关键脚本

| 脚本 | 位置 | 用途 |
|---|---|---|
| `start-detached.bat` | `backend/` | 后台启动后端 |
| `_nm_resume_loop.py` | `backend/scripts/` | 恢复 NM 构建 |
| `run_narrative_memory_build.py` | `backend/scripts/` | 手动触发 NM 构建 |
