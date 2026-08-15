# Requirements: NovelMind

> Current authority snapshot: `master@912ca6b`, 2026-08-01.
> The current-status tables below supersede stale `PLANNED` labels in the legacy registry
> later in this file. Legacy IDs remain for traceability and are not deleted.

## Current Baseline Requirements

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-BASELINE-01 | `master` is the sole GSD execution baseline; old branches are evidence only | Phase 21 | VERIFIED |
| REQ-BASELINE-02 | Branch delta is classified as equivalent/missing/obsolete/reimplement with an explicit decision | Phase 21 | VERIFIED |
| REQ-NIGHTLY-01 | Scheduled failures have stable root classes and reproducible entry points | 22-G1 | IN_PROGRESS |
| REQ-NIGHTLY-02 | Nightly always produces an authority-safe artifact or an explicit dependency classification | 22-G2 | PLANNED |
| REQ-NIGHTLY-03 | Alerts deduplicate/resolve by root cause and Phase 22 requires 3 consecutive scheduled green runs | 22-G3 | BLOCKED |
| REQ-STATUS-01 | Report implementation, sample-data coverage and quality qualification independently | all | VERIFIED_CONTRACT |

## Agent Runtime Foundation

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-AGENT-01 | Agent runtime executes only registered NovelMind domain tools; default coding tools, shell, filesystem editing and arbitrary execution are disabled | 25.2-01 | PLANNED |
| REQ-AGENT-02 | Domain tools enforce owner, spoiler cutoff, budget, timeout, output limits, evidence lineage and stable error codes server-side | 25.2-02 | PLANNED |
| REQ-AGENT-03 | Skills are versioned with allowed-tools, budget and approval contracts; every official output persists as a lineage-bound Artifact; agent sessions are never fact sources | 25.2-03 | PLANNED |
| REQ-AGENT-04 | Agent workspace streams answers, previews Artifacts and requires explicit user approval before candidate publication | 25.2-04 | PLANNED |
| REQ-AGENT-05 | Third-party packages load only from a pinned allowlist and lock manifest with declared permissions; tool-name/schema collisions fail closed at startup | 25.3-01/02 | PLANNED |
| REQ-AGENT-06 | External MCP results are labeled `external_evidence` and never enter Canon, original-text evidence validation or core retrieval authority | 25.3-03 | PLANNED |
| REQ-AGENT-07 | High-impact actions route through allow/ask/deny policy with Web Approval; Original Canon mutation and active-pointer moves are deterministic deny operations | 25.3-04 | PLANNED |
| REQ-AGENT-08 | Model-produced structured outputs permit only conservative normalization, must pass strict post-repair validation, record repair lineage/warnings and never synthesize evidence or authority fields | 26-06 | PLANNED |

## v1.2 Trusted Understanding

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-QP-01 | Typed QueryPlan captures intent, dimensions, scope, cutoff, fallback and answer constraints | 26-01 | PLANNED |
| REQ-QP-02 | Retrieval covers raw, event/causal, character state/goal/motivation/knowledge, relations, timeline, clues, world entities/rules and NM chapter/arc/global | 26-02 | PLANNED |
| REQ-QP-03 | All factual citations materialize to hash/offset-verified leaf/raw EvidenceRef | 26-03 | PLANNED |
| REQ-QP-04 | Reader and Analysis Chat share retrieval/citation authority with distinct anchors | 26-04 | PLANNED |
| REQ-QP-05 | Missing domain readers follow an explicit deterministic fallback chain; heuristic extraction remains candidate-only and unresolved dimensions terminate as stable partial/unavailable reasons | 26-02 | PLANNED |
| REQ-WM-01 | Event facts and causal edges are typed, versioned and evidence-gated | 27-01 | PLANNED |
| REQ-WM-02 | Character state, goal, motivation and knowledge evolve by cutoff/POV | 27-02 | PLANNED |
| REQ-WM-03 | World entity, rule, faction, place and item projections retain exceptions and lineage | 27-03 | PLANNED |
| REQ-WM-04 | Canon Fact, Probable Inference, Literary Interpretation and User Interpretation never silently collapse | 27-04 | PLANNED |
| REQ-NM-01 | Every chapter reaches `completed|isolated|blocked`; no silent pending | 28-01..02 | PLANNED |
| REQ-NM-02 | Semantic Arc/Volume/Global candidates continuously cover the source snapshot | 28-03 | PLANNED |
| REQ-NM-03 | Timeline/relation/clue/character/world closure and one-click analysis expose dimension-specific progress | 28-04 | PLANNED |
| REQ-NM-04 | NM remains candidate-only; no active pointer or consumer cutover | Phase 28 | LOCKED |
| REQ-NM-05 | Chapter memory carries bounded previous context, non-authoritative next hints, continuity notes and non-indexed digests with source hash, cutoff and spoiler lineage | 28-02/05 | PLANNED |
| REQ-NM-06 | Outline and mainline summaries are uncertainty-bearing candidate Artifacts with source lineage and never become Canon by generation alone | 28-03 | PLANNED |
| REQ-QA-01 | Frozen reading QA spans local/cross-chapter/global/causal/knowledge/world/no-answer/spoiler buckets | 29-01 | PLANNED |
| REQ-QA-02 | Retrieval, citation, faithfulness, relevance, abstention, latency and cost are evaluated by bucket | 29-02 | PLANNED |
| REQ-QA-03 | Real browser UAT verifies citations, partial states and spoiler-safe Reader/Analysis Chat | 29-03 | PLANNED |

