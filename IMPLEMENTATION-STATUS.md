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
| Vertex/Gemini | 实验态（无测试/无文档），仅 Timeline/Clue live 调用使用 | `vertex_gemini.py` |

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
