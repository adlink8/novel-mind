# NovelMind GSD Project

## Core Value

在可信、安全、可迁移的基础上构建 AI 小说理解与创作能力。功能数量不能替代权限、数据一致性和可重复验证。

## Current Reality

v0.2 里程碑已全部完成。认证授权、SSRF、文件边界、密钥保护、数据库迁移、持久化导入任务（租约/幂等/取消/恢复）和依赖漏洞均已修复并验证。RAG 管线已实现（分块、embedding、向量存储、语义搜索）。当前进入 v0.3 — 小说导入 + RAG 索引的完善与前端集成。详细证据见 `IMPLEMENTATION-STATUS.md`。

## Milestone History

- v0.1 审计与启动修复：完成，建立 VERIFIED / PARTIAL / MISSING 基线并修复启动级 API 契约。
- v0.2 安全与架构修复：完成，关闭 2026-06-11 复审发现的 P0/P1 阻断项（3 个 Phase，全部 VERIFIED）。
- v0.3 小说导入 + RAG 索引：当前 active milestone，在 v0.2 门槛通过后激活。

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
