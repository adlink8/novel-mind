# Roadmap: NovelMind

## Milestones

- [x] v0.1 审计与启动修复 - 建立可信实现基线和启动级契约。
- [x] v0.2 安全与架构修复 - 关闭安全、迁移、路由、依赖和导入可靠性阻断项。
- [ ] v0.3 小说导入 + RAG 索引 - 导入/索引/搜索已交付，RAG 评测质量闭环仍有缺口。
- [x] v0.5 自动质量与 CI 门禁 - 以全栈自动化和双模型仲裁替代人工 RAG 质量确认；REQ-AUTO-11 closed（06-08/06-09）。
- [x] v0.8 分层叙事记忆与层级 RAG - **SHIPPED** candidate scope 2026-07-16 — 归档 [v0.8-ROADMAP](./milestones/v0.8-ROADMAP.md) / [audit](./v0.8-MILESTONE-AUDIT.md)
- [x] v0.9 分析工作台呈现与数据诚实 - Phase 19 COMPLETE
- [x] v1.0 结构工作台与多层呈现 - Phase 20 COMPLETE（P0；NM 只读；禁止 promotion 仍有效）

- [ ] v1.1 工程与治理基线收口 - Phase 21✅ / 22（余 3-nightly 观察）/ 23–25 + 25.1 分析页对话工作台（现状核查见 [AUDIT-STATUS-REFRESH-2026-07-26](./AUDIT-STATUS-REFRESH-2026-07-26.md)）
- [ ] v1.2 单书垂直证明 - Phase 26–29（PLANNED；依赖 v1.1）
- [ ] v1.3 生产切换 - Phase 30（PLANNED；**立项需显式新授权解除 NM promotion / Reader Chat cutover 禁令**）
- [ ] v1.4 创作域 - Phase 31–34（PLANNED；核心功能完成的收口里程碑）

第一个 active milestone 已按要求设为并完成"审计与启动修复"。v0.3 经 2006-06-13 复审后恢复为 active，详见 `.planning/v0.3-MILESTONE-AUDIT.md`。

## v0.3 Plans

