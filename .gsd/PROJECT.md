# NovelMind GSD Project

## Core Value

在可信、安全、可迁移的基础上构建 AI 小说理解与创作能力。功能数量不能替代权限、数据一致性和可重复验证。

## Current Reality

基础导入、阅读、模型配置和测试已经存在。认证授权、SSRF、文件边界、密钥保护、数据库迁移、前端路由和依赖漏洞已经修复并验证；当前门槛只剩持久化导入任务。详细证据见 `IMPLEMENTATION-STATUS.md`。

## Milestone History

- v0.1 审计与启动修复：完成，建立 VERIFIED / PARTIAL / MISSING 基线并修复启动级 API 契约。
- v0.2 安全与架构修复：当前 active milestone，关闭 2026-06-11 复审发现的 P0/P1 阻断项。
- 小说导入 + RAG 索引：保留为下一里程碑，不在安全门槛通过前自动执行。

## Planning Sources

- `IMPLEMENTATION-STATUS.md`：实现事实基线
- `.gsd/ROADMAP.md`：规范化 GSD 路线图
- `.gsd/STATE.md`：当前自动执行位置
- `.gsd/backlog/`：尚未激活的计划输入

## Execution Rules

1. 所有 task plan 包含 `Steps / Must-Haves / Verification`。
2. 每个 implementation slice 以 `Test, Fix, and Confirm` 结束。
3. 文档完成状态必须有实际命令或代码证据。
4. `.gsd/STATE.md` 是唯一执行状态源，不创建 `.planning` 镜像。
5. 人类可读文档写入 `README.md`、`IMPLEMENTATION-STATUS.md` 和 `docs/`；AI 执行文档写入 `.gsd/`。