## v1.3 Visual Narrative

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-VIS-01 | Versioned Visual Bible links characters/places/items/style to evidence and interpretation labels | Phase 30 | PLANNED |
| REQ-VIS-02 | Key-scene selection records range, cast, place, time, POV, salience and diversity reasons | Phase 31 | PLANNED |
| REQ-VIS-03 | Provider-neutral Scene Spec compiles to versioned prompts without unsupported canon details | Phase 32 | PLANNED |
| REQ-VIS-04 | Illustration jobs are idempotent, budgeted, traceable and human-approved | Phase 33 | PLANNED |
| REQ-VIS-05 | Approved images use hash-verified text anchors and survive responsive reading/export | Phase 34 | PLANNED |
| REQ-VIS-06 | Speaker/dialogue textual heuristics expose offsets, confidence and warnings solely as scene-candidate recall/ranking signals, never as Canon or citation authority | 31-01/02 | PLANNED |

## v1.4 Canon Fork Derivatives

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-FORK-01 | Original Canon, User Interpretation and Fanfiction Canon have isolated authority/index/version rules | Phase 35 | PLANNED |
| REQ-FORK-02 | Derivative projects support owner-scoped planning, Markdown editing, autosave, history, diff and rollback | Phase 36 | PLANNED |
| REQ-FORK-03 | Generation consumes an auditable cutoff state package and runs contradiction/character/timeline/clue checks | Phase 37 | PLANNED |
| REQ-FORK-04 | Derivative Visual Bible/assets cannot mutate original visual authority | Phase 38 | PLANNED |
| REQ-FORK-05 | Export preserves content/assets/citations/version parity and passes end-to-end UAT/audit | Phase 39 | PLANNED |
| REQ-FORK-06 | Branch suggestions are disabled-by-default candidate outputs bound to conflict, Canon delta and evidence; they cannot auto-fork or reuse divergence/publication approval | 37-03/05 | PLANNED |

## v1.5 Windows Desktop Runtime

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-DESK-01 | A Windows Electron application hosts all existing NovelMind routes and verified user workflows without a parallel UI rewrite | Phase 41–42 | PLANNED |
| REQ-DESK-02 | The renderer is sandboxed with context isolation, no Node integration, restrictive CSP/navigation/window policies and sender-validated capability-specific IPC | Phase 42 | PLANNED |
| REQ-DESK-03 | A small `DesktopRuntime` interface deterministically starts, observes, restarts and shuts down the local Next, FastAPI, Agent Service and persistence/vector process graph | Phase 43 | PLANNED |
| REQ-DESK-04 | The installed application requires no Docker and no user-installed Node, Python, PostgreSQL or vector-service runtime | Phase 41/43/45 | PLANNED |
| REQ-DESK-05 | Mutable application data, logs and backups live under a versioned `%APPDATA%/NovelMind` layout and survive compatible upgrade/uninstall paths | Phase 43–45 | PLANNED |
| REQ-DESK-06 | Runtime endpoints and local authentication are injected at startup without fixed-port assumptions; credentials leave renderer storage and use OS-backed protection | Phase 44 | PLANNED |
| REQ-DESK-07 | Startup, migration, dependency, port, crash and provider failures are visible, recoverable and never reported as successful empty states | Phase 43–45 | PLANNED |
| REQ-DESK-08 | Provider-independent reading/editing/local-data workflows start without internet; provider-dependent actions expose honest unavailable/blocked states | Phase 44–45 | PLANNED |
| REQ-DESK-09 | Windows installation provides a single-instance application, clean process-tree shutdown, no terminal windows and a reversible versioned upgrade path | Phase 45 | PLANNED |
| REQ-DESK-10 | Release qualification covers Electron integration, clean-VM install, first run, all existing critical workflows, IPC/security negatives, crash recovery and data preservation | Phase 45 | PLANNED |

## v1.6 Provider Protocol Unification

| ID | Requirement | Owner | Status |
|---|---|---|---|
| REQ-PROVIDER-01 | OpenAI、Anthropic、Gemini、Ollama 与自定义 OpenAI-compatible 使用同一版本化 provider profile/adapter 权威；模型目录分页、能力过滤、ID 规范化和创建/更新校验不得漂移 | Phase 46-01 | PLANNED |
| REQ-PROVIDER-02 | Pi/Agent 网关与所有后端文本生成消费者只从当前 owner 的有效 `AIModelConfig` 解析 deployment；缺少、歧义、禁用或不安全配置时 fail closed，遗留硬编码模型池不再参与运行选择 | Phase 46-02 | PLANNED |
| REQ-PROVIDER-03 | 五类供应商分别通过凭据隔离的目录发现、连接测试和 Pi 真实运行资格矩阵；没有可用凭据或服务时必须记录 BLOCKED/PARTIAL，不得以 mock 或单元测试冒充真实联通 | Phase 46-03 | PLANNED |
| REQ-PROVIDER-04 | 每次实际调用保留 owner、provider、model、协议/能力、token、延迟、错误与可核验成本血缘；设置页只展示后端权威能力和诚实状态，未知价格/能力不得推测 | Phase 46-04 | PLANNED |

