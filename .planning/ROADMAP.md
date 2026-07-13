# Roadmap: NovelMind

## Milestones

- [x] v0.1 审计与启动修复 - 建立可信实现基线和启动级契约。
- [x] v0.2 安全与架构修复 - 关闭安全、迁移、路由、依赖和导入可靠性阻断项。
- [ ] v0.3 小说导入 + RAG 索引 - 导入/索引/搜索已交付，RAG 评测质量闭环仍有缺口。
- [x] v0.5 自动质量与 CI 门禁 - 以全栈自动化和双模型仲裁替代人工 RAG 质量确认；REQ-AUTO-11 closed（06-08/06-09）。

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

## Auto Start

Phase 06 与 Phase 07 均已完成。当前无自动执行 plan。auto-start 保持关闭。  
验证：`.planning/phases/06-automated-quality-ci/` SUMMARYs；`.planning/phases/07-semantic-hierarchical-chunking/07-VERIFICATION.md`。

## Backlog

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
**Status:** PLANNED
**Plans:** 5/6 plans executed

Plans:
- [x] 08-01 durable analysis jobs and immutable version foundation
- [x] 08-02 strict timeline schema and evidence-bound chapter extraction
- [x] 08-03 cross-chapter reconciliation, overrides, budget, and promotion
- [x] 08-04 progressive owner-scoped API and spoiler boundary
- [x] 08-05 global analysis workspace and interactive ECharts timeline
- [ ] 08-06 frozen qualification, end-to-end verification, and release gate
