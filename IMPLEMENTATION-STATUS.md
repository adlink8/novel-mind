# NovelMind Implementation Status

审计日期：2026-06-13 16:05（Asia/Shanghai）  
分析工作台与关系回填补充：2026-07-16（基于代码与样例小说 runtime 证据，不推翻上方基线审计快照）  
结构工作台（Phase 20）补充：2026-07-16（Structure Workspace + NM 只读 API；禁止 promotion）  
有序执行补充：2026-07-17（hierarchy 修复与重建、NM partial、timeline 服务端章范围、线索 live re-judge、API UAT；仍禁止 promote）  
**phase21 追认与 CI 恢复补充：2026-07-26**（snapshot: master @ 9f01680；详见文末 2026-07-26 节。旧节中的 Alembic head/测试计数以该节为准）

事实来源：实际代码、自动化测试、依赖审计、Next.js 构建输出、真实 PostgreSQL 与本机 ops 运行结果。规划文档中的勾选不作为完成证据。

## Summary

安全与启动基线已经修复并验证。v0.3 的持久化导入、端到端 RAG、混合搜索、前端搜索和评测基础设施已完成；但评测质量闭环仍为 PARTIAL：仅 10/100 题 confirmed，现有 6 次运行的检索指标均为 0，faithfulness/cost 尚未计算。前端已完成文学编辑台风格重构，并通过桌面与移动端浏览器验收。

**分析工作台不再是 MISSING。** 前端 `/analysis` 为全视口 **Structure Workspace**：左侧结构导航 + 中栏时间线/关系/线索 facet。结构选中范围通过 `chapter_start` / `chapter_end` 传给时间线 API（服务端过滤，FE 仍有 densify 防御层）。后端 Phase 08/09/11 仍为 facet 生产权威；Phase 20 提供 NM **只读** structure API（`candidate_preview`，**无 active pointer / 无 promote**）。

**样例 novel 91（史莱姆，owner=2）2026-07-17 事实：** hierarchy `cb_9f9aee6bf1cb427b` **reusable_exact**（audit EXIT=0）；timeline active 约 1933 events；relationships 约 41 accepted（多 establish）；clues active **v24**（32 machine，live re-judge 已跑，**payoff_chapter 仍 0**）；NM candidate **version 1 partial**（约十余 `chapter_state`，全书未完成，无 arc/global）。同人文/导出仍 MISSING。

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
| 分析工作台 UI 壳 | VERIFIED | 全视口 `analysis-fullpage` + `structure/*`：结构为主轴；facet = timeline / relationships / clues；左右等高内部滚动；下划线 Tab；说明性文案已收敛；plot-density 时间线；关系 transition 诚实徽章；progressive active/candidate |
| Phase 20 NM 结构只读 API | VERIFIED | `GET /api/narrative-memory/{novel_id}/versions|.../tree|.../claims|.../source-links`；`structure_query.py` 只读；cutoff 过滤；**无 promotion**；API UAT 对 novel 91 返回 versions 列表 |
| 时间线服务端章范围 | VERIFIED | `GET /api/timeline/{id}` 可选 `chapter_start`/`chapter_end`；`effective_narrative_bounds` 与 spoiler 取 min；单元 `tests/unit/timeline/test_chapter_range.py`（8 passed）；FE `loadTimeline` 传结构节点范围 |
| Phase 07 hierarchy 内容一致性 | VERIFIED | `segmentation.py` 用章节原文精确切片（`e322c45`）；novel 91 force rebuild 后 audit **reusable_exact** |
| Phase 07 三层层级场景扩展检索 | VERIFIED | ADR-0004；54,984 个 Evidence→Scene 节点接入检索；消灭零重叠撕裂，Recall@5 从 80.00% 提升至 96.00% |
| Phase 05-06 知识单元物化与双轨混合检索 | VERIFIED | ADR-0002 & ADR-0005；三大作品 29 个 Q/A 知识单元物化，Active Pointers 激活；独立第三方盲测 94.8 分 (A+)，18.42ms 时延 |
| 线索 detail spoiler 对齐 | VERIFIED | list `link_count` 与 detail links 共用 `_link_visible`（`4b248e5`）；`tests/unit/clues` 含一致性用例 |

## PARTIAL

| Area | Status | Gap |
|---|---|---|
| 阅读进度 | PARTIAL | 已受 owner 隔离，但仍存于 Novel 记录，没有独立设备/历史同步模型 |
| 数据服务集成 | PARTIAL | ChromaDB 向量存储集成已实现，pgvector 备选待实现 |
| AI 路由与成本统计 | PARTIAL | 服务与模型骨架存在，业务生成端点仍未接入 |
| 生产部署 | PARTIAL | 应用会拒绝弱生产密钥，但 TLS、秘密管理和网络策略由部署环境提供 |
| RAG 评测质量闭环 | PARTIAL | 100 条数据中仅 10 confirmed；6 次运行 Recall/Precision/MRR/NDCG 均为 0；faithfulness/cost 为 null；HTTP 触发仍同步 |
| Phase 08 时间线 | PARTIAL | Worker/query/UI 已落地；服务端章范围已接入。样例 slime active 约 1933 events。流式 `analyze/stream` 仍 501 |
| Phase 09 人物关系 | PARTIAL | Observation + graph + Phase 19 `edge_kind` + FE transition 徽章（change/end）已落地。样例仍以 establish / 回填种子为主；图无法低成本识别 seed `intake_kind`；演化观测生产稀疏 |
| Phase 11 线索与伏笔 | PARTIAL | Clamp + plant→payoff UI + detail spoiler 对齐 + live re-judge（v24）已跑。样例 **payoff_chapter=0**（worker 对 provisional 上 payoff 分类短路）；标题常为 meta 截断。clamp 前 later-window 问题已修 |
| 叙事记忆 narrative memory | PARTIAL | 只读产品 API/UI + builder；novel 91 有 candidate version 与少量 `chapter_state`（run partial，全书未完，无完整 arc/global）；CLI transport/create-version 有 WIP。**无 promote** |
| 通用 AI 分析 API | PARTIAL | `POST/GET /api/analysis/...` 与 hierarchy 有实现；`analyze/stream` 仍 501 |

