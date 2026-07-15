# NovelMind GSD Project

## Core Value

在可信、安全、可迁移的基础上构建 AI 小说理解与创作能力。功能数量不能替代权限、数据一致性和可重复验证。

## Current Milestone: v0.8 分层叙事记忆与层级 RAG

**Goal:** 在不推倒现有分析资产的前提下，将 chapter → scene → evidence 扩展为可版本化、可回滚、可评测的分层叙事记忆，并验证自下而上构建与自上而下检索的纵向 MVP。

**Target features:**
- 只读审计现有层级、时间线、人物关系和线索资产，按 lineage/checksum 判定复用资格。
- 定义 Chapter State → Story Arc/Volume → Global Story Model 的证据约束数据契约。
- 对单本小说执行不切 active pointer 的 dry-run backfill，并量化覆盖率、成本与失败范围。
- 验证 coarse-to-fine 检索最终回落到原文 evidence 和 spoiler cutoff。

## Active Requirements

- [ ] 现有合格 evidence/scene/chapter 资产无需重新调用模型即可被新层级复用。
- [ ] 任何上层叙事结论都能沿父子关系追溯到 active source snapshot 中的原文证据。
- [ ] 新层级以候选版本运行，未通过验证前不替换现有时间线、人物关系、线索或聊天读模型。
- [ ] 单层失败可局部重建，不要求整本小说从头分析。

## Current Reality

v0.1-v0.2 已完成。v0.3 的导入、索引、混合搜索、前端搜索和评测基础设施已验证，但 RAG 评测质量闭环仍有验收缺口：数据库仅 10/100 题为 confirmed，现有运行的检索指标均为 0，faithfulness/cost 未实现。详细证据见 `IMPLEMENTATION-STATUS.md` 和 `.planning/v0.3-MILESTONE-AUDIT.md`。

## Milestone History

- v0.1 审计与启动修复：完成，建立 VERIFIED / PARTIAL / MISSING 基线并修复启动级 API 契约。
- v0.2 安全与架构修复：完成，关闭 2026-06-11 复审发现的 P0/P1 阻断项（3 个 Phase，全部 VERIFIED）。
- v0.3 小说导入 + RAG 索引：PARTIAL，主检索链路完成，RAG 评测质量门槛待关闭。

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
*Last updated: 2026-07-15 after starting milestone v0.8.*