## Explicit Backlog

| ID | Requirement | Status |
|---|---|---|
| REQ-999-PROMOTION | NM active pointer, production A/B, promotion and rollback | DEFERRED — explicit authorization required |
| REQ-999-READER | Branch-only bookmarks/navigation/performance not needed by Phase 26–39 | BACKLOG — selective reimplementation only |
| REQ-999-EMOTIONAL-MEMORY | Long-term emotional-memory projection inspired by AgentVN | DEFERRED — requires a separate epistemic schema, evaluation set and phase authorization |

## Requirements Status

| ID | Requirement | Priority | Status |
|---|---|---|---|
| REQ-AUDIT-01 | 以 VERIFIED / PARTIAL / MISSING 建立实际实现基线 | P0 | VERIFIED |
| REQ-AUDIT-02 | 修复启动级前后端契约 | P0 | VERIFIED |
| REQ-AUDIT-03 | 未实现核心端点不得伪装成功 | P0 | VERIFIED |
| REQ-AUDIT-04 | 建立可重复 smoke/test/build 命令 | P0 | VERIFIED |
| REQ-AUDIT-05 | 文档状态与代码现实一致 | P0 | VERIFIED |
| REQ-SEC-01 | Git 忽略敏感配置、依赖、构建和上传数据 | P0 | VERIFIED |
| REQ-SEC-02 | 上传文件限制在根目录并具备失败补偿 | P0 | VERIFIED |
| REQ-SEC-03 | API 建立身份和资源授权边界 | P0 | VERIFIED |
| REQ-SEC-04 | 自定义模型 URL 阻断 SSRF，错误对外脱敏 | P0 | VERIFIED |
| REQ-SEC-05 | provider key 加密存储并支持旧密钥轮换 | P1 | VERIFIED |
| REQ-DATA-01 | ORM/Alembic 对齐并在 PostgreSQL 执行检查 | P0 | VERIFIED |
| REQ-ARCH-01 | reader 动态路由和章节响应模型正确 | P1 | VERIFIED |
| REQ-ARCH-02 | 导入进度使用持久化、并发安全的 job 模型 | P1 | VERIFIED |
| REQ-CI-01 | CI 覆盖默认分支，检查非交互且依赖风险受控 | P1 | VERIFIED |
| REQ-RAG-01 | 小说上传后可完成分块、embedding 与 Chroma 索引 | P0 | VERIFIED |
| REQ-RAG-02 | 混合搜索返回 owner 隔离的章节、chunk、score 与 evidence | P0 | VERIFIED |
| REQ-RAG-03 | 前端提供全局搜索与阅读页内搜索入口 | P1 | VERIFIED |
| REQ-EVAL-01 | 评测数据/API/CLI/可视化基础设施可重复运行 | P1 | VERIFIED |
| REQ-EVAL-02 | 形成 100 条人工确认的高质量评测题 | P1 | MISSING |
| REQ-EVAL-03 | 产出有效 baseline/hybrid 质量基线及完整指标 | P1 | PARTIAL |
| REQ-KG-01 | LLM 只做语义分析和判断，脚本负责候选召回、证据包、规则、阈值、状态机和写库 | P1 | VERIFIED |
| REQ-KG-02 | 所有候选、判断、接受关系和图边必须可追溯到真实 evidence refs | P1 | VERIFIED |
| REQ-KG-03 | 向量/BM25/邻近关系只能作为候选召回信号，不能直接写成图事实 | P1 | VERIFIED |
| REQ-KG-04 | 同一图谱链路支持小说与历史两类语料，通过 domain/ontology profile 区分 | P1 | VERIFIED |
| REQ-KG-05 | PostgreSQL 是候选、判断、证据和接受状态的事实源；Neo4j 仅作为可重放投影 | P2 | VERIFIED |
| REQ-KG-06 | 图谱链路具备 fixture/eval/成本/延迟/faithfulness 验证，不依赖人工凭感觉验收 | P1 | VERIFIED |
| REQ-NU-01 | accepted judgments 只能经版本化 draft/canonical 流水线生成 narrative units，禁止在 gate 中直接发布 | P0 | VERIFIED |
| REQ-NU-02 | 每个 narrative unit 必须保留 owner、work、domain、source judgment 和真实 evidence refs 的完整 lineage | P0 | VERIFIED |
| REQ-NU-03 | canonicalization 显式处理重复、冲突、时态与 deprecated 状态，hard-negative 错误合并为零 | P0 | VERIFIED |
| REQ-NU-04 | Chroma candidate collection 不可变且可重建，PostgreSQL 保存 build、版本、pointer 和发布审计事实 | P0 | VERIFIED |
| REQ-NU-05 | 检索支持 units、chunks 和混合模式；默认切换前 raw chunks 始终作为 fallback | P1 | VERIFIED |
| REQ-NU-06 | candidate 必须通过 fiction/history frozen A/B、faithfulness、延迟和 canary 门禁才能 promote | P0 | VERIFIED |
| REQ-NU-07 | promotion 使用 prepare/commit journal，失败可联合回滚 DB、collection、pointer 和 manifest | P0 | VERIFIED |
| REQ-NU-08 | 增量刷新只处理受影响 evidence/subjects，删除与失效零残留，无变化时 LLM/index 写入为零 | P1 | VERIFIED |
| REQ-AUTO-01 | 测试必须显式分类为 unit、integration、contract、browser 或 live，禁止默认 marker 隐式排除关键链路 | P0 | VERIFIED |
| REQ-AUTO-02 | 后端、前端和修改代码具备版本化 coverage、timeout、flake、JUnit 与 artifact 门禁 | P0 | VERIFIED |
| REQ-AUTO-03 | PostgreSQL 16 自动验证迁移、tsvector、约束、事务、并发和历史版本升级 | P0 | VERIFIED |
| REQ-AUTO-04 | 固定版本 Chroma 具备健康检查、契约、故障注入、恢复和 DB/collection 一致性验证 | P0 | VERIFIED |
| REQ-AUTO-05 | RAG fixture 从冻结源证据自动生成，经不同模型族的 Generator/Judge 与确定性 arbiter 自动取得资格，不依赖人工 confirmed | P0 | VERIFIED |
| REQ-AUTO-06 | RAG 自动评测覆盖 faithfulness、answer relevance、context precision/recall、重复性、漂移、成本和延迟，所有依赖故障 fail closed | P0 | VERIFIED |
| REQ-AUTO-07 | Judge revision 必须先通过独立 calibration，critical false-accept 为零且一致率达标 | P0 | VERIFIED |
| REQ-AUTO-08 | OpenAPI、前端 consumer、组件和双 viewport Playwright 覆盖核心用户流程、权限和错误状态 | P1 | VERIFIED |
| REQ-AUTO-09 | CI 收敛为 secretless PR、main integration、受控 nightly 三层 DAG，并以唯一 ci-gate 作为 required check | P0 | VERIFIED |
| REQ-AUTO-10 | baseline promotion、分支保护、告警、权限、制品、可靠性和回归证据均自动化且可审计 | P1 | VERIFIED |
| REQ-AUTO-11 | 每份可比较 RAG 质量报告必须绑定 chunker 名称、版本、配置 hash、chunk manifest hash 与父级 source snapshot | P0 | VERIFIED |
| REQ-CHUNK-01 | 分块策略以版本化 manifest 表示，原始 chunk、scene 和 evidence chunk 均可回链到章节与原文 offset | P0 | VERIFIED |
| REQ-CHUNK-02 | 规则式初切必须产生边界置信度和原因码，低置信边界才允许进入 LLM 判断队列 | P0 | VERIFIED |
| REQ-CHUNK-03 | LLM 只输出严格 schema 的边界判断与上下文保留建议，脚本负责长度、重叠、证据和写库约束 | P0 | VERIFIED |
| REQ-CHUNK-04 | 系统支持 chapter → scene → evidence 的层级检索表示，并保持 chunks 原始证据回退 | P0 | VERIFIED |
| REQ-CHUNK-05 | 新 chunker 先构建 immutable candidate，不移动 active index；仅通过 Phase 06 质量门才可 promotion | P0 | VERIFIED |
| REQ-CHUNK-06 | 源章节或 chunker 变化只重切受影响范围，可恢复、可回滚且不残留旧向量 | P0 | VERIFIED |
| REQ-CHUNK-07 | LLM 不可用、schema 非法或预算耗尽时回退到规则切片，并在 lineage 中明确标记 fallback | P1 | VERIFIED |
| REQ-CHUNK-08 | chunker A/B 必须用同一冻结语料、同一质量 policy 和 Phase 06 自动评测比较质量、成本和延迟 | P0 | VERIFIED |
| REQ-TIME-01 | 小说分析使用可恢复、可取消、可渐进展示的持久后台任务；章节结果完成即发布，失败后按 checkpoint 续跑 | P0 | VERIFIED |
| REQ-TIME-02 | 分析产物按 source snapshot、prompt/schema/model 版本化，新版本完整验证后切换 active，人工修正独立保留且可回滚 | P0 | VERIFIED |
| REQ-TIME-03 | 导入后只自动执行切片、场景层级和低成本结构分析；时间线深度分析在首次进入分析页时启动 | P0 | VERIFIED |
| REQ-TIME-04 | 时间事件同时保存故事内顺序与章节叙述顺序，支持明确、相对、模糊、未知四种时间精度及推断证据 | P0 | VERIFIED |
| REQ-TIME-05 | 时间事件自动发布并绑定章节、原文 offsets/evidence refs、置信度、模型 lineage；人工编辑后不得被重分析覆盖 | P0 | VERIFIED |
| REQ-TIME-06 | 时间线支持人物筛选、故事时间/叙述顺序切换，以及按需显示导致、触发、回应、阻断因果关系 | P1 | VERIFIED |
| REQ-TIME-07 | 全局分析页先选择小说，以可缩放横向时间轴渐进展示事件、进度、错误和最后更新时间 | P0 | VERIFIED |
| REQ-TIME-08 | 默认按阅读进度隐藏未来事件，用户显式切换全书分析后才能查看未读章节内容 | P0 | VERIFIED |
| REQ-TIME-09 | LLM 使用章节级低成本抽取与跨章节高质量归并的分级路由，并执行单书 token/费用/调用预算和确定性暂停 | P0 | VERIFIED |
| REQ-TIME-10 | Phase 08 仅支持小说时间线；人物关系图、阅读 AI、线索伏笔与历史文本支持明确延期或移除 | P1 | VERIFIED |
| REQ-REL-01 | 人物关系图只投影经 evidence/threshold/conflict 门控接受的小说人物关系，任何候选、LLM 判断和图边均保留来源与版本 lineage | P0 | PLANNED |
| REQ-REL-02 | 人物关系以追加式观察和有效叙事区间保存，用户可按章节/阅读进度查看关系随故事推进的变化而不覆盖历史状态 | P0 | PLANNED |
| REQ-REL-03 | 人物关系 API 按 owner、novel、analysis version 和阅读进度过滤；默认不会返回未读章节的节点、边、标签或统计信息 | P0 | PLANNED |
| REQ-REL-04 | 分析工作台提供可缩放、可筛选、可定位证据的动态人物关系图；用户可按人物、关系类型和叙事位置查看子图 | P1 | PLANNED |
| REQ-REL-05 | 人工对人物合并、关系类型、有效区间的修正作为保护性 override 保存，后续分析不得静默覆盖 | P1 | PLANNED |
| REQ-REL-06 | 人物图构建、投影、缓存和前端交互具备版本化评测、owner/spoiler/adversarial 与浏览器验证；Neo4j 仅为可重放投影 | P1 | PLANNED |
| REQ-CHAT-01 | 阅读器选区必须以 chapter/source offsets/evidence refs 持久化，AI 只能引用选区与经权限和剧透边界过滤的上下文 | P0 | PLANNED |
| REQ-CHAT-02 | 用户可在每本小说下创建、重命名、切换、归档和删除多个 owner-scoped 持久会话，消息顺序和引用可重放 | P0 | PLANNED |
| REQ-CHAT-03 | 默认聊天严格按阅读进度防剧透；全书上下文只能使用既有的明确 per-novel 全书开关，且响应必须标注可核验来源 | P0 | PLANNED |
| REQ-CHAT-04 | LLM 仅产生受限回答、澄清问题和结构化建议；它不得直接修改时间线、人物关系或线索事实，任何写入都需用户确认的显式动作 | P0 | PLANNED |
| REQ-CHAT-05 | 会话调用保存模型/prompt/context/usage/成本/失败状态并执行每会话与每小说预算；依赖、预算或校验失败必须可恢复且 fail closed | P0 | PLANNED |
| REQ-CHAT-06 | 阅读器提供不遮挡正文的选区入口与可收起小窗；会话历史、引用跳转、加载、取消和错误状态在桌面与移动端可用 | P1 | PLANNED |
| REQ-CHAT-07 | 选区、权限、剧透、引用完整性、并发会话、取消恢复及真实浏览器链路均有自动化验证 | P1 | PLANNED |
| REQ-CLUE-01 | 线索候选只能由脚本构造的跨章节证据包触发，LLM 只做语义判断；缺少早期线索、后续证据或可追溯引用时不得发布 | P0 | PLANNED |
| REQ-CLUE-02 | 每条线索以 candidate、active、reinforced、paid_off、dismissed 五状态追加式演化，并保存首现、强化、回收和人工处置的版本 lineage | P0 | PLANNED |
| REQ-CLUE-03 | 线索可关联人物、时间事件和关系观察，但这些关联必须是证据引用，不得把相似度或聊天内容直接写为事实 | P0 | PLANNED |
| REQ-CLUE-04 | 默认线索视图和 API 按阅读进度隐藏未来伏笔、强化和回收；全书披露沿用明确的 per-novel 开关 | P0 | PLANNED |
| REQ-CLUE-05 | 用户可确认、驳回、补充说明或调整线索关联；人工决策形成保护性 override，重分析仅产生需对比的新版本 | P1 | PLANNED |
| REQ-CLUE-06 | 分析工作台提供线索时间带、证据面板、状态筛选和回收链路，而非把后端剧情摘要中间结果暴露成新菜单 | P1 | PLANNED |
| REQ-CLUE-07 | 冻结小说 fixture、对抗性假阳性/剧透/跨版本测试、成本延迟和浏览器验证共同构成发布门禁 | P1 | PLANNED |
| V08-AUDIT-01 | 运维者可按资产与版本生成只读的层级记忆候选资格报告 | P0 | VERIFIED |
| V08-AUDIT-02 | 报告将现有资产明确分类为 reusable_exact、rebuild_required、blocked 或 optional_unavailable | P0 | VERIFIED |
| V08-AUDIT-03 | Phase 07 层级资产无效时必须在任何 provider 调用前阻断构建 | P0 | VERIFIED |
| V08-AUDIT-04 | 资格审计不得调用模型、修复数据或写入任何 active pointer | P0 | VERIFIED |
| V08-MEM-01 | Chapter State、连续 Story Arc/Volume 与 Global Story Model 使用独立 immutable candidate version | P0 | VERIFIED |
| V08-MEM-02 | 上层状态与主张使用严格类型 schema；自由文本不得充当事实权威 | P0 | VERIFIED |
| V08-MEM-03 | 每条上层主张都必须解析到同一 source snapshot 的 leaf evidence | P0 | VERIFIED |
| V08-MEM-04 | 节点、边、来源链接与 manifest 均保存 owner、novel、version、checksum 与 model lineage | P0 | VERIFIED |
| V08-MEM-05 | v0.8 不创建或切换生产 active pointer | P0 | VERIFIED |
| V08-BUILD-01 | 候选构建严格按 Chapter State → Story Arc/Volume → Global Story Model 自底向上执行 | P0 | VERIFIED |
| V08-BUILD-02 | 构建支持预算、checkpoint、取消、恢复与 exact-cache reuse | P0 | VERIFIED |
| V08-BUILD-03 | 章节失败只阻断受影响 arc/global，不触发整本无条件重启 | P0 | VERIFIED |
| V08-BUILD-04 | 时间线、人物关系和线索只作为可追溯的可选增强信号 | P1 | VERIFIED |
| V08-BUILD-05 | Reader Chat 永远不得作为层级记忆事实来源 | P0 | VERIFIED |
| V08-RETR-01 | 检索可根据问题选择 local、arc、global 或 mixed 起始层 | P0 | VERIFIED |
| V08-RETR-02 | 检索支持逐层下钻并在上层不可用时回退 leaf/raw evidence | P0 | VERIFIED |
| V08-RETR-03 | 最终引用只允许使用重新校验过 offset/hash 的原文证据 | P0 | VERIFIED |
| V08-RETR-04 | owner、novel、version 与 spoiler 边界必须在每个检索步骤生效 | P0 | VERIFIED |
| V08-RETR-05 | 路由过程可审计且不得通过路由元数据泄露未来内容 | P0 | VERIFIED |
| V08-REUSE-01 | 系统通过依赖图计算变更后的 dirty closure | P0 | VERIFIED |
| V08-REUSE-02 | 未变化节点以 checksum-identical 方式 carry forward | P0 | VERIFIED |
| V08-REUSE-03 | arc 边界不确定时保守扩大重建范围 | P1 | VERIFIED |
| V08-REUSE-04 | 重用报告量化避免的调用、token 与成本 | P1 | VERIFIED |
| V08-QUAL-01 | 单书冻结题集覆盖 local、跨章节、global、no-answer 与 spoiler 场景 | P0 | VERIFIED |
| V08-QUAL-02 | 候选与 leaf baseline 在同一 source、cutoff 与预算下比较 | P0 | VERIFIED |
| V08-QUAL-03 | lineage、owner、snapshot、spoiler 或 pointer 任一违规都必须阻断资格 | P0 | VERIFIED |
| V08-QUAL-04 | 报告覆盖质量、faithfulness、延迟、成本、reuse 与 fallback 指标 | P0 | VERIFIED |
| V08-QUAL-05 | 最终结论仅允许 qualified_candidate 或 blocked，且不得执行 promotion | P0 | VERIFIED |
| UI-MOTION-01 | 全站交互动效使用统一的 150/200/300ms token、方向 easing 与 reduced-motion 规则 | P1 | VERIFIED |
| UI-MOTION-02 | Sidebar、设置、搜索、Chat 与证据浮层具有一致的打开、关闭、outside-click、Escape 与焦点返回语义 | P1 | VERIFIED |
| UI-MOTION-03 | 页面内容、分析进度、列表和卡片状态过渡不得引起布局跳动或重复播放 | P1 | VERIFIED |
| UI-MOTION-04 | Light、dark 与 custom 主题在首帧前恢复，切换无 hydration 闪烁或正文尺寸变化 | P1 | VERIFIED |
| UI-MOTION-05 | 动效不改变 API、数据、导航、阅读、分析或权限行为，且不新增动画运行时依赖 | P0 | VERIFIED |
| UI-MOTION-06 | 桌面与 390px 触摸视口自动验证键盘、触摸、reduced-motion、无遮挡和无水平滚动 | P1 | VERIFIED |
| REQ-BASE-01 | 规划权威与代码一致：phase21 分支工作被 GSD 追认，STATE/IMPLEMENTATION-STATUS/ROADMAP 反映真实 Alembic head 与测试规模 | P0 | PLANNED |
| REQ-BASE-02 | 状态文档带统一快照标识（commit、日期、DB fingerprint 或未连接声明），文档漂移可检出 | P0 | PLANNED |
| REQ-BASE-03 | master CI 全绿：Ruff、integration、Browser smoke、ci-gate 聚合、CodeQL 全部通过且连续 3 个 nightly 稳定 | P0 | PLANNED |
| REQ-BASE-04 | ci-gate 作为 required check 实际阻止红色合入，带红合并事故有根因记录 | P0 | PLANNED |
| REQ-GOV-01 | 唯一 Layer Registry ADR（S/D/R/A 四命名空间）被 PROJECT、REQUIREMENTS、API schema 和代码共同引用；新代码禁用裸 L0-L6 | P0 | PLANNED |
| REQ-GOV-02 | Narrative Unit 与 Narrative Memory 的用途、边界、替代关系和消费顺序由 ADR 固定 | P0 | PLANNED |
| REQ-GOV-03 | chunk_level / semantic_level / release_status 字段语义分离，facet 禁止无证据反写主结构且有契约测试 | P1 | PLANNED |
| REQ-GOV-04 | raw TextChunk→Chroma 双写具备 journal、幂等键、完成标记与索引完整性 gate；部分失败 fail-closed，不得静默置 ready | P0 | PLANNED |
| REQ-GOV-05 | raw chunk / Narrative Unit / NM 三层检索由服务端统一 router 决策，降级顺序与 citation 层规则固定，Reader Chat 优先级并入同一契约 | P0 | PLANNED |
| REQ-GOV-06 | relationship 观察携带 intake_kind / producer_kind 来源枚举并贯穿 API/UI | P1 | PLANNED |
| REQ-GOV-07 | 所有 LLM 调用按 price snapshot × usage 真实结算 cost_usd（clue 链路对齐 NM/timeline/reader_chat 既有实现） | P1 | PLANNED |
| REQ-GOV-08 | 暴露的 API 契约诚实：characters 双轨收口、analyze/stream 删除或实现、fanfiction 显式 deferred，禁止占位空数组或长期 501 | P1 | PLANNED |
| REQ-ACHAT-01 | 分析页对话与阅读器聊天共享同一检索/引用/预算底座与同一剧透边界（默认 cutoff 内，显式全书开关后才全书），仅锚点不同 | P0 | PLANNED |
| REQ-ACHAT-02 | `/analysis` 默认对话视图，可视化为可切换视图，切换不丢会话与结构选中状态 | P1 | PLANNED |
| REQ-ACHAT-03 | 分析页对话为 owner-scoped 持久多会话，回答带服务端重验的叶子原文 citation，可注入结构范围上下文 | P0 | PLANNED |
| REQ-BOOK-01 | 样例长篇全部章节进入明确终态（completed 或带 reason code 的显式隔离），无静默 pending | P0 | PLANNED |
| REQ-BOOK-02 | Arc/Volume 覆盖全部连续范围且 Global Story Model 生成，manifest 可由 DB 重算；全程 candidate-only | P0 | PLANNED |
| REQ-BOOK-03 | 单章失败只阻断所属 Arc 在真实长篇上验证，构建报告含 calls/tokens/cost/cache/来源状态 | P0 | PLANNED |
| REQ-BOOK-04 | 结构工作台在真实 Arc/Global 数据上联动，facet 范围正确 | P1 | PLANNED |
| REQ-BOOK-05 | Reader Chat 在 NM 不可用或 partial 时 fallback 正确且引用回落原文（实测） | P0 | PLANNED |
| REQ-SEM-01 | 时间线存在证据门控的因果边（caused/triggered/responded/blocked）且抽样人工核验通过 | P0 | PLANNED |
| REQ-SEM-02 | 人物关系存在真实 change/end 演化观察，有效区间生效并可视 | P0 | PLANNED |
| REQ-SEM-03 | 线索存在完整 cue→reinforce→payoff/dismissed 生产链，payoff_chapter > 0 且标题可读 | P0 | PLANNED |
| REQ-QUAL-06 | 每份评测报告绑定 DB fingerprint、dataset version、source snapshot 与 commit | P0 | PLANNED |
| REQ-QUAL-07 | NM 真实候选通过冻结单书 qualification（全桶），结论为 qualified_candidate 或 blocked 并归档 | P0 | PLANNED |
| REQ-PROM-01 | NM promotion 具备 Active Pointer 唯一权威、CAS、before/after manifest 与 rollback journal（立项需新授权） | P0 | PLANNED |
| REQ-PROM-02 | 生产切换只能由同源冻结 A/B 达标触发，阈值预先声明 | P0 | PLANNED |
| REQ-PROM-03 | A/B 不达标时保持 candidate-only 并归档 blocked 原因，作为合法退出 | P0 | PLANNED |
| REQ-CRE-01 | Original Canon / User Interpretation / Fanfiction Canon 三空间具有独立 authority、namespace、version 与 citation 规则 | P0 | PLANNED |
| REQ-CRE-02 | 创作内容不得进入原作检索索引、评测语料或 facet 生产链（负向测试证明） | P0 | PLANNED |
| REQ-CRE-03 | 创作项目模型：owner 隔离 CRUD、章节规划、Markdown 编辑与自动保存 | P0 | PLANNED |
| REQ-CRE-04 | 创作版本历史可追溯、可 diff、可回滚 | P0 | PLANNED |
| REQ-CRE-05 | 续写上下文包注入指定 cutoff 的人物/世界状态、时间线因果、未回收伏笔与证据引用，包内容可审计 | P0 | PLANNED |
| REQ-CRE-06 | 创作一致性评测：人物行为/既定事实/时间线矛盾自动检查 + 冻结样例集门禁；偏离 override 显式记录且不回写原作空间 | P0 | PLANNED |
| REQ-CRE-07 | 创作作品可导出 Markdown/EPUB，内容与版本一致 | P1 | PLANNED |
| REQ-SHIP-01 | 生产部署基线：TLS、密钥管理、备份、监控与成本告警核对通过 | P1 | PLANNED |
| REQ-SHIP-02 | 最终审计三维度（implementation/data/quality）全绿，Target C 达标条件逐项核销 | P0 | PLANNED |