## MISSING

| Area | Status | Gap |
|---|---|---|
| 同人文生成 | MISSING | `fanfiction` 创建/续写端点仍返回 HTTP 501 |
| 编辑与导出 | MISSING | 无富文本编辑、版本管理和 EPUB/Markdown 导出 |
| 关系演化观测 | MISSING | 回填与当前观测以 establish 为主；change/end 生产链未在样例中形成 |
| 线索 payoff 生产闭环 | MISSING | live re-judge 已调用 Vertex，但 worker gate/title 仍不产生可验收 plant→payoff 链 |

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

---

## 2026-07-26 快照（phase21 追认 + CI 恢复；snapshot: master @ 9f01680）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Alembic head | **`18appsetting1`**（迁移目录 `backend/migrations/versions/`；旧节记 `518675fa18f8` 已过期） | `18_app_settings.py` |
| 后端测试 | **1085 passed / 189 skipped**（unit+contract；integration 27 passed） | PR #12/#13 CI |
| 前端测试 | **Vitest 236 passed（27 文件）**；Playwright 32/32；`next build` 通过 | PR #13 CI |
| AI 路由与用量 | 不再是 PARTIAL："业务生成端点未接入"已过期——`GET/PUT /api/settings/routing` + `GET /api/usage/summary` + 前端 Routing/Usage section 已交付 | `10632b1`/`f4fbf06` |
| Clue payoff 状态机 | 已修复（payoff/reinforcement 可进入 active lifecycle）；样例数据 payoff 仍 0，待 Phase 27 生产重跑 | `2cf8562` |
| Timeline 故事序 | reconcile 无约束 tie-break 由候选 ID 字典序改为叙事顺序（生产语义修复 + 回归测试） | PR #13 |
| master CI | **全绿**（2026-07-22 起连续红 → PR #13 五类根因修复后恢复）；分支保护 `ci-gate` required + enforce_admins | run 30204817945 |
| 已声明豁免 | pip-audit：chromadb PYSEC-2026-311（无修复版）；npm audit `--omit=dev`（brace-expansion 5.0.8 与 eslint 链不兼容）——解除条件见 ci.yml 注释 | `.github/workflows/ci.yml` |
| 依赖升级 | echarts 5→6.1；sharp/postcss/hono overrides；生产依赖 npm audit 0 | PR #13 |
| 文本模型供应商 | OpenAI、Anthropic、Gemini AI Studio、Ollama、自定义 OpenAI-compatible 共用 LiteLLM 与设置页模型配置；Vertex 专用路径已移除 | `ai_service.py`、`provider_catalog.py` |

---

## 2026-08-02 快照（Phase 25.2/25.3 实现并验证；snapshot: master @ 2f20a40）

以下事实覆盖上文旧节中的对应记录（25.2/25.3 = Novel Agent Runtime 基础）：

| 项 | 当前值 | 证据 |
|---|---|---|
| 25.2 Embedded Novel Agent Runtime | **VERIFIED 2026-08-02** | `25.2-VERIFICATION.md` passed（source_commit `6988ceb`） |
| 25.3 Pi Package Compatibility & Governance | **VERIFIED 2026-08-02** | `25.3-VERIFICATION.md` passed（source_commit `e4b1c95`） |
| 后端测试 | **195 passed**（CI 37 + agent_runtime 集成 24 + adversarial 56 + contract 83） | 2026-08-02 本机全量；unit 452 passed |
| agent-service（Node） | **223 passed / 10 files**；tsc clean | `cd agent-service && npx vitest run` |
| 前端测试 | **281 passed / 36 files** | `cd frontend && npm run test -q` |
| 执行门禁 | `scripts/check_agent_runtime_execution_gate.py` PASS | gate 9/9 CI 测试 |
| Agent 域工具 | 7 个类型化只读工具（get_novel/get_chapter/search/timeline/relationships/clues/narrative_memory），禁默认编码工具 | `backend/app/api/agent_tools.py` |
| Agent API | `/api/agent`（skill-runs/artifacts/approval-requests）+ `/api/agent-tools` + `/api/gateway` 已挂载 | `backend/app/main.py` |
| Skill/Artifact 持久化 | SkillRegistry/SkillVersion/SkillRun/Artifact/ArtifactRevision/NovelAgentProfile/ApprovalRequest；Alembic `27approval01` head | `backend/app/models/agent_runtime.py` + `backend/migrations/versions/` |
| MCP 隔离 | `pi-mcp-adapter@2.17.0` **ADOPT（external-tools-only）**；结果仅 `external_evidence`（`prohibited_from_canon=true`），禁止入 Canon | `agent-service/src/mcp/` + `backend/app/schemas/agent_runtime.py` |
| Web renderer 可行性 | CitedAnswerArtifact 渲染器 + external_evidence 显示纪律；**零 `@earendil-works/pi-web-ui` 依赖**（pattern-only） | `frontend/src/components/analysis/cited-answer-artifact.tsx` + `25.3-05-FEASIBILITY.md` |
| Web 审批 | SSE `approval_request` 帧 + approve/reject UX；FastAPI 保持唯一决策权威 | `agent-service/src/transport/sse.ts` + `frontend/src/components/analysis/approval-request-dialog.tsx` |
| 已知环境限制 | `test_openapi_contract.py` 在 pytest 下挂起（subprocess→litellm/tiktoken 下载）；Next 16 canary dev server 编译失败（e2e 受限）；前端遗留 29 个 typecheck 错误（`creative-project-editor.tsx`/`reader-chat-budget-section.tsx`，`FanFictionChapter` 类型缺失，与 25.2/25.3 无关） | 2026-08-02 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 26 执行需 Phase 22 3/3 + 26-01 bootstrap gate | `.planning/STATE.md` |

