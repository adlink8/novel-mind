# Phase 37 Context — Constrained Derivative Generation

## Decisions

- **D-37-01** 生成前必须编译可审计 context package，包含指定 cutoff 的人物/世界状态、时间线因果、未回收伏笔、world rules、evidence refs、Canon Fork/version lineage 和 user intent。Canonical refs: `REQ-CRE-05`, `REQ-FORK-01`, `ROADMAP.md#Phase 37`。
- **D-37-02** Agent/LLM 只产严格 schema 的 candidate；确定性代码负责 evidence、scope、schema、预算、矛盾/一致性门和发布状态。Canonical refs: `REQ-CRE-06`, `docs/architecture/08-ai-model-layer.md`。
- **D-37-03** 人物行为、既定事实、时间线和 clue 违规必须 fail closed；允许偏离只能作为显式 derivative override，不得回写原作空间。Canonical refs: `REQ-CRE-06`, `ROADMAP.md#Phase 37`。
- **D-37-04** Phase 22 夜间资格保持 0/3；本阶段不把生成成功当作质量资格或生产 promotion。Canonical ref: `.planning/STATE.md`。

## Agent Consumer Contract

- Skill / mode: continue-derivative-story.
- Inputs: frozen branch context package.
- Official output: DraftArtifact; ContinuityReport, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: separate publish and divergence approvals.
- Deterministic authority: continuity/evidence/branch/budget validators + deterministic publisher.
- Forbidden: Original write or conflated approval; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- 复用既有 `ai_router`/`ai_service`、Reader generation job 和 Narrative Memory retrieval manifest；可选择 continuation/ rewrite 两种 intent，但输出 contract 必须统一。
- 约束检查的规则顺序可设计为 deterministic preflight → model candidate → deterministic post-check → explicit override。

## Deferred Ideas (OUT OF SCOPE)

- 自动把 candidate 提升为 Original Canon/NM active；生产 A/B 与 pointer cutover 属于 `999.x`。
- 无 context package 的自由写作、未引用的“记忆”注入、聊天内容反向写事实。