## v0.8 Scope Boundaries

- 不对现有小说执行全量重分析；先以单书 dry-run 证明候选链路。
- 不创建或切换生产 active pointer，不改变时间线、人物关系、线索或 Reader Chat 的生产消费者。
- 不新增产品 UI，也不引入 GraphRAG、RAPTOR、Neo4j、LangChain 等生产依赖。
- 上层摘要只用于路由与组织，永远不能替代最终原文证据。

## Traceability

| Requirement | Plan | Evidence |
|---|---|---|
| REQ-SEC-01, REQ-SEC-02 | 02-01 | `.gitignore` checks; upload/delete rollback tests |
| REQ-SEC-03..05 | 02-02 | auth/ownership/SSRF/crypto tests |
| REQ-DATA-01, REQ-ARCH-01, REQ-CI-01 | 02-03 completed slices | PostgreSQL migration, Next build, CI and audits |
| REQ-ARCH-02 | 02-03 completed slice | ImportJob table、租约并发、幂等、取消和重启恢复测试 |
| REQ-RAG-01..03 | v0.3 core/search/frontend | 12 RAG e2e；hybrid search tests；前端 lint/test/build |
| REQ-EVAL-01 | 03-01 | `03-01-VERIFICATION.md`；eval API owner regression tests |
| REQ-EVAL-02..03 | 03-01 | 当前 10 confirmed / 90 candidate；6 次运行检索指标为 0 |
| REQ-KG-01..06 | 04-01..04-04 completed | `.planning/phases/04-llm/04-*-SUMMARY.md`; `backend/tests/test_knowledge_eval.py`; knowledge graph gate/projection tests |
| REQ-NU-01..08 | 05-01..05-05 completed | `.planning/phases/05-narrative-knowledge-unit-layer/05-VERIFICATION.md`; all 8 requirements independently verified |
| REQ-AUTO-01..10 | 06-01..06-07 completed | `.planning/phases/06-automated-quality-ci/06-VERIFICATION.md` |
| REQ-AUTO-11 | 06-08..06-09 completed | `06-08/06-09-SUMMARY.md`; `QualityRun` + `BaselineCandidate`; Alembic `07qualityruns01` / `08baselinecand01` |
| REQ-CHUNK-01..03,07,08 | 07-01..03,07-06 completed | `07-VERIFICATION.md`; `backend/app/services/chunking/*`; 88 related tests |
| REQ-CHUNK-04..06 | 07-04..05 + PG wiring | `chunk_builds` / `chunk_hierarchy_nodes` / active pointer; `indexing_service` + `hybrid_search` hooks; `test_pg_hierarchy_wiring.py` |
| REQ-TIME-01..10 | 08 implemented; qualification passed | `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-QUALIFICATION.md` |
| V08-AUDIT-01..04 | Phase 12 | `12-VERIFICATION.md` passed；read-only eligibility；provider/pointer negative tests |
| V08-MEM-01..05 | Phase 13 | `13-VERIFICATION.md` passed；strict candidate contracts；lineage and no-pointer invariants |
| V08-BUILD-01..05 | Phase 14 | `14-VERIFICATION.md` passed；bottom-up builder；checkpoint/cache/failure-scope tests |
| V08-RETR-01..05 | Phase 15 | `15-VERIFICATION.md` passed；routing/down-drill；raw citation and spoiler adversarial tests |
| V08-REUSE-01..04 | Phase 16 | `16-VERIFICATION.md` passed；dependency dirty closure；checksum carry-forward report |
| V08-QUAL-01..05 | Phase 17 | `17-VERIFICATION.md` passed；frozen single-book comparative qualification；candidate-only verdict |
| UI-MOTION-01..06 | Phase 18 | `18-VERIFICATION.md` passed；motion tokens；dismissable surfaces；theme boot；responsive accessibility |

## Current Evidence

- **v0.8 audit:** `.planning/v0.8-MILESTONE-AUDIT.md` — verdict `achieved_candidate_scope` (2026-07-16).
- Phase 12–17 narrative-memory chain VERIFIED candidate-only; Alembic head `17memqual01`.
- Phase 18 frontend motion VERIFIED (independent UX track).
- Phase 06 quality durable jobs + baseline prepare/commit + cross-chunker reports delivered.
- Phase 07 chunking pipeline packages under `backend/app/services/chunking/` with SUMMARYs 07-01..07-06.
- Related automated suite: **88 passed** (`unit/integration chunking` + adversarial + legacy `test_chunking`).
- Import progress is persisted through `ImportJob`; lease control, retry, cancellation and restart recovery are verified.
- Phase 04 knowledge graph fixture eval has 20 labeled fiction/history examples and deterministic offline tests.
- Phase 07 residual: promote hierarchy/build stores from in-memory contracts to PostgreSQL + production retrieval wiring.
- Branch may still carry local BGE/reader UX WIP outside Phase 06/07 plan commits.
