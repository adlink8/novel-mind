# NovelMind GSD Project

## Core Value

在可信、安全、可迁移的基础上构建 AI 小说理解与创作能力。功能数量不能替代权限、数据一致性和可重复验证。

## Current Milestone

**v1.1 工程与治理基线收口（PLANNED，2026-07-26 立项规划）** — Phase 21–25，详见 `ROADMAP.md`。

后续里程碑已按标准 GSD 格式写入 `ROADMAP.md`：v1.1 基线收口（Phase 21–25）→ v1.2 单书垂直证明（Phase 26–29）→ v1.3 生产切换（Phase 30，需新授权）→ v1.4 创作域（Phase 31–34，核心功能完成）。现状核查事实：`AUDIT-STATUS-REFRESH-2026-07-26.md`。

首个动作：Phase 21（phase21 分支工作追认与文档一致性）∥ Phase 22（CI 恢复绿与门禁生效），两者无依赖可并行。

## Current State (shipped)

- **v0.8 分层叙事记忆与层级 RAG（candidate-only）**：Phase 12–17 VERIFIED；Phase 18 动效 VERIFIED。  
  审计：`.planning/v0.8-MILESTONE-AUDIT.md` → `achieved_candidate_scope`  
  归档：`.planning/milestones/v0.8-ROADMAP.md`、`v0.8-REQUIREMENTS.md`  
  Alembic head：`17memqual01`  
- 不存在 narrative-memory production promotion / active pointer cutover。

## Active Requirements (cross-milestone residuals)

- [ ] v0.3 RAG 评测质量闭环（100 confirmed、有效非零指标、faithfulness/cost）→ 归入 Phase 28。
- [ ] Phase 10 real Playwright residual（reader-chat-real）→ 归入 Phase 28。
- [ ] （可选）live-provider 单书 qualification（需 hard-budget，非 CI 权威）→ 归入 Phase 28。
- [ ] master CI 全红恢复 + 分支保护实际生效（2026-07-26 新发现）→ Phase 22。
- [ ] phase21 分支工作 GSD 追认与文档漂移消除（2026-07-26 新发现）→ Phase 21。

## Current Reality

v0.1–v0.2 完成。v0.3 主检索链路完成但评测质量仍 PARTIAL。v0.5–v0.7 主产品能力已落地（09 VERIFIED；10 PARTIAL；11 implement-complete）。v0.8 candidate 链路与动效系统已交付并通过集成/单元复验。

## Milestone History

- v0.1 审计与启动修复：完成。
- v0.2 安全与架构修复：完成。
- v0.3 小说导入 + RAG 索引：PARTIAL，评测质量门槛待关闭。
- v0.5 自动质量与 CI：完成。
- v0.7 关系 / 阅读问答 / 线索：09 VERIFIED；10 PARTIAL；11 implement-complete。
- **v0.8 分层叙事记忆与层级 RAG：SHIPPED candidate scope 2026-07-16。**

## Planning Sources

- `IMPLEMENTATION-STATUS.md`：实现事实基线
- `.planning/ROADMAP.md`：规范化 GSD 路线图
- `.planning/STATE.md`：当前自动执行位置
- `.planning/backlog/`：尚未激活的计划输入

## Execution Rules

1. 所有 task plan 包含 `Steps / Must-Haves / Verification`。
2. 每个 implementation slice 以 `Test, Fix, and Confirm` 结束。
3. 文档完成状态必须有实际命令或代码证据。
4. `.planning/STATE.md` 是唯一执行状态源。
5. 人类可读文档写入 `README.md`、`IMPLEMENTATION-STATUS.md` 和 `docs/`；AI 执行文档写入 `.planning/`。

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? Move to Out of Scope with reason.
2. Requirements validated? Move to Validated with phase reference.
3. New requirements emerged? Add to Active.
4. Decisions to log? Add to Key Decisions.
5. "What This Is" still accurate? Update if drifted.

**After each milestone:**
1. Review all sections and the Core Value.
2. Audit Out of Scope and compatibility constraints.
3. Update context with verified implementation facts.

---
*Last updated: 2026-07-16 after archiving milestone v0.8 (candidate scope).*
