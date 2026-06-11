# Deployment

## Current Support Level

仓库支持本地开发和安全基线验证，尚不具备生产发布条件。`docker-compose.yml` 提供 PostgreSQL/pgvector、Chroma 和可选 Neo4j，不包含 FastAPI 与 Next.js 应用镜像。

## Development Start

```powershell
docker compose up -d db

cd backend
.\.venv311\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

cd ..\frontend
npm run dev
```

Chroma 或 Neo4j 仅在对应功能开发时启动：

```powershell
docker compose up -d chroma
docker compose --profile graph up -d neo4j
```

## Security Baseline Already Implemented

- JWT Bearer 与 HttpOnly Cookie 会话，Cookie 写请求执行 Origin 校验；
- 小说和模型配置 owner 隔离；
- provider key 加密和轮换兼容；
- provider URL allowlist、DNS 和 IP SSRF 防护；
- 上传 containment、大小限制和文件/数据库补偿；
- 生产模式拒绝弱 JWT/加密密钥；
- Python/Node 依赖审计当前为 0 已知漏洞。

## Production Blockers

- 无持久化 import job、worker、重启恢复和取消机制；
- 无应用生产镜像、TLS ingress、限流和 CSRF 策略评审；
- 开发数据库/Neo4j 默认凭据不可用于生产；
- 数据服务端口当前映射到宿主机；
- Chroma 使用未固定的 `latest` 镜像；
- 无 secret manager、备份恢复演练、监控和告警；
- RAG 与 AI 生成链路尚未实现。

## Minimum Production Topology

```text
TLS ingress
  -> Next.js web
  -> authenticated FastAPI
  -> PostgreSQL on private network
  -> durable import worker/queue
  -> selected vector store on private network
  -> provider egress allowlist/proxy
```

只有 ingress 应公开可达。数据库、向量库和图数据库必须位于私有网络。

## Release Gate

1. 完成 GSD `02-03` 持久化导入任务。
2. PostgreSQL migration 从上一 revision 升级验证通过。
3. 依赖、secret、镜像和应用安全扫描通过。
4. 认证端到端、备份恢复和回滚演练通过。
5. TLS、限流、监控和告警配置有可重复部署证据。
