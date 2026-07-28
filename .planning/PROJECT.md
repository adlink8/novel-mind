# NovelMind GSD Project

## Core Value

在可信、安全、可迁移的基础上构建 AI 小说理解与创作能力。功能数量不能替代权限、数据一致性和可重复验证。

## Current Milestone

**No active v0.8 work** — v0.8 已于 2026-07-16 以 **candidate scope** 收口归档。

下一里程碑未立项；可选：`/gsd-new-milestone`，或处理 v0.3 评测 / Phase 10 residual / WIP 分拣。

## Current State (shipped)

- **v0.8 分层叙事记忆与层级 RAG（candidate-only）**：Phase 12–17 VERIFIED；Phase 18 动效 VERIFIED。  
  审计：`.planning/v0.8-MILESTONE-AUDIT.md` → `achieved_candidate_scope`  
  归档：`.planning/milestones/v0.8-ROADMAP.md`、`v0.8-REQUIREMENTS.md`  
  Alembic head：`17memqual01`  
- 不存在 narrative-memory production promotion / active pointer cutover。

## Active Requirements (cross-milestone residuals)

- [ ] v0.3 RAG 评测质量闭环（100 confirmed、有效非零指标、faithfulness/cost）。
- [ ] Phase 10 real Playwright residual（reader-chat-real）。
- [ ] （可选）live-provider 单书 qualification（需 hard-budget，非 CI 权威）。

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