---

## 2026-08-02/03 快照（Phase 26 实现并验证；snapshot: master @ cb071bc）

以下事实覆盖上文旧节中的对应记录（Phase 26 = Question-Driven Retrieval and Evidence）：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 26 | **VERIFIED 2026-08-02/03** | `26-VERIFICATION.md` passed（source_commit `cb071bc`） |
| 后端测试 | **920 passed**（unit 548 + integration/queryplan 68 + adversarial 129 + agent_runtime 60 + ci 37 + contract 78） | 独立测试子代理 2026-08-02/03 |
| agent-service | **282 passed / 11 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| QueryPlan | 严格契约 + 确定性 fail-closed parser + durable QueryPlanTrace（幂等重放） | `backend/app/services/queryplan/` + migration `20260801_2601` |
| 检索适配器/融合 | 8 维度显式 availability、exact→heuristic→stable-reason 回退链、确定性 fusion | `queryplan/adapters.py` + `fusion.py` |
| 证据物化 | leaf EvidenceRef（Unicode offset+hash）、immutable content-addressed Frozen Manifest、陈旧 hash 拒绝 | `queryplan/evidence.py` + `service.py` |
| 共享消费者 | Reader/Analysis Chat 共享 QueryPlan 核心，保留 selection vs chapter_range anchor，暴露 trace/citation | `analysis_chat/query_adapter.py` + `test_chat_consumers.py` |
| Agent 集成 | 版本化 answer-reading-question Skill（6 只读工具 allowlist），CitedAnswerArtifact 唯一官方输出，无 Approval/Publisher | `agent-service/src/skills/answer-reading-question/` + `test_phase_26_skill.py` |
| 结构化输出完整性 | 保守 normalizer + 严格 post-repair 校验，零受保护字段合成，normalization trail/raw_hash/repaired_hash | `agent-service/src/structured-output/` + `structured_output_integrity.py` |
| Alembic | 单 head `20260801_2601`；upgrade/downgrade 可逆；alembic check 零 drift | `alembic heads` |
| 已知环境限制 | 同前：openapi subprocess 挂起、Next dev server 编译失败（e2e 受限）、live provider UAT 需 key、前端遗留 29 typecheck 错误（FanFictionChapter，与 Phase 26 无关） | 2026-08-02/03 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 27 执行需 Phase 22 3/3 + 26-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-03 快照（Phase 27/28 实现并验证；snapshot: master @ a7414c5）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 27 Novel World Model | **VERIFIED 2026-08-03** | `27-VERIFICATION.md` passed（source_commit `0616920`） |
| Phase 28 Whole-Book Narrative Memory | **VERIFIED 2026-08-03** | `28-VERIFICATION.md` passed（source_commit `a7414c5`） |
| 后端测试（Phase 27/28 相关） | unit 650 + narrative_memory 157 + adversarial 223 + agent_runtime 96 + ci 37 = **1352 passed**（28 全量） | 独立测试子代理 2026-08-03 |
| agent-service | **433 passed / 14 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| World Model | 事件/因果边（证据门控）、角色 epistemic 历史（cutoff/POV）、实体/规则/例外、四 label authority + disclosure | `backend/app/services/world_model/` + migrations 2701/2702/2703 |
| Narrative Memory | failure/recovery checkpoints、章节终态 + frozen manifest、语义弧/卷/全局、跨维度闭合 + 一键分析 | `backend/app/services/narrative_memory/` + migration `20260801_2801` |
| Agent skills | answer-reading-question、propose-world-model-candidates、analyze-chapter、build-story-arc（4 skills） | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_2801` | `alembic heads` |
| 已知环境限制 | 同前 + `test_qualification_command_pg.py` 3 失败为既有 `.venv` 路径硬编码；narrative_memory 集成套件在 Windows 需分批串行跑 | 2026-08-03 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 29 执行需 Phase 22 3/3 + 28-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-03 快照（Phase 29 实现并验证，v1.2 里程碑完成；snapshot: master @ efa4f77）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 29 Quality Qualification | **VERIFIED 2026-08-03** | `29-VERIFICATION.md` passed（source_commit `efa4f77`） |
| v1.2 里程碑（26–29） | **完成**（实现 + 验证） | 26/27/28/29 VERIFICATION 均 passed |
| 后端测试 | **1197 passed**（unit 683 + integration/qualification 90 + adversarial 239 + agent_runtime 115 + ci 37） | 独立测试子代理 2026-08-03 |
| agent-service | **491 passed / 15 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| Reading QA | 八桶 gold set（local/cross-chapter/global/causal/character/world/no-answer/spoiler）+ 分桶评测 + 三维审计 | `backend/app/services/qualification/` + `backend/evals/reading_qa_v1.json` |
| Browser UAT | 服务端契约 28p + e2e specs（33 tests）；citation jump/accessibility/spoiler-safe | `test_browser_contract.py` + `frontend/e2e/reader-chat-quality.spec.ts` |
| Agent skills | 5 skills：answer-reading-question、propose-world-model-candidates、analyze-chapter、build-story-arc、evaluate-reading-skill-runs | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_2801`（Phase 29 无新 migration） | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、`.venv` 路径 3 失败、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-03 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 30 执行需 Phase 22 3/3 + 29-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-03 快照（Phase 30 实现并验证；snapshot: master @ 67908b1）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 30 Visual Bible | **VERIFIED 2026-08-03** | `30-VERIFICATION.md` passed（source_commit `67908b1`） |
| 后端测试 | **1212 passed**（unit 732 + visual_bible unit 49 + integration 22 + adversarial 239 + agent_runtime 133 + ci 37） | 独立测试子代理 2026-08-03 |
| agent-service | **543 passed / 16 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **315 passed / 39 files** | `cd frontend && npx vitest run` |
| Visual Bible | candidate 契约（6 模型）、证据物化 + owner-scoped API、工作区 UI、review/versioning envelope、build-visual-bible skill | `backend/app/models/visual_bible.py` + `backend/app/services/visual_bible/` + `frontend/src/components/visual-bible/` |
| Agent skills | 6 skills：answer-reading-question、propose-world-model-candidates、analyze-chapter、build-story-arc、evaluate-reading-skill-runs、build-visual-bible | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_visual_bible` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-03 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 31 执行需 Phase 22 3/3 + 30-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-03 快照（Phase 31 实现并验证；snapshot: master @ fae6b68）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 31 Key Scene Detection | **VERIFIED 2026-08-03** | `31-VERIFICATION.md` passed（source_commit `fae6b68`） |
| 后端测试 | **1355 passed**（unit 786 + key_scenes unit 54 + integration 26 + adversarial 245 + agent_runtime 151 + visual_bible 22 + ci 37） | 独立测试子代理 2026-08-03 |
| agent-service | **597 passed / 17 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **330 passed / 40 files** | `cd frontend && npx vitest run` |
| Key Scene Detection | scene 契约/边界、多信号显著性 + 多样性排序、人工审查 + 冻结 set、detect-key-scenes skill（工具 13） | `backend/app/models/key_scene.py` + `backend/app/services/key_scenes/` + `frontend/src/components/key-scenes/` |
| Agent skills | 7 skills：answer-reading-question、propose-world-model-candidates、analyze-chapter、build-story-arc、evaluate-reading-skill-runs、build-visual-bible、detect-key-scenes | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_key_scene` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、CI PG 残留 composite type（需 schema reset）、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-03 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 32 执行需 Phase 22 3/3 + 31-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-03 快照（Phase 32 实现并验证；snapshot: master @ ca06706）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 32 Scene Spec and Prompt Compiler | **VERIFIED 2026-08-03** | `32-VERIFICATION.md` passed（source_commit `ca06706`） |
| 后端测试 | **1443 passed**（unit 875 + scene_spec 66 + prompt_compiler 48 + adversarial 245 + agent_runtime 172 + ci 37） | 独立测试子代理 2026-08-03 |
| agent-service | **655 passed / 18 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **348 passed / 41 files** | `cd frontend && npx vitest run` |
| Scene Spec / Prompt | SceneSpec/PromptRevision 契约、evidence-to-spec 编译器、provider 适配器、validation/preview/review、compile-scene-spec skill | `backend/app/services/scene_spec/` + `backend/app/services/prompt_compiler/` + `frontend/src/components/scene-spec/` |
| Agent skills | 8 skills：+compile-scene-spec | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_prompt_review_events` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、`test_postgres_migrations.py` 过期 head pin、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-03 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 33 执行需 Phase 22 3/3 + 32-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-04 快照（Phase 33 实现并验证；snapshot: master @ 1b8a658）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 33 Illustration Generation & Consistency | **VERIFIED 2026-08-04** | `33-VERIFICATION.md` passed（source_commit `1b8a658`） |
| 后端测试 | **1485 passed**（unit 927 + illustrations unit 52 + integration 30 + adversarial 251 + agent_runtime 188 + ci 37） | 独立测试子代理 2026-08-03/04 |
| agent-service | **714 passed / 19 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **367 passed / 42 files** | `cd frontend && npx vitest run` |
| Illustration | job/asset/budget 契约、mock 生成 + durable worker + 存储、identity/style 一致性评分、review/compare/approval 工作流、illustrate-scene skill（工具 14） | `backend/app/services/illustrations/` + `frontend/src/components/illustrations/` |
| Agent skills | 9 skills：+illustrate-scene | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_illustration_jobs` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-04 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 34 执行需 Phase 22 3/3 + 33-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-04 快照（Phase 34 实现并验证，v1.3 里程碑完成；snapshot: master @ 68819ac）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 34 In-Text Anchors, Reader and Export | **VERIFIED 2026-08-04** | `34-VERIFICATION.md` passed（source_commit `68819ac`） |
| v1.3 里程碑（30–34） | **完成**（实现 + 验证） | 30/31/32/33/34 VERIFICATION 均 passed |
| 后端测试 | **1607 passed**（unit 1002 + anchors 91 + export 24 + adversarial 251 + agent_runtime 202 + ci 37） | 独立测试子代理 2026-08-04 |
| agent-service | **776 passed / 20 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **386 passed / 44 files** | `cd frontend && npx vitest run` |
| Illustration Anchors | hash-verified 锚点契约、响应式 reader 呈现、锚点修复、Markdown/HTML/EPUB 导出、propose-illustration-anchor skill + 确定性发布 | `backend/app/services/illustration_anchors/` + `backend/app/services/export/` + `frontend/src/components/reader/` |
| Agent skills | 10 skills：+propose-illustration-anchor | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_illustration_anchors` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-04 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 35 执行需 Phase 22 3/3 + 34-VERIFICATION（已存在）或进一步 override | `.planning/STATE.md` |

---

## 2026-08-04 快照（Phase 35 实现并验证；snapshot: master @ 5992c25）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 35 Triple Knowledge Spaces and Canon Fork | **VERIFIED 2026-08-04** | `35-VERIFICATION.md` passed（source_commit `5992c25`） |
| 后端测试 | **1720 passed**（unit 1052 + canon_fork unit 50 + integration 43 + adversarial 329 + agent_runtime 219 + ci 37） | 独立测试子代理 2026-08-04 |
| agent-service | **825 passed / 21 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **386 passed / 44 files** | `cd frontend && npx vitest run` |
| Canon Fork | 三空间不可混用契约、fork snapshot/cutoff、检索/引用隔离、负向污染 guard、create-canon-fork skill + 确定性 materializer | `backend/app/services/canon_fork/` + `backend/app/models/canon_space.py` |
| Agent skills | 11 skills：+create-canon-fork | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_canon_contamination04` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、live UAT 需 provider key、前端 29 typecheck 遗留 | 2026-08-04 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 36 执行继续在 35-39 override 下 | `.planning/STATE.md` |

