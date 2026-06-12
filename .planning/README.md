# GSD AI Workspace

`.planning/` 是 NovelMind 唯一的 AI 规划和执行状态目录，遵循 GSD（Get Shit Done）工作流。

## Ownership

- 主要读写者：GSD / AI agent。
- 人类维护者通常只审核，不需要手工维护运行状态。
- 面向人的项目说明统一写入 `README.md`、`IMPLEMENTATION-STATUS.md` 和 `docs/`。

## Files

- `PROJECT.md`：AI 工作边界和核心价值
- `REQUIREMENTS.md`：可追踪需求
- `ROADMAP.md`：milestone 和 phase
- `STATE.md`：唯一执行游标
- `config.json`：自动路由和计划格式规则
- `phases/`：active / completed task plans 与 summaries
- `backlog/`：尚未激活的上下文和计划草案

## Write Protocol

1. 执行前读取 `config.json`、`STATE.md`、`ROADMAP.md` 和当前 plan。
2. 只在 `.planning/` 更新 milestone、phase、plan、task 和自动入口。
3. 每个 task plan 必须包含 `Steps / Must-Haves / Verification`。
4. 每个 implementation slice 最后执行 `Test, Fix, and Confirm`。
5. 代码验证完成后，再同步人类可读结论到 `IMPLEMENTATION-STATUS.md` 或 `docs/`。
6. `STATE.md` 是唯一 AI 状态源；冲突时以实际代码和验证结果校准它。
7. 系统结构以 `docs/architecture/` 为准，GSD 内不做重复架构描述。

## Active Entry

读取 `config.json` 的 `auto_start`。当前入口为：

`phases/02-security-and-architecture-remediation/02-03-PLAN.md`
