# Roadmap: NovelMind

## Milestones

- [x] v0.1 审计与启动修复 - 建立可信实现基线和启动级契约。
- [x] v0.2 安全与架构修复 - 关闭安全、迁移、路由、依赖和导入可靠性阻断项。
- [ ] v0.3 小说导入 + RAG 索引 - 导入/索引/搜索已交付，RAG 评测质量闭环仍有缺口。

第一个 active milestone 已按要求设为并完成"审计与启动修复"。v0.3 经 2026-06-13 复审后恢复为 active，详见 `.planning/v0.3-MILESTONE-AUDIT.md`。

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

| Milestone | Plans Complete | Status |
|---|---:|---|
| v0.1 审计与启动修复 | 3/3 | Complete |
| v0.2 安全与架构修复 | 3/3 | Complete |
| v0.3 小说导入 + RAG 索引 | 4/5 | **Gaps Found** |

## Auto Start

当前没有可自动执行的 plan。下一步需要人工校准 gold chunks 并确认评测题，完成后再运行三策略评测和关闭审计缺口。

## Backlog

### Phase 999.1: 首页 UI 优化 (BACKLOG)
- 删除占位统计卡片（小说总数/章节总数/AI分析次数/同人文作品）
- 已扩展为全站 UI/UX 重构并完成：2026-06-13

### Phase 999.2: Bug 修复 (BACKLOG)
- 阅读页右上角"上一章"与"退出账号"按钮重合
- 搜索返回"搜索失败请重试"无内容
- 已完成：2026-06-13

### Phase 4: LLM 语义判定与证据门控知识图谱链路

**Goal:** 建立可审计的知识图谱构建链路：脚本负责召回、证据、规则、阈值和写库；LLM 只负责语义理解、关系候选和判断。该链路同时支持小说与历史语料。
**Requirements**: REQ-KG-01..06
**Depends on:** Phase 3
**Plans:** 3/4 plans executed

Plans:
- [x] 04-01 knowledge data contracts: PostgreSQL candidate/judgment/evidence/review contracts
- [x] 04-02 candidate packages and LLM judgment: deterministic recall package + structured LLM semantic judgment
- [x] 04-03 evidence gates and projection: schema/evidence/threshold/conflict gates + accepted graph projection
- [ ] 04-04 evaluation and domain fixtures: fiction/history fixtures, graph eval CLI, cost/latency/faithfulness reporting