---

## 2026-08-04 快照（Phase 36 实现并验证；snapshot: master @ a354a1e）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 36 Derivative Project and Editor | **VERIFIED 2026-08-04** | `36-VERIFICATION.md` passed（source_commit `a354a1e`） |
| 后端测试 | **1855 passed**（unit 1063 + derivative 全套 125 + adversarial 391 + agent_runtime 239 + ci 37） | 独立测试子代理 2026-08-04 |
| agent-service | **875 passed / 22 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **404 passed / 46 files** | `cd frontend && npx vitest run` |
| Derivative Editor | owner-scoped 项目 CRUD、章节规划 + Markdown 编辑器、autosave CAS/history/diff/rollback、浏览器 UAT + gate、edit-derivative-story skill + 确定性 Revision Service | `backend/app/services/derivative_editor/` + `frontend/src/components/writing/` |
| Agent skills | 12 skills：+edit-derivative-story | `agent-service/src/skills/` |
| Alembic | 单 head `20260801_derivative_agent_edit01` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、`test_agent_tools.py` contract 32 既有失败、前端 29 typecheck 遗留 | 2026-08-04 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 37 执行继续在 35-39 override 下 | `.planning/STATE.md` |

---

## 2026-08-04 快照（Phase 37 实现并验证；snapshot: master @ b8594e3）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 37 Constrained Generation | **VERIFIED 2026-08-04** | `37-VERIFICATION.md` passed（source_commit `b8594e3`） |
| 后端测试 | **2059 passed**（unit 1171 + derivative_generation 108 + 集成 30 + adversarial 451 + agent_runtime 263 + ci 37） | 独立测试子代理 2026-08-04 |
| agent-service | **927 passed / 23 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **404 passed / 46 files** | `cd frontend && npx vitest run` |
| Constrained Generation | context package 编译器、约束草稿生成 runner、一致性 gates + BranchSuggestion、显式分歧 override、continue-derivative-story skill | `backend/app/services/derivative_generation/` |
| Agent skills | 13 skills：+continue-derivative-story | `agent-service/src/skills/` |
| Alembic | 单 head `20260802_derivative_override01` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败、openapi subprocess 挂起、agent_runtime 需 `-o timeout=600`、前端 29 typecheck 遗留 | 2026-08-04 本机 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 38 执行继续在 35-39 override 下 | `.planning/STATE.md` |