- [x] 核心 RAG 管线（分块/embedding/ChromaDB）
- [x] 混合搜索（BM25 + 向量融合）
- [x] 前端搜索 UI + 阅读页内搜索
- [ ] 03-rag-eval: 评测基础设施已完成；100 confirmed、有效非零指标、faithfulness/cost 待完成 (GitHub Issue #2)
- [x] 全站 UI/UX 重构：响应式应用壳、文学编辑台视觉系统、搜索与阅读体验修复

## v0.2 Plans

- [x] 02-01: 仓库与上传边界修复
- [x] 02-02: 认证、SSRF 与密钥保护
- [x] 02-03: 持久化导入任务与里程碑收尾

## v0.2 Success Criteria

1. Git/secret、上传路径、未授权访问、SSRF 和密钥存储测试通过。 VERIFIED
2. PostgreSQL Alembic upgrade/current/check 通过。 VERIFIED
3. Next.js 输出 `/novels/[id]`，集合响应不包含正文或 `source_path`。 VERIFIED
4. backend/frontend tests、build、lint、CI 和依赖审计通过。 VERIFIED
5. 导入任务有持久 job、并发安全状态机、幂等重试和重启恢复。 VERIFIED

## Progress

| Milestone / Phase | Plans Complete | Status |
|---|---:|---|
| v0.1 审计与启动修复 | 3/3 | Complete |
| v0.2 安全与架构修复 | 3/3 | Complete |
| v0.3 小说导入 + RAG 索引 | 4/5 | Gaps Found (eval gold 质量) |
| Phase 4 知识图谱 LLM 门控 | 4/4 | Complete |
| Phase 5 叙事知识单元层 | 5/5 | Complete |
| v0.5 / Phase 6 自动质量与 CI 门禁 | 9/9 | **Complete** (REQ-AUTO-11 closed) |
| Phase 7 语义/层级分块 | 6/6 | **Complete** (logic+tests+PG/indexing wiring) |
| Phase 8 版本化小说分析与时间线 | 10/10 | **Complete** (35/35 must-haves verified) |
| v0.7 / Phase 9-11 叙事关系、阅读问答与线索追踪 | Phase 09: 5/5; Phase 10: 5/5; Phase 11: 5/5 | **09 VERIFIED**; **10 PARTIAL** (real Playwright residual); **11 implement-complete** (adversarial residual closed) |
| v0.8 / Phase 12-17 分层叙事记忆与层级 RAG | 19/19 plans (12–17 done) | **COMPLETE** — Phase 12–17 verified; candidate-only/no pointer cutover |
| Phase 18 前端动效与过渡系统 | 3/3 | **COMPLETE** — 18-01..03 done; dual-viewport motion qualified |
| v0.9 / Phase 19 分析工作台呈现与数据诚实 | 4/4 | **COMPLETE** — 19-01..04 done (truth + presentation) |
| v1.0 / Phase 20 结构工作台与多层呈现 | 4/4 | **COMPLETE** — P0 Structure Workspace + NM read-only |
| v1.1 / Phase 21-25 + 25.1 工程与治理基线收口 | 4/16 | Phase 21 COMPLETE; 22 NEAR-COMPLETE (3-nightly watch); 23–25, 25.1 planned |
| v1.2 / Phase 26-29 单书垂直证明 | 0/11 | PLANNED |
| v1.3 / Phase 30 生产切换 | 0/3 | PLANNED — 立项需新授权 |
| v1.4 / Phase 31-34 创作域 | 0/11 | PLANNED |

### Phase 19: Analysis Workbench Presentation & Truth

**Goal:** 让分析台三 tab 在呈现与数据诚实上分离：时间线=章节多轨剧情；关系=已接受 vs 临时共现可区分；线索=埋设→兑现跨章对象（非时间线摘要墙）。  
**Depends on:** Phase 07, 08, 09, 11（消费契约，不重开）  
**Status:** COMPLETE (plans verified; optional phase report / ship)  
**Non-goals:** narrative-memory promotion、完整 KG 重训、换图表库  

Plans:
- [x] 19-01 truth layer & API honesty (edge_kind / clue title+span)
- [x] 19-02 timeline multi-lane chapter plot view
- [x] 19-03 relationship honest edges & ego presentation
- [x] 19-04 clue plant→payoff presentation

**Waves:** 1 (19-01) → 2 (19-02 ∥ 19-03) → 3 (19-04).  
Context: `.planning/phases/19-analysis-workbench-presentation-and-truth/19-CONTEXT.md`.  
Summaries: `19-01`..`19-04-SUMMARY.md`.

### Phase 20: Structure Workspace & Multi-layer Presentation

**Goal:** `/analysis` 以结构为主轴（自上而下呈现），时间线/关系/线索为挂载在结构节点上的 facet；复用 Narrative Memory 候选作为 L2–L4 只读骨架；无 NM 时章节结构诚实降级。  
**Depends on:** Phase 07, 08, 09, 11, 12–17 NM candidate, 19 honesty  
**Status:** COMPLETE (P0 verified; residuals in 20-VERIFICATION.md)  
**Non-goals:** NM promotion、替换 08/09/11 生产权威、Reader Chat cutover、GraphRAG/Neo4j  

Plans:
- [x] 20-01 NM read-only structure API
- [x] 20-02 Structure Workspace shell (FE)
- [x] 20-03 Scope-bound facets + claims drill + honesty polish
- [x] 20-04 Docs + verification + planning cursor

**Waves:** 1 (20-01) → 2 (20-02) → 3 (20-03) → 4 (20-04).  
Context: `.planning/phases/20-structure-workspace-multilayer-presentation/20-CONTEXT.md`.  
Research: `20-RESEARCH.md`.  
Verification: `20-VERIFICATION.md`.  
Summaries: `20-01`..`20-04-SUMMARY.md`.

### Phase 21: phase21 追认与文档一致性恢复

**Goal:** 把 2026-07-18~07-23 未走 GSD 的 "phase21" 分支工作（设置中心重构、AI 路由偏好 + 用量 API、`app_settings` 迁移 `18appsetting1`、阅读器体验、Timeline/Reader Chat 服务重写、Vertex/Gemini 实验适配）纳入规划权威，消除全部文档漂移，建立快照标识规范。  
**Requirements:** REQ-BASE-01, REQ-BASE-02  
**Depends on:** 无（立即可做，与 Phase 22 并行）  
**Status:** COMPLETE (2026-07-26) — 追认目录 `phases/21-settings-routing-usage-and-debtfix/`；IMPLEMENTATION-STATUS 2026-07-26 节；api README/`__init__` 反向漂移修正；CONCERNS.md 与 docs/路线图.md 重写  
**Non-goals:** 不重做 phase21 代码；不清理 git 历史中的 75MB dump（需单独决策）

**Success Criteria:**
1. `.planning/phases/21-settings-routing-usage-and-debtfix/` 存在 CONTEXT + SUMMARY，追认实际交付与验证证据（后端 1085 passed / 189 skipped 基线、迁移 `18appsetting1`）。
2. `IMPLEMENTATION-STATUS.md` 刷新：Alembic head（当前误记 `518675fa18f8`）、测试计数（当前误记 239）、AI 路由/用量条目、Vertex 实验态标注。
3. `backend/app/api/README.md` 与 `api/__init__.py` 的反向漂移修正（timeline/analysis 不再标 501）。
4. `docs/路线图.md`、`.planning/codebase/CONCERNS.md` 重写或标记 superseded。
5. 被更新的状态文档头部含统一快照标识（commit、日期、DB fingerprint 或"未连接 DB"声明）。

Plans:
- [ ] 21-01 phase21 追认目录 + IMPLEMENTATION-STATUS/STATE/ROADMAP 刷新
- [ ] 21-02 api README/__init__ 反向漂移修正 + codebase map 与 docs/路线图.md 重写 + 快照标识规范

**Waves:** 1 (21-01) → 2 (21-02)

### Phase 22: CI 恢复绿与门禁真实生效

**Goal:** master 每日 CI 恢复全绿，ci-gate 聚合脚本修复，分支保护 required check 实际阻止红色合入（PR #11 曾带红 ci-gate 合入）。  
**Requirements:** REQ-BASE-03, REQ-BASE-04  
**Depends on:** 无（与 Phase 21 并行）  
**Status:** NEAR-COMPLETE (2026-07-26) — 22-01/22-02 经 PR #13 落地（五类根因；master run 30204817945 全绿）；22-03 已设 `ci-gate` required + enforce_admins（此前分支保护无任何 required check，即 PR #11 带红合入根因）；**余：连续 3 个 nightly 全绿观察（预计 2026-07-29 核销）**。豁免：pip-audit chromadb PYSEC-2026-311、npm audit `--omit=dev`（解除条件见 ci.yml 注释）

**Success Criteria:**
1. Ruff check 0 违规（import 排序、`Optional`→`| None`、TRY004 等机械清理）。
2. `tests/integration/timeline/test_real_qualification.py::test_release_entry_blocks_postgres_report_authority_mismatch` 按根因修复（区分测试契约漂移 vs qualification 逻辑回归）。
3. Browser smoke 的 Playwright webServer exit 127 修复。
4. ci-gate "Write producer results envelope" SyntaxError 修复，聚合逻辑有自身单测。
5. CodeQL 两语言 Analyze 恢复通过或按官方指引调整配置。
6. 分支保护验证：ci-gate 为 required 且演练证明红色无法合入；PR #11 带红合入的原因记录在案。
7. 连续 3 个 nightly 运行全绿。

Plans:
- [ ] 22-01 Ruff/静态清理 + ci-gate 聚合脚本修复与单测
- [ ] 22-02 integration timeline 资格测试 + Browser smoke webServer + CodeQL 修复
- [ ] 22-03 分支保护强制验证 + 带红合入根因记录 + 3 天绿色观察

**Waves:** 1 (22-01 ∥ 22-02) → 2 (22-03)

### Phase 23: 层级注册表与叙事系统边界

**Goal:** 建立唯一 Layer Registry ADR（S/D/R/A 四命名空间），固定 Narrative Unit 与 Narrative Memory 的用途、边界与消费顺序，分离三种 level 字段语义。关闭 NM-ARCH-001..004、NM-GOV-001。  
**Requirements:** REQ-GOV-01, REQ-GOV-02, REQ-GOV-03  
**Depends on:** Phase 21  
**Status:** PLANNED  
**Non-goals:** 不新增语义层；不做存量字段强制迁移

**Success Criteria:**
1. `docs/adr/0001-layer-registry.md`：S0-S6 语义层、D* 数据成熟度、R* 发布生命周期、A* 架构层；每层输入/输出/SSOT/可重建性/模型写入权/失效传播/对应表与 API。
2. `docs/adr/0002-narrative-unit-vs-narrative-memory.md`：两系统用途、依赖、是否替代、各自 Active 含义、消费顺序；PROJECT/REQUIREMENTS 引用。
3. 新增代码/schema 禁用裸 `L0-L6`；`chunk_level`/`semantic_level`/`release_status` 命名规范落入 API schema 约定（新字段强制）。
4. 旧 L* 文档批量标注 "superseded by ADR-0001"。
5. Facet（Timeline/Relationship/Clue）作为只读投影、禁止无证据反写主结构的规则写入 ADR 并有契约测试。

Plans:
- [ ] 23-01 Layer Registry ADR + 文档引用与 superseded 标注
- [ ] 23-02 NU/NM 边界 ADR + 字段语义规范 + facet 反馈环禁止契约测试

**Waves:** 1 (23-01) → 2 (23-02)

### Phase 24: 存储一致性与检索统一

**Goal:** 关闭 raw TextChunk→Chroma 双写一致性缺口（fail-closed），统一 raw chunk / Narrative Unit / NM 三层检索的路由、降级与 citation 规则，并入 Reader Chat 优先级契约。关闭 NM-GOV-002/003/006、NM-DATA-010。  
**Requirements:** REQ-GOV-04, REQ-GOV-05  
**Depends on:** Phase 23（ADR 先固定权威顺序）  
**Status:** PLANNED

**Success Criteria:**
1. `indexing_service.py`：索引写入带 journal/幂等键/完成标记；`failed_count > 0` 时 novel 状态不得为 `ready`（fail-closed 或显式 `partial` 且检索侧感知）；重建消除"DB 已删、旧向量残留"窗口。
2. 索引完整性 gate：DB chunk 集与 Chroma collection 可 reconcile，缺失/孤儿可检出可修复；manifest 绑定数据库版本。
3. 统一检索策略：mode 决策移到服务端 router；units 为空/损坏时自动降级 raw chunks 并标注 fallback reason；citation 只能来自叶子原文层的规则代码化。
4. NM hierarchical retrieval 在 router 中有显式接入点（candidate-only 保持关闭但降级顺序已定义）；Reader Chat `SOURCE_PRIORITY` 与 router 契约合并或引用同一 ADR。
5. Neo4j 投影防双写自动化约束（只读 accepted facts、可全量 replay）有契约测试。

Plans:
- [ ] 24-01 TextChunk→Chroma journal/幂等/fail-closed + 删除窗口修复
- [ ] 24-02 索引 reconcile/完整性 gate + manifest 绑定
- [ ] 24-03 统一 router/fallback/citation + Reader Chat 优先级并轨 + Neo4j 约束测试

**Waves:** 1 (24-01) → 2 (24-02 ∥ 24-03)

### Phase 25: Facet 数据诚实与 API 契约收口

**Goal:** 补齐 clue/relationship 的数据诚实字段与成本结算，收口三个占位 API 双轨。关闭 NM-DATA-005/007/008、NM-API-001/002/003。  
**Requirements:** REQ-GOV-06, REQ-GOV-07, REQ-GOV-08  
**Depends on:** Phase 23  
**Status:** PLANNED

**Success Criteria:**
1. Clue judge schema 增加独立 `short_title`（展示与审计 rationale 分离）；标题不再截断 rationale；存量标题可批量重建。
2. Clue LLM 调用按 provider price snapshot × usage 真实结算 `cost_usd`（对齐 narrative_memory/timeline/reader_chat 已有实现）；DB 合计不再恒 0。
3. `RelationshipObservation`/`CharacterRelation` 增加 `intake_kind`/`producer_kind` 枚举列并贯穿 API/UI（区分 LLM 多阶段观察 / timeline seed-backfill / 共现候选）。
4. `api/characters.py` 适配到 Phase 09 Relationship 或返回 410 deprecated，禁止返回看似合法的空数组；`analyze/stream` 删除契约或实现；`fanfiction` 标记 deferred（v1.4 接管）。
5. 变更均有迁移 + 契约测试，CI 保持绿。

Plans:
- [ ] 25-01 clue short_title + cost_usd 真实结算
- [ ] 25-02 relationship intake/producer lineage 迁移与贯穿
- [ ] 25-03 characters/stream/fanfiction API 契约收口

**Waves:** 1 (25-01 ∥ 25-02) → 2 (25-03)

### Phase 25.1: 分析页对话工作台（Analysis Chat Workspace）

**Goal:** `/analysis` 默认呈现改为**对话窗口**，可视化（结构/时间线/关系/线索）成为可切换视图；对话与阅读器聊天共享同一数据底座与剧透边界，仅锚点不同。  
**Requirements:** REQ-ACHAT-01, REQ-ACHAT-02, REQ-ACHAT-03  
**Depends on:** Phase 10（reader_chat 服务栈复用）、Phase 20（NM 只读结构）；弱依赖 Phase 24（router 统一后自动获得降级链，可后补对齐）  
**Status:** PLANNED (2026-07-26 立项)  
**Non-goals:** 不建第二套检索/引用底座；不改变 NM candidate-only 红线；不移除可视化视图

**核心设计契约（2026-07-26 决议）：**
1. **同一数据底座**：分析页对话复用 `reader_chat` 服务栈（gateway/budget/citation/审计），新增 scope 参数区分锚点；禁止另建平行链路，否则两个窗口会对同一问题给出矛盾答案。
2. **同一剧透边界**：两个窗口都默认按阅读进度 cutoff 裁剪上下文；仅当该小说的既有 per-novel 全书开关显式打开后才接收全书（与 REQ-TIME-08/REQ-CHAT-03 一致）。**不是"章节 vs 全书"，而是"锚点不同 + 同一边界"。**
3. **锚点差异**：阅读器聊天 = 选区锚定（选中原文 + 章节可见上下文）；分析页聊天 = 结构锚定（cutoff 内章节树/NM 只读候选骨架 + 跨章检索证据）。citation 规则两边相同：最终引用只能回落叶子原文并服务端重验。
4. **质量诚实**：Arc/Global 数据到位（Phase 26/27）前，跨章回答来自 leaf/raw 检索并在 UI 标注来源层级；数据闭环后无需改架构自动增强。

**Success Criteria:**
1. `/analysis` 默认打开对话视图，与可视化视图可即时切换且互不丢状态（对话历史、结构选中范围）。
2. 分析页对话为 owner-scoped 持久多会话（复用 Phase 10 会话模型或同构扩展），每条回答带可点击 citation 跳原文。
3. 默认模式下问未读内容得到与 Reader Chat 一致的防剧透行为（拒答/裁剪而非泄露）；全书开关状态两个窗口一致生效。
4. 结构选中范围（章节区间/NM 节点）可作为对话上下文注入，且在回答中可见（"基于第 12–34 章"）。
5. 对话调用走既有预算/成本/审计链；desktop + 390px Playwright 验收。

Plans:
- [ ] 25.1-01 chat scope 契约与后端扩展（structure-anchored context assembly + 会话模型复用）
- [ ] 25.1-02 分析页对话 UI + 视图切换 + citation 跳转
- [ ] 25.1-03 剧透一致性对抗测试 + 双视口浏览器验收

**Waves:** 1 (25.1-01) → 2 (25.1-02) → 3 (25.1-03)

### Phase 26: NM 整书构建收敛

**Goal:** novel 91 全部 515 章进入明确终态，Arc/Volume 与 Global 真实生成，失败隔离与恢复在长篇上被证明，产出完整成本/复用报告；全程 candidate-only。关闭 NM-DATA-001/002 数据侧。  
**Requirements:** REQ-BOOK-01, REQ-BOOK-02, REQ-BOOK-03  
**Depends on:** Phase 22（CI 绿）、Phase 24（索引一致性）  
**Status:** PLANNED

**Success Criteria:**
1. 515 章 chapter_state 全部 completed 或显式隔离（隔离章带 reason code，不静默 pending）；33 个历史 failed 恢复或归类。
2. Arc/Volume 覆盖全部连续章节范围，Global Story Model 生成且 manifest 可由 DB 重算。
3. 单章失败只阻断所属 Arc 在真实长篇上验证（非仅 fixture）；是否允许 qualified partial Arc 有决策记录。
4. 构建报告：calls/tokens/cost/cache hits/来源状态完整；`cost_usd` 真实（依赖 Phase 25）。
5. 无任何 active pointer 创建或移动（负向测试）。

Plans:
- [ ] 26-01 失败章诊断与恢复策略（schema/package/budget/transport 分类处理）
- [ ] 26-02 全书 chapter_state 收敛运行 + 断点续跑运维记录
- [ ] 26-03 Arc/Global 生成 + manifest 重算验证 + 成本报告

**Waves:** 1 (26-01) → 2 (26-02) → 3 (26-03)

### Phase 27: 跨章语义闭环

**Goal:** 在真实数据上把"事件目录"升级为因果链、"关系建立"升级为演化链、"线索发现"升级为 plant→payoff 闭环。关闭 NM-DATA-003/004 数据侧。  
**Requirements:** REQ-SEM-01, REQ-SEM-02, REQ-SEM-03  
**Depends on:** Phase 25（诚实字段）、Phase 26（全书数据）  
**Status:** PLANNED

**Success Criteria:**
1. novel 91 时间线存在证据门控的 caused/triggered/responded/blocked 因果边（数量 > 0 且抽样人工核验）。
2. relationship 存在真实 change/end 观察（生产链产出，非仅 seed establish），`valid_from/valid_to` 生效并在 UI 可见。
3. clue 存在完整 cue→reinforce→payoff/dismissed 链（payoff 状态机修复 `2cf8562` 后的生产重跑，payoff_chapter > 0），标题可读。
4. 三者保持只读 facet 契约：证据引用完整、unavailable 不解释为零事实。

Plans:
- [ ] 27-01 timeline 因果边生产运行 + 门控与抽样核验
- [ ] 27-02 relationship 演化观察生产链 + intake 区分验证
- [ ] 27-03 clue 全书 re-judge 生产重跑 + payoff 链验收

**Waves:** 1 (27-01 ∥ 27-02 ∥ 27-03)

### Phase 28: 质量证据与 v0.3 收口

**Goal:** 重建当前环境的评测权威：100 confirmed 金标、非零检索指标、faithfulness/cost 报告，全部绑定 DB fingerprint；对真实 NM 候选跑 qualification；关闭 v0.3 与 Phase 10 residual。关闭 NM-DATA-009。  
**Requirements:** REQ-EVAL-02, REQ-EVAL-03, REQ-QUAL-06, REQ-QUAL-07  
**Depends on:** Phase 26、Phase 27  
**Status:** PLANNED

**Success Criteria:**
1. 100 条 confirmed 评测题（复用 Phase 06 双模型仲裁链自动 qualify + 人工抽检），当前 DB 中 dataset/run 非零。
2. baseline/hybrid/units Recall/MRR/NDCG 非零且可复现；faithfulness/cost 完整计算。
3. 每份报告绑定 DB fingerprint、dataset version、source snapshot、commit。
4. NM 真实候选（Phase 26 产物）通过 Phase 17 冻结 qualification：local/arc/global/no-answer/spoiler 全桶，结论 `qualified_candidate` 或 `blocked` 并归档。
5. `reader-chat-real.spec.ts` 在真实 Postgres 下通过（Phase 10 residual 关闭）。
6. v0.3 里程碑标记完成。

Plans:
- [ ] 28-01 评测金标重建 + 指标非零验证 + 快照绑定
- [ ] 28-02 faithfulness/cost 报告 + NM 候选真实 qualification
- [ ] 28-03 Phase 10 real Playwright residual + v0.3 收口审计

**Waves:** 1 (28-01) → 2 (28-02 ∥ 28-03)

### Phase 29: 统一消费验证（v1.2 收口）

**Goal:** 结构工作台与 Reader Chat 在真实 Arc/Global 数据上联动验证，降级路径实测，完成 v1.2 里程碑审计。  
**Requirements:** REQ-BOOK-04, REQ-BOOK-05  
**Depends on:** Phase 26–28  
**Status:** PLANNED

**Success Criteria:**
1. `/analysis` Structure Workspace 展示真实 Arc/Global 骨架（非降级章节树），facet 范围联动正确。
2. Reader Chat 在 NM 不可用/partial 时 fallback 正确且引用仍回落原文（实测）。
3. 桌面 + 390px 浏览器验收通过；全站 build/test/Playwright 绿。
4. v1.2 里程碑审计报告按 `implementation_readiness / sample_data_coverage / quality_qualification` 三维度归档。

Plans:
- [ ] 29-01 工作台真实数据联动 + fallback 实测
- [ ] 29-02 双视口浏览器验收 + v1.2 里程碑审计

**Waves:** 1 (29-01) → 2 (29-02)

### Phase 30: NM Promotion 契约、A/B 与切换决策

**Goal:** 设计并实现 NM 生产 promotion 全链，用 A/B 数据决定是否切换 Reader Chat 与工作台的默认消费层。**本阶段立项需显式新授权解除 promotion/cutover 禁令。**  
**Requirements:** REQ-PROM-01, REQ-PROM-02, REQ-PROM-03  
**Depends on:** v1.2 全部  
**Status:** PLANNED (blocked on authorization)

**Success Criteria:**
1. Active Pointer 唯一权威、CAS promotion、before/after manifest、rollback journal 实现并测试（对齐 Narrative Unit 已有 promotion 模式）。
2. A/B：NM hierarchical vs Narrative Unit vs raw hybrid 在同一冻结题集/预算下对比，阈值预先声明。
3. 达标 → 切换默认层并保留回退开关；不达标 → 记录 blocked 原因保持 candidate-only（两种结论均为合法退出）。
4. 切换后 spoiler/owner/citation 对抗测试全绿。

Plans:
- [ ] 30-01 promotion 契约实现（pointer/CAS/manifest/rollback）
- [ ] 30-02 A/B 冻结对比 + 阈值判定
- [ ] 30-03 切换执行（或 blocked 归档）+ 回归与回退演练

**Waves:** 1 (30-01) → 2 (30-02) → 3 (30-03)

### Phase 31: 三重知识空间契约

**Goal:** 建立 Original Canon / User Interpretation / Fanfiction Canon 的隔离契约：不同 authority、namespace、version、citation 规则；禁止创作内容污染原作分析层。  
**Requirements:** REQ-CRE-01, REQ-CRE-02  
**Depends on:** Phase 23 ADR 体系（可与 v1.3 并行）  
**Status:** PLANNED

**Success Criteria:**
1. ADR + 数据模型：三空间各自的表/namespace/版本语义；fanfiction 内容不得进入原作检索索引、评测语料或 facet 生产链（负向测试）。
2. 用户 override 与原作事实冲突的表达与展示规则固定。
3. Reader Chat / 检索 / NM 构建的输入边界更新：创作空间内容显式排除。

Plans:
- [ ] 31-01 三空间 ADR + 数据模型与迁移
- [ ] 31-02 隔离负向测试 + 既有管线输入边界更新

**Waves:** 1 (31-01) → 2 (31-02)

### Phase 32: 创作项目与编辑器

**Goal:** 用真实实现替换 fanfiction 501：创作项目模型、Markdown 编辑、版本历史、章节规划；`/writing` 从占位页升级为真实入口。  
**Requirements:** REQ-CRE-03, REQ-CRE-04  
**Depends on:** Phase 31  
**Status:** PLANNED

**Success Criteria:**
1. 创作项目 CRUD（owner 隔离）、章节规划、Markdown 编辑与自动保存。
2. 版本历史：每次保存可追溯、可 diff、可回滚。
3. fanfiction API 501 移除。
4. 权限/安全对齐既有标准（owner、上传边界、审计）。

Plans:
- [ ] 32-01 创作域数据模型 + API + 迁移
- [ ] 32-02 编辑器 UI + 版本历史
- [ ] 32-03 章节规划 + 浏览器验收

**Waves:** 1 (32-01) → 2 (32-02 ∥ 32-03)

### Phase 33: 理解约束的续写

**Goal:** 续写生成注入原作理解约束（指定叙事位置的人物状态、世界状态、时间线因果、未回收伏笔、文风），使输出区别于普通文本生成，并可评测一致性。  
**Requirements:** REQ-CRE-05, REQ-CRE-06  
**Depends on:** Phase 32、v1.2 数据闭环（人物/世界状态来自 NM chapter_state/arc）  
**Status:** PLANNED

**Success Criteria:**
1. 续写上下文包 = 用户设定 + 指定 cutoff 处的 NM 状态 + 相关证据引用；包内容可审计。
2. 生成走既有预算/审计/成本链路（复用 timeline/reader_chat gateway 模式）。
3. 一致性评测：人物行为/既定事实/时间线矛盾的自动检查 + 冻结样例集门禁。
4. 用户可选择偏离原作（override 显式记录），偏离不回写原作空间。

Plans:
- [ ] 33-01 理解约束上下文包 + 生成链路
- [ ] 33-02 一致性评测与门禁
- [ ] 33-03 偏离管理 + UI 集成

**Waves:** 1 (33-01) → 2 (33-02 ∥ 33-03)

### Phase 34: 导出、部署与项目收口

**Goal:** Markdown/EPUB 导出、生产部署基线、全项目最终审计——核心功能完成的正式退出。  
**Requirements:** REQ-CRE-07, REQ-SHIP-01, REQ-SHIP-02  
**Depends on:** Phase 32、33  
**Status:** PLANNED

**Success Criteria:**
1. 创作作品与（可选）分析报告导出 Markdown/EPUB，内容与版本一致。
2. 生产部署清单：TLS/密钥管理/备份/监控成本告警核对（沿用现有 deploy configs 收口）。
3. 最终审计：三维度全绿；TARGET-GAP-ANALYSIS 的 Target C 达标条件逐项核销；Target D 差距重估。
4. `docs/` 面向用户文档完整（GETTING-STARTED 到创作流程）。

Plans:
- [ ] 34-01 导出管线 + 格式验收
- [ ] 34-02 部署基线核对 + 监控告警
- [ ] 34-03 最终里程碑审计 + 文档收口

**Waves:** 1 (34-01 ∥ 34-02) → 2 (34-03)

## Cross-cutting Execution Rules (v1.1+)

1. 所有 plan 遵循 GSD：`Steps / Must-Haves / Verification`，slice 以 `Test, Fix, and Confirm` 结束，完成状态必须有命令或代码证据。
2. **红线（直至 Phase 30 立项获得显式授权前有效）**：禁止 NM promotion / active pointer 创建或移动 / Reader Chat cutover。
3. 每个 phase 收口按 `implementation_readiness / sample_data_coverage / quality_qualification` 三维度分开报告，禁止合并为单一百分比。
4. 任何新功能立项前先检查是否依赖尚未关闭的前置 phase。
5. 建议节奏：Phase 21 与 22 立即并行启动；23 → 24 ∥ 25；随后进入 v1.2。

## Auto Start

**v1.0 / Phase 20 COMPLETE（2026-07-16）** — plans 20-01..04 verified (P0).  
**2026-07-17 ordered follow-on** — hierarchy green (91), timeline chapter range, rel transition UI, clue live re-judge (payoff residual), API UAT; NM build **partial** (see `20-ORDERED-EXEC-REPORT.md`).  
**禁止** narrative-memory promotion / Reader Chat cutover 无新授权时执行。  
**2026-07-26 全量规划落地** — Phase 21–34 已按标准 GSD 格式写入本文件（见上方 Phase 21..34 区块）；现状核查事实见 `AUDIT-STATUS-REFRESH-2026-07-26.md`。  
Next: 启动 v1.1 — Phase 21（phase21 追认与文档一致性）∥ Phase 22（CI 恢复绿）；随后 23 → 24 ∥ 25。原 follow-on 项（NM build to arc/global、clue payoff 生产重跑）已并入 Phase 26/27。

## Backlog

### Cross-cutting architecture/data governance gaps

- Source of truth: [`ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md`](./ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md)
- Expected-goal distance and staged path: [`TARGET-GAP-ANALYSIS-2026-07-17.md`](./TARGET-GAP-ANALYSIS-2026-07-17.md)
- Before adding more semantic layers, close or explicitly accept the audit's P0/P1 contracts: `S/D/R/A` layer registry, Narrative Unit vs Narrative Memory boundary, storage/projection authority, index consistency, lifecycle/provenance, and reproducible verification baseline.
- This entry records backlog only; it does not authorize NM promotion, pointer mutation, data deletion, or automatic cleanup.

### Phase 999.1: 首页 UI 优化 (BACKLOG)
- 删除占位统计卡片（小说总数/章节总数/AI分析次数/同人文作品）
- 已扩展为全站 UI/UX 重构并完成：2006-06-13

### Phase 999.2: Bug 修复 (BACKLOG)
- 阅读页右上角"上一章"与"退出账号"按钮重合
- 搜索返回"搜索失败请重试"无内容
- 已完成：2006-06-13

### Phase 4: LLM 语义判定与证据门控知识图谱链路

**Goal:** 建立可审计的知识图谱构建链路：脚本负责召回、证据、规则、阈值和写库；LLM 只负责语义理解、关系候选和判断。该链路同时支持小说与历史语料。
**Requirements**: REQ-KG-01..06
**Depends on:** Phase 3
**Status:** VERIFIED on 2006-07-02
**Plans:** 4/4 plans complete

Plans:
- [x] 04-01 knowledge data contracts: PostgreSQL candidate/judgment/evidence/review contracts
- [x] 04-02 candidate packages and LLM judgment: deterministic recall package + structured LLM semantic judgment
- [x] 04-03 evidence gates and projection: schema/evidence/threshold/conflict gates + accepted graph projection
- [x] 04-04 evaluation and domain fixtures: fiction/history fixtures, graph eval CLI, cost/latency/faithfulness reporting

### Phase 5: Narrative Knowledge Unit Layer

**Goal:** 将 Phase 04 的 accepted judgments 蒸馏为可追溯、可版本化、可评测和可回滚的叙事知识单元检索层，同时保留原始 chunk 混合召回。
**Requirements**: REQ-NU-01..08
**Depends on:** Phase 4
**Status:** VERIFIED on 2026-07-12
**Plans:** 5/5 plans complete

Plans:
- [x] 05-01 narrative unit contracts and source snapshot
- [x] 05-02 canonicalization and lifecycle gates
- [x] 05-03 candidate index and hybrid retrieval
- [x] 05-04 frozen evaluation, canary, and promotion
- [x] 05-05 incremental refresh, reconcile, and rollback

### Phase 6: Automated Quality and CI Gates

**Goal:** 补齐后端、前端、数据库、向量库、API、浏览器和 live AI 的自动化验证，并以冻结证据、双模型仲裁和确定性规则建立无需日常人工逐题确认的 RAG 质量门禁。
**Requirements:** REQ-AUTO-01..11
**Depends on:** Phase 3, Phase 5
**Status:** COMPLETE (06-01..06-09; REQ-AUTO-11 closed)
**Plans:** 9 plans

Plans:
- [x] 06-01 test taxonomy and deterministic quality foundations
- [x] 06-02 PostgreSQL and Chroma integration matrix
- [x] 06-03 frozen fixtures, adversarial gates, and Judge calibration
- [x] 06-04 RAG scoring, durable worker, and Eval API compatibility
- [x] 06-05 API contracts, frontend components, and browser journeys
- [x] 06-06 unified CI producer DAG, security, artifacts, and nightly qualification
- [x] 06-07 ci-gate aggregation, branch protection, and release gate
- [x] 06-08 persistent QualityRun repository and lineage-bound identity chain
- [x] 06-09 persistent baseline prepare/commit and cross-chunker reports

### Phase 7: Semantic and Hierarchical Chunking

**Goal:** 在保留原始 chunk 证据底座的前提下，使用规则初切与 LLM 低置信边界判断建立可版本化、可评测、可回滚的 chapter → scene → evidence 层级切片，并由 Phase 06 自动质量门选择可发布 chunker。
**Requirements:** REQ-CHUNK-01..08
**Depends on:** Phase 06
**Status:** COMPLETE (07-01..07-06; REQ-CHUNK-01..08)
**Plans:** 6 plans

Plans:
- [x] 07-01 chunker manifests, source lineage, and deterministic baseline
- [x] 07-02 rule boundary confidence and candidate segmentation
- [x] 07-03 LLM low-confidence boundary adjudication and fallback
- [x] 07-04 hierarchical scene/evidence storage and retrieval
- [x] 07-05 candidate rebuild, incremental refresh, promotion, and rollback
- [x] 07-06 chunker A/B quality qualification and release verification

### Phase 8: Versioned novel analysis orchestration and interactive timeline

**Goal:** 以持久、版本化、证据约束的后台分析任务生成小说时间事件，并在全局分析工作台渐进展示防剧透的双顺序横向时间线。
**Requirements**: REQ-TIME-01..10
**Depends on:** Phase 7
**Status:** COMPLETE — 10/10 plans; independent verification passed 35/35 must-haves
**Plans:** 10/10 plans complete

Plans:
- [x] 08-01 durable analysis jobs and immutable version foundation
- [x] 08-02 strict timeline schema and evidence-bound chapter extraction
- [x] 08-03 cross-chapter reconciliation, overrides, budget, and promotion
- [x] 08-04 progressive owner-scoped API and spoiler boundary
- [x] 08-05 global analysis workspace and interactive ECharts timeline
- [x] 08-06 frozen qualification, end-to-end verification, and release gate
- [x] 08-07 production worker orchestration and PostgreSQL call boundaries
- [x] 08-08 real ordering/source isolation and production qualification
- [x] 08-09 final cancellation/cache/source-offset/qualification gap closure
- [x] 08-10 executable DB-and-command release authority gap closure

### Phase 9: Dynamic Character Relationship Graph

**Goal:** 基于已证据门控的人物关系事实和时间线版本，生成可按叙事进度演化、默认防剧透的小说人物关系图。
**Requirements:** REQ-REL-01..06
**Depends on:** Phase 04, Phase 08
**Status:** COMPLETE — 5/5 plans; independent verification passed 21/21 must-haves (2026-07-15)

Plans:
- [x] 09-01 append-only relationship observation contracts, evidence lineage, overrides, and migration
- [x] 09-02 deterministic relationship candidates, bounded evidence packages, LLM judgment, and gates
- [x] 09-03 owner/version/spoiler graph API, fold, overrides, and replayable Neo4j projection
- [x] 09-04 Cytoscape.js analysis workspace, evidence panel, timeline linking, and large-graph degradation
- [x] 09-05 frozen fixtures, adversarial/performance tests, browser qualification, and release gate

**Waves:** 1 → 2 → 3 → 4 → 5 complete. Verified: `.planning/phases/09-dynamic-character-relationship-graph/09-VERIFICATION.md`.

### Phase 10: Reader Text-Selection AI and Multi-Session Conversations

**Goal:** 让读者从阅读器选取原文后，在同一本小说的多个持久会话中进行证据受限、防剧透的 AI 对话。
**Requirements:** REQ-CHAT-01..07
**Depends on:** Phase 08, Phase 09
**Status:** PARTIAL — 5/5 plans; independent verification 19/20 must-haves (2026-07-15); real Playwright residual (Postgres 5432)

Plans:
- [x] 10-01 durable conversations, messages, selections, manifests, citations, jobs, calls, budgets, and migration
- [x] 10-02 owner-scoped multi-session lifecycle, ordered messages, idempotency, and API contracts
- [x] 10-03 server-verified Unicode selections, visible-context assembly, and Phase 09 read-only contract
- [x] 10-04 cited answer worker, audit lineage, dual budgets, cancellation, retry, and adversarial tests
- [x] 10-05 reader selection entry, collapsible multi-session window, citations, browser qualification, and release gate

**Waves:** 1 → {2, 3} → 4 → 5 complete. Verified: `.planning/phases/10-reader-selection-ai-and-multi-session-conversations/10-VERIFICATION.md` (PARTIAL). Phase 09 production reader wired; no clue product UI. Close residual with `reader-chat-real.spec.ts` when DB is up.

### Phase 11: Clue and Foreshadow Tracking

**Goal:** 以证据、版本和人工可控的五状态生命周期，发现、追踪、核验小说中的线索、伏笔及其回收。
**Requirements:** REQ-CLUE-01..07
**Depends on:** Phase 08, Phase 09, Phase 10
**Status:** IMPLEMENT COMPLETE — 5/5 plans; product must-haves verified; pure-module write scan residual closed (2026-07-15)

Plans:
- [x] 11-01 clue lifecycle contracts, PostgreSQL authority, append-only states, overrides, and migration
- [x] 11-02 cross-chapter candidate recall, bounded evidence packages, Phase 09 read-only protocol, and LLM gates
- [x] 11-03 durable clue worker, versioning, budgets, overrides, owner/spoiler API, and explicit unavailable-source behavior
- [x] 11-04 analysis workspace clue timeband, filters, evidence panel, payoff chain, and manual actions
- [x] 11-05 frozen fixtures, false-positive/spoiler/version adversarial tests, browser qualification, and release gate

**Waves:** 1 → 2 → 3 → 4 → 5 complete. Verified: `.planning/phases/11-clue-and-foreshadow-tracking/11-VERIFICATION.md`. Alembic head `11cluetrack01`. Clue UI on `/analysis` only (no top-level `/clues`). Qualification: `backend/scripts/run_clue_qualification.py`. Phase 10 chat is never a clue source; Phase 09 reader outages remain `source_unavailable`, not zero signals.

### Phase 12: Read-only Asset Audit and Eligibility

**Goal:** 在任何模型调用或上层写入前，以只读方式证明单本小说现有 hierarchy 与可选分析资产是否可被 v0.8 精确复用，并给出可机器处理的阻断原因和最小重建范围。
**Requirements:** V08-AUDIT-01, V08-AUDIT-02, V08-AUDIT-03, V08-AUDIT-04
**Depends on:** Phase 07, Phase 08, Phase 09, Phase 11
**Status:** COMPLETE — 3/3 plans; independent verification passed 4/4 requirements and 10/10 truths (2026-07-15)
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 运维者对一部 owner-scoped 小说运行审计后，可获得按 hierarchy、timeline、relationship、clue 的版本化资产清单，以及每项唯一的 `reusable_exact`、`rebuild_required`、`blocked` 或 `optional_unavailable` 判定和 reason codes。
2. 审计会从 PostgreSQL 重算 active Phase 07 build 的 source snapshot、manifest、chapter→scene→evidence 父子关系、offset/content hash 和覆盖率；任一 required invariant 无效时，后续构建在 provider call 前被阻断。
3. timeline、relationship 或 clue 缺失、不可用或 lineage 不匹配时会作为可选来源状态显式报告，不会被解释为“没有事件/关系/线索”，也不会阻断仅依赖 hierarchy 的资格。
4. 自动化负向验证证明审计前后 provider call 数为零，现有数据未被修复，并且 chunk、timeline、clue 及其他生产 active pointer/revision 均未变化。

Plans:
- [x] 12-01 audit contracts and read-only source adapters: eligibility enums, reason codes, owner/novel/version inventory, optional-source status
- [x] 12-02 hierarchy and domain lineage verification: manifest/tree/offset/hash/coverage checks with provider-call-before blocking
- [x] 12-03 owner-scoped audit CLI/API and PostgreSQL negative tests: report, minimal rebuild scope, no model/data/pointer writes

### Phase 13: Candidate Memory Contracts and Provenance Authority

**Goal:** 建立与现有分析生命周期隔离的不可变叙事记忆候选契约，使每条 Chapter State、Story Arc/Volume 和 Global Story Model 主张均可由数据库权威追到同一 source snapshot 的叶子原文。
**Requirements:** V08-MEM-01, V08-MEM-02, V08-MEM-03, V08-MEM-04, V08-MEM-05
**Depends on:** Phase 12
**Status:** COMPLETE — verified 2026-07-16 (`13-VERIFICATION.md` status: passed)
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 系统可创建独立于 timeline/relationship/clue 的 immutable `NarrativeMemoryVersion` candidate，并持久保存 owner、novel、version、source/hierarchy lineage、prompt/schema/model/config hashes；v0.8 不存在可达的 production promotion 路径。
2. Chapter State、连续 Story Arc/Volume 和 Global Story Model 使用 strict typed claims/state deltas；extra fields、summary-only facts、包外 source refs 或未知 authoritative 字段会 fail closed。
3. 对任一候选 claim，验证器可沿 node/edge/source links 下钻并从服务器 `Chapter.content[start:end]` 重切，证明 owner、novel、snapshot、offset 和 content hash 完全一致；断链或宽泛引用使候选不合格。
4. PostgreSQL integration tests 证明父子图无环、章节范围合法、manifest 可从排序后的 node/edge/link rows 重算，且 candidate 创建/验证不会创建或移动任何 active pointer。

Plans:
- [x] 13-01 narrative memory PostgreSQL authority: additive candidate version/node/claim/edge/source-link/manifest/report migration and composite scope constraints
- [x] 13-02 strict ChapterState/StoryArc/GlobalStoryModel schemas: typed claims, deltas, uncertainty, visibility and builder lineage
- [x] 13-03 provenance closure and manifest gates: DAG/range/source re-slice validation, append-only enforcement and no-pointer invariants

### Phase 14: Durable Bottom-up Candidate Builder

**Goal:** 对已通过 Phase 12 审计的单本小说，按 Chapter State → Story Arc/Volume → Global Story Model 自下而上构建可恢复候选，同时隔离章节失败并约束所有模型费用与可选来源。
**Requirements:** V08-BUILD-01, V08-BUILD-02, V08-BUILD-03, V08-BUILD-04, V08-BUILD-05
**Depends on:** Phase 13
**Status:** COMPLETE — 4/4 plans; independent verification passed (2026-07-16); Alembic head `14membuild01`
**Plans:** 4/4 plans complete

**Success Criteria:**
1. 运维者启动 candidate dry-run 后，durable worker 先逐章生成 evidence-bound Chapter State，再按显式卷界或版本化连续范围生成 Story Arc/Volume，最后只从已验证 arcs 生成单个 Global Story Model。
2. 每个模型调用在执行前完成预算预留，并绑定 frozen source/prompt/schema/model/config exact-cache key；任务支持 checkpoint、取消和恢复，未知价格或依赖失效时零 provider 调用并明确暂停。
3. 模拟单章失败时，已完成兄弟 Chapter States 保持 byte-identical，worker 仅阻断包含该章的 arc 与 Global；恢复后从失败 stage 继续而不是无条件重跑整本小说。
4. timeline、accepted relationship observations 和 clue lifecycle 仅在 lineage/evidence 合格时作为可选 enrichment；来源 unavailable 被显式记录，Reader Chat imports/text/citations 均不能进入事实 package。
5. 构建完成后数据库重算 candidate manifest 与 worker artifact 一致，报告列出完成/失败 stages、calls、tokens、cost、cache hits 和来源状态，且所有生产 pointers 保持不变。

Plans:
- [x] 14-01 durable chapter-state worker: frozen packages, strict generation, budget, exact cache, checkpoint, cancel and resume
- [x] 14-02 contiguous arc/volume planning and aggregation: explicit volume preference, deterministic coverage gates and evidence closure
- [x] 14-03 global story aggregation and candidate manifest: validated-child-only claims, conflict/open-loop handling and DB recomputation
- [x] 14-04 optional source adapters and failure isolation: timeline/relationship/clue enrichment, chat exclusion and partial-stage recovery tests

**Waves:** 1 → 2 → 3 → 4 complete. Verified: `.planning/phases/14-durable-bottom-up-candidate-builder/14-VERIFICATION.md` (37 targeted tests passed).

### Phase 15: Adaptive Hierarchical Retrieval and Leaf Evidence Safety

**Goal:** 在不替换现有 Reader Chat 的前提下，提供可审计、全程防剧透的离线分层检索实验，使 local/arc/global/mixed 查询能够下钻并最终只引用重验后的叶子原文。
**Requirements:** V08-RETR-01, V08-RETR-02, V08-RETR-03, V08-RETR-04, V08-RETR-05
**Depends on:** Phase 14
**Status:** COMPLETE — 3/3 plans; independent verification passed (2026-07-16); 59 targeted tests
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 冻结问题进入实验入口后，确定性 router 会记录 local、arc、global 或 mixed 起始层和 reason；不同层候选确实改变检索路径，而不是仅把上层摘要附加到 leaf top-k。
2. 检索可从 Global/Arc 向 Chapter State 下钻，并在上层缺失、partial 或误路由时使用 collapsed multi-level 或 raw/leaf fallback；返回结果包含 traversal path、各层候选、omitted counts 和 fallback reason。
3. 所有最终 citation 都由服务端在 frozen hierarchy build 中重新校验 chapter、Unicode code-point offsets 和 content hash；摘要、similarity、routing score 或聊天文本不能作为 citation。
4. owner、novel、candidate version 和 persisted reading cutoff 在候选选择、下钻、leaf expansion、rerank、cache 与最终 manifest 每一步生效；跨 scope 或 future evidence fail closed。
5. 对抗测试证明未读 arc 的标题、节点数量、分数、trace、cache key 和 source status 不泄露未来内容，且实验入口默认关闭、不改变现有 Reader Chat consumer output。

Plans:
- [x] 15-01 deterministic query router and visible candidate sets: local/arc/global/mixed intent, cutoff-first SQL/metadata filtering
- [x] 15-02 multi-level descent and leaf resolver: adaptive/collapsed candidates, raw fallback, server re-slice and frozen retrieval manifest
- [x] 15-03 routing audit and adversarial safety: owner/version/tenant-cache IDOR, future-metadata leakage and reader-chat no-cutover tests

**Waves:** 1 → 2 → 3 complete. Verified: `.planning/phases/15-adaptive-hierarchical-retrieval-and-leaf-evidence-safety/15-VERIFICATION.md` (59 targeted tests passed).

### Phase 16: Dependency-aware Local Rebuild and Carry-forward

**Goal:** 以可验证的依赖图和变更 oracle 计算 candidate 的最小安全 dirty closure，在边界不确定时保守扩散，并量化未变资产复用带来的调用与成本节省。
**Requirements:** V08-REUSE-01, V08-REUSE-02, V08-REUSE-03, V08-REUSE-04
**Depends on:** Phase 15
**Status:** COMPLETE — 16-VERIFICATION `status: passed`; Alembic `16memrebuild01`; 62 targeted tests
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 对 chapter edit、insert、delete、reorder 和 arc-boundary change fixture，系统可输出由 source/evidence → Chapter State → Arc/Volume → Global 构成的 dirty closure 及每个节点的失效原因。 VERIFIED
2. 未进入 dirty closure 的节点在新 candidate 中以 checksum-identical、lineage-valid 的方式 carry forward；验证证明它们不会产生 provider call、embedding 或重复索引写入。 VERIFIED
3. 当 arc 边界、跨章状态延续或 dependency lineage 无法证明稳定时，planner 会将重建范围扩大到受影响 arc/后缀和 Global，而不会保留可能 stale 的父节点。 VERIFIED
4. 重用报告同时给出 rebuilt/carried/stale counts、实际与避免的 calls/tokens/cost、dirty range 和 cache reuse；与 full rebuild upper bound 的口径可复核。 VERIFIED

Plans:
- [x] 16-01 dependency graph and change oracle: edit/insert/delete/reorder/boundary fixtures with deterministic dirty reasons
- [x] 16-02 checksum carry-forward and conservative propagation: no-change byte identity, stale-ref rejection and stage-only rebuild
- [x] 16-03 reuse economics report: avoided calls/tokens/cost, rebuild scope and PostgreSQL authority verification

### Phase 17: Frozen Single-book Qualification and Candidate Verdict

**Goal:** 使用冻结单书题集和同源 leaf baseline 独立验证结构、溯源、安全、检索质量、faithfulness、成本与复用收益，只产出 `qualified_candidate` 或 `blocked`，绝不执行 promotion。
**Requirements:** V08-QUAL-01, V08-QUAL-02, V08-QUAL-03, V08-QUAL-04, V08-QUAL-05
**Depends on:** Phase 12, Phase 13, Phase 14, Phase 15, Phase 16
**Status:** COMPLETE — 3/3 plans; independent verification passed (2026-07-16); Alembic head `17memqual01`
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 资格运行使用在查看候选结果前冻结的单书 source、policy 和问题集，且题目明确覆盖 local、跨章节/arc、whole-book/global、no-answer 和 spoiler 分桶。
2. hierarchical candidate 与 leaf/raw baseline 使用同一 source snapshot、reading cutoff、问题和预算口径；报告逐桶给出 leaf evidence recall/ranking、routing hit/fallback、answer faithfulness/relevance、p50/p95、calls/tokens/cost 和 reuse 指标。
3. fresh PostgreSQL verifier 独立重算结构、manifest、claim→leaf lineage、owner/snapshot scope、spoiler visibility 和所有 pointer before/after；任一断链、越界、泄漏、空必需指标或 pointer 变化都得到 `blocked`。
4. fixed-command qualification 只能返回 `qualified_candidate` 或 `blocked`，并保存 policy/fixture/source/prompt/schema/model/config hashes 与命令输出 digest；没有 endpoint、CLI 或 worker 路径会执行 promotion。
5. 最终报告明确说明这是单书 candidate 结论，不会替换 timeline、relationship、clue 或 Reader Chat，也不会宣称关闭 v0.3 的 100 confirmed、faithfulness/cost 全项目缺口。

Plans:
- [x] 17-01 frozen single-book fixture and policy: bucketed questions, no-answer/spoiler adversarial cases, same-source baseline and predeclared thresholds
- [x] 17-02 comparative evaluation and complete metrics: retrieval, routing, faithfulness, latency, cost, reuse and fallback reports
- [x] 17-03 independent PostgreSQL qualification authority: fixed commands, fresh observer, pointer-diff proof and candidate-only verdict

**Waves:** 1 → 2 → 3 complete. Verified: `.planning/phases/17-frozen-single-book-qualification-and-candidate-verdict/17-VERIFICATION.md` (63 targeted tests passed).

### Phase 18: Frontend Motion and Transition System

**Goal:** 在不改变业务行为、API 或数据结构的前提下，为现有 Next.js 界面建立克制、统一、可访问的动画过渡系统，并消除主题首帧闪烁、浮层退出不一致和动态内容布局跳动。
**Requirements:** UI-MOTION-01, UI-MOTION-02, UI-MOTION-03, UI-MOTION-04, UI-MOTION-05, UI-MOTION-06
**Depends on:** Existing frontend foundation (Phases 08–11); independent from the Phase 14–17 RAG execution chain
**Status:** COMPLETE — 18-01..03 verified 2026-07-16
**Plans:** 3/3 plans complete

**Success Criteria:**
1. 所有目标交互使用 150/200/300ms 语义 token，进入为 ease-out、退出为 ease-in；新增代码无任意时长、linear 或 `transition-all`。
2. sidebar、dialog、阅读设置、搜索、Reader Chat 与证据面板支持一致的触发切换、outside click、Escape、顶层关闭和焦点返回。
3. light/dark/custom 主题在首个可见帧前恢复；切换期间无整页闪烁、正文尺寸变化、固定控件漂移或自定义背景位移动画。
4. `prefers-reduced-motion` 下所有业务状态立即可用，非必要位移/缩放/脉冲被移除，loading/progress 同时提供文本或 ARIA 状态。
5. 桌面与 390px 触摸视口验证关键面板、主题、分析增量和布局边界；无水平滚动、输入框遮挡、底部进度覆盖聊天或焦点丢失。

Plans:
- [x] 18-01 motion tokens, reduced-motion contract, pre-paint theme bootstrap and shared primitives
- [x] 18-02 dismissable sidebar/settings/search/chat/evidence panels with topmost outside-click and focus restoration
- [x] 18-03 analysis progress/list/card transitions plus desktop/mobile Playwright motion qualification

**Scope note:** Phase 18 是独立的前端体验阶段，不属于 v0.8 candidate RAG 的生产切换，不新增后端、API、动画运行时依赖、滚动劫持或持续装饰动画。
