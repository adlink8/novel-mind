# Phase 38 Context — Derivative Visual Consistency

## Decisions

- **D-38-01** 视觉 fork 从 Phase 30–34 已规划的 Visual Bible/Scene Spec/AssetRevision/anchor immutable references 读取，但 derivative Visual Bible/Scene Spec/asset 必须拥有独立 namespace、version、owner 和 provenance。Canonical refs: `REQ-FORK-04`, `ROADMAP.md#Phase 38`, `.planning/phases/30-visual-bible/30-CONTEXT.md`, `.planning/phases/32-scene-spec-prompt-compiler/32-CONTEXT.md`, `.planning/phases/33-illustration-generation-consistency/33-CONTEXT.md`, `.planning/phases/34-illustration-anchor-export/34-CONTEXT.md`。
- **D-38-02** 原始视觉 authority 不可被 derivative 资产覆盖；任何偏离必须显式记录为 derivative divergence。Canonical refs: `REQ-FORK-04`, `ROADMAP.md#Phase 38`。
- **D-38-03** 生成服务只返回候选资产；确定性代码负责 namespace、identity、source reference、checksum、review/version lineage 和发布门。Canonical ref: 用户约束“Agent 只产候选，确定性代码掌握发布”。
- **D-38-04** Phase 22 仍 0/3 nightly，视觉一致性完成不改变质量资格。Canonical ref: `.planning/STATE.md`。

## Agent Consumer Contract

- Skill / mode: illustrate-derivative-scene.
- Inputs: branch SceneSpec + branch Visual Bible delta + Original references read-only.
- Official output: BranchVisualBibleArtifact; BranchIllustrationRevision, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: publish_derivative_visual Web approval.
- Deterministic authority: branch/identity/style/divergence/consistency validator.
- Forbidden: Original visual authority mutation; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- 可复用 Phase 30–34 的 Visual Bible/Scene Spec/AssetRevision/anchor/export-manifest 契约；实现前仍需核对这些 phase 是否已执行以及代码是否与规划一致。
- 可在现有文件存储和 hash/provenance 模式内选择资产表、review 状态和跨章节 identity key。

## Deferred Ideas (OUT OF SCOPE)

- 本阶段不修改原作视觉资产、不切换全局视觉 pointer、不把图像 embedding 结果写进原作 index。
- 不引入新的图像生成供应商或未经验证的资产包；供应商/依赖需另行 human verify。