---

## 2026-08-05 快照（Phase 38 实现并验证；snapshot: master @ fad8978）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 38 Derivative Visual Consistency | **VERIFIED 2026-08-05** | `38-VERIFICATION.md` passed（source_commit `fad8978`） |
| 后端测试 | **2157 passed**（unit 1214 + derivative 10-file 集成 121 + adversarial 500 + agent_runtime 285 + ci 37） | 独立测试子代理 2026-08-05 |
| agent-service | **974 passed / 24 files**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **416 passed / 47 files** | `cd frontend && npm test` |
| Derivative Visual Consistency | fork Visual Bible schema/lineage/不可变 Original 边界、derivative Scene Spec compiler + 8 gates、candidate 资产存储 + 跨章一致性 + PublishedDerivativeVisualAsset DTO/query、review seam/UI 面板、illustrate-derivative-scene skill + publish_derivative_visual action | `backend/app/services/derivative_visual/` + `frontend/src/components/writing/visual-review-panel.tsx` |
| Agent skills | 14 skills：+illustrate-derivative-scene；facade TOOL_NAMES 20→21 | `agent-service/src/skills/` |
| Alembic | 单 head `20260802_derivative_asset01` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败（38-04 e2e 6 用例 route-mock 可解析）、openapi subprocess 挂起、agent_runtime 需 `-o timeout=600`、前端 39 typecheck 遗留（新增文件 0 错误） | 2026-08-05 本机 |
| 已修复 stale 断言 | `test_derivative_visual_schema` downgrade 目标改为 `20260802_derivative_override01`；`test_derivative_editor_gate` no-publish 检查限定浏览器编辑器表面（排除 approval-gated agent-tools action 域） | commit `fad8978`，7p/8p 复验通过 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3）；Phase 39 执行继续在 35-39 override 下 | `.planning/STATE.md` |

