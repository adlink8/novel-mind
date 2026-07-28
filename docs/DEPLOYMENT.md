
=======

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

只有 ingress 应公开可达。数据库、向量库和图数据库必须位于私有网络。

## Release Gate

1. 构建并验证 FastAPI、worker 与 Next.js 的生产镜像。
2. PostgreSQL migration 从上一 revision 升级验证通过。
3. 依赖、secret、镜像和应用安全扫描通过。
4. 认证端到端、任务重启恢复、备份恢复和回滚演练通过。
5. TLS、限流、监控和告警配置有可重复部署证据。
