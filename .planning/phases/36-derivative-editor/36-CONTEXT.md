# Phase 36 Context — Derivative Editor

## Decisions

- **D-36-01** 编辑对象是 owner-scoped derivative project；创建时必须显式选择 Canon Fork，不允许从当前阅读页面隐式推断。Canonical refs: `REQ-FORK-02`, `REQ-CRE-03`, `ROADMAP.md#Phase 36`。
- **D-36-02** 项目支持 planning、Markdown chapter editing、autosave、immutable history、diff、rollback；草稿恢复和 optimistic concurrency 是硬要求。Canonical refs: `REQ-CRE-03`, `REQ-CRE-04`, `ROADMAP.md#Phase 36`。
- **D-36-03** 编辑写入只进入 `Fanfiction Canon` namespace；不能修改 `Original Canon` 或 `User Interpretation`。Canonical refs: `REQ-FORK-01`, `REQ-CRE-02`, 用户约束“确定性代码掌握发布”。
- **D-36-04** Phase 22 仍 0/3 nightly，规划不改变其 BLOCKED 状态。Canonical ref: `.planning/STATE.md#Truth Snapshot`。

## Agent Consumer Contract

- Skill / mode: edit-derivative-story.
- Inputs: branch context + base revision.
- Official output: DerivativeEditProposal, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: apply_derivative_edit Web approval for Agent patches.
- Deterministic authority: owner/fork/base-revision/CAS validator + Revision Service.
- Forbidden: Agent autosave/direct revision write; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- 可在现有 SQLAlchemy timestamp、owner dependency、Reader Chat sequence/idempotency 与前端 React Query/textarea 模式上设计 project/chapter/revision 表和编辑器状态。
- 可选择文本 diff 算法；发布版本、revision checksum、父 revision 和冲突检测必须由确定性代码决定。

## Deferred Ideas (OUT OF SCOPE)

- Phase 37 的 LLM constrained generation、Phase 38 的视觉管线和 Phase 39 的最终导出/审计实现。
- 富文本编辑器运行时、协作多人实时编辑、外部同步服务；本阶段只锁定 Markdown 与可恢复草稿。