---

## 2026-08-05 快照（Phase 39 实现并验证；snapshot: master @ c21c9e0）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 39 Derivative Export Closeout | **VERIFIED 2026-08-05** | `39-VERIFICATION.md` passed（source_commit `c21c9e0`） |
| 后端测试 | **2229 passed**（unit 1258 + derivative export 集成/security 76 + adversarial 550 + agent_runtime 308 + ci 37） | 独立测试子代理 2026-08-05 |
| agent-service | **1020 passed**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **429 passed / 48 files** | `cd frontend && npm test` |
| Derivative Export Closeout | 可重现 Markdown/EPUB export（frozen ExportSnapshot）、bounded provenance package、export browser UAT 面板、prepare-export skill + approve_export/materialize_export action、独立 audit gate（lineage 10 项 + REQ-SHIP-01 基线，qualified_candidate/blocked 无 promotion path） | `backend/app/services/derivative_export/` + `frontend/src/components/writing/export-panel.tsx` |
| Agent skills | 15 skills：+prepare-export；facade TOOL_NAMES 21→23 | `agent-service/src/skills/` |
| Alembic | 单 head `20260802_derivative_asset01` | `alembic heads` |
| 已知环境限制 | 同前：e2e Next dev server 编译失败（39-03 e2e 36 用例 route-mock 可解析）、EPUB 无 validator 显式 unverified 不标绿、openapi subprocess 挂起、agent_runtime 需 `-o timeout=600`、前端 typecheck 遗留（新增文件 0 错误） | 2026-08-05 本机 |
| audit gate 诚实状态 | Phase 39 milestone 已交付并验证；Phase 22 0/3 + REQ-SHIP-01 基线（TLS/secret/backup/monitoring/cost budget）缺证据 → 最终 verdict 恒 blocked，永不 promotion | `39-VERIFICATION.md` |
| 里程碑 | **v1.4 (35–39) 完成**；v1.2 (26–29) + v1.3 (30–34) + v1.4 (35–39) 全 roadmap 交付 | `.planning/STATE.md` |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3，最终发布门唯一未验证项） | `.planning/STATE.md` |

## 2026-08-05 快照（Phase 40 chat_backfill 按需分析；snapshot: master @ 8ba59d3）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Phase 40 问答按需分析（chat_backfill） | **已实现 2026-08-05**（用户扩展决策：问答证据不足→后台按需触发分析 skill→物化域表 candidate） | 本轮实现 + 测试 |
| 触发链路 | reader_chat worker 产出 abstain（`uncertainty.reason_code=no_evidence`）后按 QueryDimension 映射触发对应 skill（world_projection/character_state→propose-world-model-candidates、raw_text→detect-key-scenes、relations→build-visual-bible、events/timeline→build-story-arc），每次最多 2 个；SkillRun 增 `origin=chat_backfill` + `backfill_dimension` + `user_message_id` + 部分唯一索引防在途重复 | `backend/app/services/agent_runtime/backfill.py` + `worker.py` |
| poller 端点 | `GET /api/agent/queued-runs` + `POST /api/agent/queued-runs/{id}/claim`（gateway token 认证、原子 queued→running + lease reclaim + 铸造 internal_token）；finalize/cancel 放宽为 `require_agent_actor` | `backend/app/api/agent_service_runs.py` |
| agent-service poller | `poller.ts`：轮询 + claim + 复用 session.prompt + finalize（internal token 认证），并发上限、lease reclaim、conflict 静默；`startServer` 启停 | `agent-service/src/poller.ts` |
| 物化 | `materialize.py`：finalize 成功后 background task 按 artifact.type 记录物化结果；digest 类型（chapter_analysis/story_arc）诚实 skipped，不自动 promotion（域表 candidate 写入依赖既有 gate 前提） | `backend/app/services/agent_runtime/materialize.py` |
| 前端 | MessageView 增 `backfill_runs`，analysis-chat-panel 渲染「后台分析中/完成」chip | `conversations.py` + `analysis-chat-panel.tsx` |
| Alembic | 单 head `085fffd58ee9`（down=当前 head `20260802_derivative_asset01`） | `alembic heads` |
| 后端测试 | 新增 11p unit（backfill 映射/去重）+ 6p integration（poller 端点/claim/materializer，CI PG）；既有 agent 回归 295p + agent_runtime 37p 无回归 | `pytest` |
| agent-service | **1028 passed**；tsc 0 errors（新增 poller 2p） | `cd agent-service && npx vitest run` |
| 前端 | **428 passed / 47 files**；tsc 干净 | `cd frontend && npm test` |
| 端到端验证 | poller 自动发现 queued run → claim（queued→running）→ 执行（Gemini 分析，耗时 >3min 因模型调用，链路本身正常） | 本机实测 |
| 物化闭环（第二层） | **已实现 2026-08-05**：skill 产物真正写进域表 candidate 行（key_scene_sets/candidates/evidence_ranges、world_model_knowledge、visual_bible_versions），并接线检索（reader_chat.fetch_knowledge_evidence + queryplan world_projection resolver）让下一轮问答可见 candidate 证据（带 candidate:True 标记） | materializers.py + retrieval.py + context.py |
| 边界诚实说明 | 物化只写域表 candidate（review_state/epistemic_status=candidate，gate_status=passed 仅表示确定性 gate 通过）；**candidate → available/published 仍需现有用户审批**（key_scene:approve / VisualBibleReview / EpistemicGate approval），符合「无自动 promotion」契约；digest 类型（analyze-chapter/story-arc）诚实 skipped 不写域表 | 本轮设计 |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3，最终发布门唯一未验证项） | `.planning/STATE.md` |

