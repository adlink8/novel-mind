# NovelMind Implementation Status

审计日期：2026-06-13 16:05（Asia/Shanghai）

事实来源：实际代码、自动化测试、依赖审计、Next.js 构建输出和真实 PostgreSQL Alembic 命令。规划文档中的勾选不作为完成证据。

## Summary

安全与启动基线已经修复并验证。v0.3 的持久化导入、端到端 RAG、混合搜索、前端搜索和评测基础设施已完成；但评测质量闭环仍为 PARTIAL：仅 10/100 题 confirmed，现有 6 次运行的检索指标均为 0，faithfulness/cost 尚未计算。前端已完成文学编辑台风格重构，并通过桌面与移动端浏览器验收。

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
| API Key 加密 | VERIFIED | JWT 与加密密钥分离；Fernet 密文；支持轮换 |
| 上传与删除一致性 | VERIFIED | 随机文件名、根目录 containment、读取上限、原子写入；数据库失败清文件，删除提交失败恢复文件 |
| 数据库迁移 | VERIFIED | 用户、owner 和复合唯一约束 migration；真实 PostgreSQL `upgrade/current/check` 通过 |
| 响应最小化 | VERIFIED | 小说详情不返回 `source_path`，章节集合不返回正文 |
| 小说导入管线 | VERIFIED | GB18030/Big5/Shift_JIS 多编码检测、章节自动分割、进度跟踪（龙族Ⅰ 测试通过） |
| 导入任务持久化 | VERIFIED | ImportJob 模型 + 状态机 + 租约并发 + SHA-256 幂等 + 取消 + 重启恢复 |
| 前端认证可用性 | VERIFIED | 注册、登录、Cookie 会话检查和注销 UI；Axios 携带凭据 |
| 前端路由和构建 | VERIFIED | `/novels/[id]` 动态路由 + `/search` 搜索页正确 |
| 前端视觉与响应式 | VERIFIED | 统一 AppShell、桌面浮动侧栏、移动顶部/底部导航、纸张主题和 Lucide 图标；1280px 与 390px 浏览器验收通过 |
| 搜索结果安全与跳转 | VERIFIED | 搜索结果跳转到实际 `/novels/[id]` 路由；高亮片段不再通过 `dangerouslySetInnerHTML` 注入 |
| 自动化与静态检查 | VERIFIED | pytest 239（非 e2e）+ RAG e2e 12、Vitest 22、ESLint 0、Ruff 0、Bandit 0 High/Medium |
| 依赖安全 | VERIFIED | chromadb CVE-2026-45829 (non-critical)；`npm audit` 0 |
| RAG 管线 | VERIFIED | 分块 → Ollama nomic-embed-text (768维) → ChromaDB → 混合搜索（向量 + BM25） |
| 混合搜索 | VERIFIED | BM25 tsvector 全文搜索 + 向量加权融合；全局/小说内搜索 API；搜索页面 + 阅读页内搜索面板 |
| RAG 评测基础设施 | VERIFIED | EvalDataset/EvalRun/EvalResult ORM；bm25/baseline_vector/hybrid_search；owner 隔离 API；CLI；错误案例；前端 ECharts 管理与趋势页 |

## PARTIAL

| Area | Status | Gap |
|---|---|---|
| 阅读进度 | PARTIAL | 已受 owner 隔离，但仍存于 Novel 记录，没有独立设备/历史同步模型 |
| 数据服务集成 | PARTIAL | ChromaDB 向量存储集成已实现，pgvector 备选待实现 |
| AI 路由与成本统计 | PARTIAL | 服务与模型骨架存在，业务生成端点仍未接入 |
| 生产部署 | PARTIAL | 应用会拒绝弱生产密钥，但 TLS、秘密管理和网络策略由部署环境提供 |
| RAG 评测质量闭环 | PARTIAL | 100 条数据中仅 10 confirmed；6 次运行 Recall/Precision/MRR/NDCG 均为 0；faithfulness/cost 为 null；HTTP 触发仍同步 |

## MISSING

| Area | Status | Gap |
|---|---|---|
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
| Backend pytest | VERIFIED：239 non-e2e + 12 RAG e2e，Python 3.12.12 |
| Frontend Vitest | VERIFIED：22 passed |
| Frontend lint/build | VERIFIED：ESLint 0；Next 16 Turbopack build passed |
| Python audit | PARTIAL：Bandit 中高风险 0；本轮 pip-audit 因网络/索引访问 180 秒超时，未重新确认 |
| Node audit | VERIFIED：npm audit 0 |
| Alembic | VERIFIED：head `518675fa18f8`；current/check passed on PostgreSQL 16 |
| UI 浏览器验收 | VERIFIED：登录、工作台、书架、评测、设置；1280px/390px；console 0 errors |
| 小说导入集成 | VERIFIED：《龙族Ⅰ·火之晨曦》11 章 / 274,011 字导入成功 |

## GSD Starting Point

最后完成 milestone：**v0.2 - 安全与架构修复**。v0.3 经复审恢复为 active/gaps_found；当前无可自动执行 plan，下一步依赖人工评测集校准。

`03-01` 的工程 Delivery 已实现，但质量验收未完成：
- Slice 1: 评测数据结构 + ORM (EvalDataset/EvalRun/EvalResult)
- Slice 2: 候选测试题生成器 (generate_eval_candidates.py + AI 直接生成)
- Slice 3: 自动评测引擎 (eval_service.py 三策略 + API + CLI)
- Slice 4: 前端评测管理页 (/eval)
- Slice 5: 评测可视化 (ECharts 指标对比 + 趋势折线图 + 上边栏导航)
- Slice 6: 文档与审计更新
- Post-milestone UI: UI/UX Pro Max 全站视觉重构、响应式应用壳、阅读与搜索体验、安全高亮修复

Key metrics:
- Backend: 239 non-e2e + 12 e2e passed
- Frontend: 22 tests + build passed
- ChromeDB models: bge-m3, nomic-embed-text, qwen3.5:9b, gemma4-local (D:\Ollama\models)
- Embedding: nomic-embed-text (768维, 与 ChromaDB 存储一致)
