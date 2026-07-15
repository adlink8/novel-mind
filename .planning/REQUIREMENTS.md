# Requirements: 安全与架构修复

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
| V08-AUDIT-01 | 运维者可按资产与版本生成只读的层级记忆候选资格报告 | P0 | PLANNED |
| V08-AUDIT-02 | 报告将现有资产明确分类为 reusable_exact、rebuild_required、blocked 或 optional_unavailable | P0 | PLANNED |
| V08-AUDIT-03 | Phase 07 层级资产无效时必须在任何 provider 调用前阻断构建 | P0 | PLANNED |
| V08-AUDIT-04 | 资格审计不得调用模型、修复数据或写入任何 active pointer | P0 | PLANNED |
| V08-MEM-01 | Chapter State、连续 Story Arc/Volume 与 Global Story Model 使用独立 immutable candidate version | P0 | PLANNED |
| V08-MEM-02 | 上层状态与主张使用严格类型 schema；自由文本不得充当事实权威 | P0 | PLANNED |
| V08-MEM-03 | 每条上层主张都必须解析到同一 source snapshot 的 leaf evidence | P0 | PLANNED |
| V08-MEM-04 | 节点、边、来源链接与 manifest 均保存 owner、novel、version、checksum 与 model lineage | P0 | PLANNED |
| V08-MEM-05 | v0.8 不创建或切换生产 active pointer | P0 | PLANNED |
| V08-BUILD-01 | 候选构建严格按 Chapter State → Story Arc/Volume → Global Story Model 自底向上执行 | P0 | PLANNED |
| V08-BUILD-02 | 构建支持预算、checkpoint、取消、恢复与 exact-cache reuse | P0 | PLANNED |
| V08-BUILD-03 | 章节失败只阻断受影响 arc/global，不触发整本无条件重启 | P0 | PLANNED |
| V08-BUILD-04 | 时间线、人物关系和线索只作为可追溯的可选增强信号 | P1 | PLANNED |
| V08-BUILD-05 | Reader Chat 永远不得作为层级记忆事实来源 | P0 | PLANNED |
| V08-RETR-01 | 检索可根据问题选择 local、arc、global 或 mixed 起始层 | P0 | PLANNED |
| V08-RETR-02 | 检索支持逐层下钻并在上层不可用时回退 leaf/raw evidence | P0 | PLANNED |
| V08-RETR-03 | 最终引用只允许使用重新校验过 offset/hash 的原文证据 | P0 | PLANNED |
| V08-RETR-04 | owner、novel、version 与 spoiler 边界必须在每个检索步骤生效 | P0 | PLANNED |
| V08-RETR-05 | 路由过程可审计且不得通过路由元数据泄露未来内容 | P0 | PLANNED |
| V08-REUSE-01 | 系统通过依赖图计算变更后的 dirty closure | P0 | PLANNED |
| V08-REUSE-02 | 未变化节点以 checksum-identical 方式 carry forward | P0 | PLANNED |
| V08-REUSE-03 | arc 边界不确定时保守扩大重建范围 | P1 | PLANNED |
| V08-REUSE-04 | 重用报告量化避免的调用、token 与成本 | P1 | PLANNED |
| V08-QUAL-01 | 单书冻结题集覆盖 local、跨章节、global、no-answer 与 spoiler 场景 | P0 | PLANNED |
| V08-QUAL-02 | 候选与 leaf baseline 在同一 source、cutoff 与预算下比较 | P0 | PLANNED |
| V08-QUAL-03 | lineage、owner、snapshot、spoiler 或 pointer 任一违规都必须阻断资格 | P0 | PLANNED |
| V08-QUAL-04 | 报告覆盖质量、faithfulness、延迟、成本、reuse 与 fallback 指标 | P0 | PLANNED |
| V08-QUAL-05 | 最终结论仅允许 qualified_candidate 或 blocked，且不得执行 promotion | P0 | PLANNED |

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
| V08-AUDIT-01..04 | Phase 12 | read-only eligibility report；provider/pointer negative tests |
| V08-MEM-01..05 | Phase 13 | strict candidate contracts；lineage and no-pointer invariants |
| V08-BUILD-01..05 | Phase 14 | bottom-up builder；checkpoint/cache/failure-scope tests |
| V08-RETR-01..05 | Phase 15 | routing/down-drill；raw citation and spoiler adversarial tests |
| V08-REUSE-01..04 | Phase 16 | dependency dirty closure；checksum carry-forward report |
| V08-QUAL-01..05 | Phase 17 | frozen single-book comparative qualification；candidate-only verdict |

## Current Evidence

- Phase 06 quality durable jobs + baseline prepare/commit + cross-chunker reports delivered.
- Phase 07 chunking pipeline packages under `backend/app/services/chunking/` with SUMMARYs 07-01..07-06.
- Related automated suite: **88 passed** (`unit/integration chunking` + adversarial + legacy `test_chunking`).
- Alembic head includes quality run/baseline tables (`08baselinecand01`).
- Import progress is persisted through `ImportJob`; lease control, retry, cancellation and restart recovery are verified.
- Phase 04 knowledge graph fixture eval has 20 labeled fiction/history examples and deterministic offline tests.
- Phase 07 residual: promote hierarchy/build stores from in-memory contracts to PostgreSQL + production retrieval wiring.
- Branch may still carry local BGE/reader UX WIP outside Phase 06/07 plan commits.