---

## 2026-08-06 快照（Agent 自动路由闭环 + 统一 AI 助手；snapshot: master @ 8c21c5c）

以下事实覆盖上文旧节中的对应记录：

| 项 | 当前值 | 证据 |
|---|---|---|
| Agent 自动路由闭环 | **已实现 2026-08-06**：question→intent→skill 启发式路由，无命中回退 answer-reading-question；**契约恢复**：AGENT-RUNTIME-CONTRACT「The Agent selects versioned Skills」——用户不选 skill，AI 自动路由 | commit `e9f4acd` + `8c21c5c` |
| 意图路由服务 | `backend/app/services/agent_runtime/skill_router.py`：5 类意图（画图/关系/性格/关键场景/续写）+ 维度可用性补充信号；`route_question_to_skill` 最多 2 skill，同一 skill 去重；无任何命中回退 `answer-reading-question` | `skill_router.py` |
| route-skill 端点 | `POST /api/agent/novels/{id}/route-skill`：按 owner+novel 过滤 active skill，返回 `{skills, primary, question_hash, input_anchor}`；无 active 命中回退 answer-reading-question；路由是服务端决策，不作为对用户的技能建议 | `backend/app/api/agent_skills.py` |
| 锚点自动补全 | `resolve_skill_input_anchors`：生图 skill（illustrate-scene）自动从最新**已批准** PromptRevision 血缘解析 prompt_revision_id/visual_bible_version_id/scene_spec_revision_id/source_snapshot_id + 幂等 job_key（`auto-<uuid>`）；无已批准 PromptRevision 或血缘不完整→诚实失败不伪造锚 | `skill_router.py:134` |
| SSE 自动路由 | agent-service `server.ts`：SSE run body.skill 缺省→调 route-skill 自动路由；显式 body.skill 仅高级覆盖；锚字段只注入 schema 允许的字段（additionalProperties:false 防 422） | `agent-service/src/server.ts:425` |
| 统一 AI 助手 | 分析页 `AnalysisUnifiedChat`（统一对话窗口，取代 chat/agent 双 tab）+ 阅读页侧边栏 AI 助手（reader-chat-panel 扩展：对话/选区画图/续写快捷入口）；**前端不暴露 skill**，Agent 自动路由 | `frontend/src/components/analysis/analysis-unified-chat.tsx` + `frontend/src/components/reader/reader-chat-panel.tsx` |
| 真实生图 | 腾讯混元 hunyuan-image via ZCodeProxy（illustration_provider=mock\|hunyuan）；asset 281KB JPEG 端到端验证通过 | commit `8f54bae` + `backend/.env` |
| 书签 | reader_bookmarks 模型基础上移植 | commit `32cac91` |
| 插图 URL 修复 | 插图 asset bytes URL 双重 `/api` 前缀 → 404 修复 | commit `200a152` |
| 端口固化 | 后端 8010 / 前端 3005 / agent 3100 / ZCodeProxy 3001（Makefile + `scripts/keep-alive.ps1`） | commit `7175168` |
| 端到端验证 | 自动路由 5 意图 + SSE 自动执行 + 真实生图 + 锚点 + 前端 200 | 本机实测 2026-08-06 |
| 部署注意 | agent-service 启动必须注入 `NOVELMIND_GATEWAY_TOKEN=dev-agent-gateway-token-local`（backend/.env:39 定义；Makefile `dev-agent` 已注入） | `backend/.env` + `Makefile` |
| 后端测试 | **1305 passed** | 独立测试子代理 2026-08-06 |
| agent-service | **1039 passed**；tsc 0 errors | `cd agent-service && npx vitest run` |
| 前端 | **460 passed** | `cd frontend && npm test` |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3，最终发布门唯一未验证项） | `.planning/STATE.md` |

---

## 2026-08-11 快照（v1.5 Windows 桌面收口：证据级验证，非 release-ready；snapshot: HEAD d6bbb05 + 45-01/45-02/45-03）

以下为 v1.5 桌面（Phase 41–45）的诚实收口记录。verdict 由
`desktop/scripts/verify-release-evidence.ps1 -RequireAll` 基于校验和绑定的证据计算，
`releaseReady=false`。**不得**将下述任何一项描述为已签署/已发布/clean-VM 通过。

