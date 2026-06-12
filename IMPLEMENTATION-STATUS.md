# NovelMind Implementation Status

审计日期：2026-06-12 17:30（Asia/Shanghai）

事实来源：实际代码、自动化测试、依赖审计、Next.js 构建输出和真实 PostgreSQL Alembic 命令。规划文档中的勾选不作为完成证据。

## Summary

安全与启动基线已经修复并验证。已知的未授权访问、模型配置越权、SSRF、密钥复用、旧密文兼容、上传文件补偿、数据库迁移漂移、日志凭据泄露和高风险依赖问题均已关闭。当前未完成项属于产品与可靠性建设，主要是持久化导入任务和 RAG 管线。

状态定义：

- **VERIFIED**：存在实际实现，并通过当前可重复命令验证。
- **PARTIAL**：已有部分实现，但关键端到端能力仍不完整。
- **MISSING**：规划中存在，实际代码未实现。

## VERIFIED

| Area | Status | Evidence |
|---|---|---|
| 账户与会话 | VERIFIED | bcrypt、JWT issuer/audience、HttpOnly SameSite Cookie、Bearer API 支持、登录/注销和前端门禁 |
| 小说权限隔离 | VERIFIED | 列表按 owner 过滤；详情、章节、进度、状态和删除执行所有权校验；跨用户测试返回 404 |
| AI 模型配置隔离 | VERIFIED | 配置按 owner 唯一和查询；跨用户读写、测试、默认切换和删除均被阻止 |
| SSRF 防护 | VERIFIED | 自定义地址要求服务端精确主机白名单；校验协议、凭据、DNS、IPv4/IPv6 和非公网地址；调用前重复验证 |
| API Key 加密 | VERIFIED | JWT 与加密密钥分离；`enc:v1` Fernet 密文；旧明文/旧密钥兼容；支持 previous key 轮换 |
| 上传与删除一致性 | VERIFIED | 随机文件名、根目录 containment、读取上限、原子写入；数据库失败清文件，删除提交失败恢复文件 |
| 数据库迁移 | VERIFIED | 用户、owner 和复合唯一约束 migration；真实 PostgreSQL `upgrade/current/check` 通过，head `f3c8b7b2dbf7` |
| 响应最小化 | VERIFIED | 小说详情不返回 `source_path`，章节集合不返回正文 |
| 小说导入管线 | VERIFIED | GB18030/Big5/Shift_JIS 多编码检测、章节自动分割、进度跟踪；`/api/novels/upload` 端点完整验证（龙族Ⅰ 测试通过） |
| 前端认证可用性 | VERIFIED | 注册、登录、Cookie 会话检查和注销 UI；Axios 携带凭据 |
| 前端路由和构建 | VERIFIED | `/novels/[id]` 动态路由正确；Next 16 Turbopack 生产构建通过 |
| 自动化与静态检查 | VERIFIED | pytest 172、Vitest 22、ESLint 0、Ruff 0、Bandit 中高风险 0 |
| 依赖安全 | VERIFIED | Python 3.11 环境 `pip-audit` 0；`npm audit` 0 |
| AI 状态目录 | VERIFIED | `.planning/` 已移除；`.gsd/` 为唯一 AI 读写状态目录 |

## PARTIAL

| Area | Status | Gap |
|---|---|---|
| 阅读进度 | PARTIAL | 已受 owner 隔离，但仍存于 Novel 记录，没有独立设备/历史同步模型 |
| 导入进度 | PARTIAL | ImportJob 模型持久化，支持状态机、重试和异步后台处理；大文件（>5MB）流式处理待实现 |
| 数据服务集成 | PARTIAL | ChromaDB 向量存储集成已实现，pgvector 备选待实现 |
| AI 路由与成本统计 | PARTIAL | 服务与模型骨架存在，业务生成端点仍未接入 |
| 生产部署 | PARTIAL | 应用会拒绝弱生产密钥，但 TLS、秘密管理和网络策略由部署环境提供 |
| RAG 管线 | PARTIAL | 分块、embedding、向量存储、搜索 API 已实现，集成测试待完成 |

## MISSING

| Area | Status | Gap |
|---|---|---|
| 持久化导入任务 | VERIFIED | ImportJob 模型 + 状态机 + 重试机制 + 异步后台处理已实现 |
| 混合语义搜索 | MISSING | 无向量 + 关键词搜索 API 与前端结果页 |
| AI 分析与创作 | MISSING | 分析、人物、时间线和同人文生成仍返回空状态或 501 |
| 编辑与导出 | MISSING | 无富文本编辑、版本管理和 EPUB/Markdown 导出 |

## Security Closure

| Finding | Result |
|---|---|
| 匿名和跨用户 IDOR | CLOSED：统一认证与 owner 依赖，新增跨用户回归测试 |
| Cookie 会话 CSRF | CLOSED：写请求要求 Origin 命中服务端 CORS 白名单；Bearer 客户端不受影响 |
| bcrypt 超长密码 | CLOSED：注册和登录均拒绝超过 72 UTF-8 bytes 的密码 |
| 模型配置越权 | CLOSED：AIModelConfig 增加 owner_id 和用户级唯一约束 |
| 自定义 URL SSRF | CLOSED：管理员白名单 + DNS/IP 校验；私网主机需显式配置 |
| JWT/加密共用弱密钥 | CLOSED：独立密钥，生产启动校验，旧密钥 keyring |
| 上传/数据库双写不一致 | CLOSED：失败补偿与删除隔离恢复测试 |
| ORM/Alembic 漂移 | CLOSED：真实 PostgreSQL upgrade/current/check 通过 |
| 数据库 URL 日志泄漏 | CLOSED：SQLAlchemy URL 脱敏渲染 |
| Python/Node 已知依赖漏洞 | CLOSED：LiteLLM 1.83.10+、Next 16.3.0-canary.6、Vitest 4、Vite 8；审计均为 0 |

## Verification Snapshot

| Check | Result |
|---|---|
| Backend pytest | VERIFIED：172 passed，Python 3.14.2 |
| Frontend Vitest | VERIFIED：22 passed |
| Frontend lint/build | VERIFIED：ESLint 0；Next 16 Turbopack build passed |
| Python audit | VERIFIED：pip-audit 0；Bandit 中高风险 0 |
| Node audit | VERIFIED：npm audit 0 |
| Alembic | VERIFIED：head `f3c8b7b2dbf7`；current/check passed on PostgreSQL 16 |
| 小说导入集成 | VERIFIED：《龙族Ⅰ·火之晨曦》11 章 / 274,011 字导入成功 |

## GSD Starting Point

当前 milestone：**v0.2 - 安全与架构修复**。

`02-01` 与 `02-02` 已完成。`/gsd auto` 下一入口：

`.gsd/phases/02-security-and-architecture-remediation/02-03-PLAN.md`

`02-03` 只保留未完成的持久化导入任务和收尾验证，不重新执行已关闭的安全修复。