| 项 | 状态 | 证据 |
|---|---|---|
| v1.5 桌面收口 verdict | **release-blocked（releaseReady=false）**：仅在证据支持层级完成收口；clean-VM 缺失（D-45-07/D-45-09）使 REQ-DESK-10 保持发布阻塞 | `.planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-VERIFICATION.md`（verifier 两遍 PASS + 删证据即 FAIL 的篡改测试） |
| Windows 打包链 | **VERIFIED**：win-unpacked + NSIS 安装包，可复现构建（staged 1440 文件两遍一致），single-instance、无控制台、数据保留卸载；artifact 哈希与 manifest/CHECKSUMS 一致 | `desktop/dist/CHECKSUMS.SHA256` 3/3；45-01-SUMMARY.md；package 套件 21/21 |
| 升级/恢复/卸载 | **VERIFIED**：备份优先可逆升级事务 + 默认数据保留卸载，fixture 校验和绑定；update 套件 23/23（两遍） | 45-02-SUMMARY.md |
| 打包 UAT（本机近似） | **32/32 PASS**（win-unpacked 与 NSIS 安装版；`-RequireAll` 同）；13/13 路由、离线/杀服务 fail-closed、数据留存；**clean_vm=false，非 pristine-VM 证据** | 45-UAT.md + `desktop/tests/clean-vm/results/` |
| Electron 安全负面审计 | **VERIFIED**：打包 release-security 套件 **17/17**（webPreferences 实读、CSP/导航/窗口/权限 deny-by-default、伪造 sender、未知/畸形/超大 payload、local-auth replay、脱敏、外部加载），dev IPC/policy 21/21 + credential/local-auth 16/16，合计 **54/54** | `.planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-SECURITY.md` |
| SBOM/证据完整性 | **VERIFIED**：`desktop/scripts/generate-sbom.ps1 -Verify` **12/12 PASS 两遍无漂移**；`runtime-manifest.json` 哈希 `cb8fa6c9…` 与 41-DECISION 一致（41 NO-GO 证据未被篡改）；staged 逐文件 re-hash 1440/34019789 字节；secret 扫描 0；记录 `unsigned=true` | `desktop/dist/release-sbom.json` |
| 41 NO-GO 边界 | **未翻案**：41-DECISION 保持 NO-GO；仅打包已证明运行时（Electron 43.3.0 + embedded Node v24.18.1 + Next standalone）；Python/FastAPI、PostgreSQL/pgvector、vector store **未捆绑**（PREREQ-2/3/4，post-45），打包应用除 `next` 外全部 fail-closed | 41-DECISION.md + `desktop/dist/bundled-inventory.json` |
| main 进程接线 | **未完成（post-45 前置）**：`PackagedProcessAdapter` 未接入 main 启动；UAT/安全套件经 `NOVELMIND_RENDERER_URL` seam 加载打包渲染器（同一机制） | 45-01/43-01/44-03 摘要 + 45-UAT.md Known Stubs |
| 代码签名/发布 | **外部门（D-45-06）**：artifact 未签名（`signAndEditExecutable=false`），无 publish 段；证书获取与发布需显式授权，未执行 | electron-builder.yml + 45-SECURITY.md |
| 仍阻塞 | Phase 22 Nightly 3/3 未达成（0/3，最终发布门唯一未验证项）；**pristine clean-VM 执行缺失（REQ-DESK-10 发布阻塞，D-45-07/D-45-09）** | `.planning/STATE.md` + 45-VERIFICATION.md |

结论：v1.5 桌面在证据支持的层级收口（打包、升级、UAT 近似、安全负面、SBOM 全部通过并有
校验和绑定证据），但**不是 release-ready**：clean-VM、签名/发布、bundled Python/PG/vector、
main 进程打包适配器接线均为未满足的外部门或 post-45 前置，Phase 22 保持独立 0/3。

---

## 2026-08-23 快照（生效链路与高置信度残余清理）

本快照不改变上面的历史打包证据，也不宣称桌面端 release-ready。

| 项 | 当前值 | 证据 |
|---|---|---|
| 启动链路 | Makefile 只保留固定 3005 的前端目标；前端生产启动和 Agent 启动都会先构建，避免运行旧 `.next` / `dist` | `Makefile`、`scripts/keep-alive.ps1`、`agent-service/package.json` |
| Desktop 路由资格门 | 当前业务路由 **14/14**；本轮 standalone Playwright 静态资源、页面加载、客户端导航 **22/22 PASS**；提交中的当前路由/静态资源门已接入 Browser CI，历史 13 路由证据清单保持哈希不变 | `desktop/tests/fixtures/route-inventory.json`、`desktop/scripts/check-current-route-parity.mjs`、`.github/workflows/ci.yml` |
| 残余清理 | 删除 3 组未挂载前端组件及专属测试、未使用 Agent API 聚合兼容层；移除 4 个无源码调用依赖 | `frontend/src/components/`、`backend/app/api/`、依赖清单 |
| 本轮验证 | Backend CI/contract **295 passed**；Frontend **816 passed / 84 files**；Agent **1146 passed / 36 files**；Ruff、ESLint、TypeScript、Frontend build 全部通过 | 本轮本地命令输出 |
| 发布边界 | Compose 仍不包含完整应用服务；`PackagedProcessAdapter` 仍未接入 main；因此完整服务器部署和桌面发布仍未闭环 | `docker-compose.yml`、`desktop/src/main/index.ts` |
