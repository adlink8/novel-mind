# continue-derivative-story（Phase 37 版本化 constrained derivative generation 技能 / REQ-FORK-03 / REQ-FORK-06 / REQ-AGENT-02/03/04/07）

给定一本小说（owner/novel/branch 血缘绑定，可选 fork 用于 derivative mode）的
**只读 branch 上下文 + 冻结 context package 血缘** + **派生 continuation 意图**
（project_id / chapter_id / chapter_number / intent / context_package_id /
source snapshot 血缘 / evidence refs），通过 **7 个只读域工具 + 2 个 action 工具**
（`allow_divergence` / `publish_derivative_revision`）消费 Phase 37 确定性
constrained derivative generation 域能力（D-37-01..D-37-05），产出候选
`DraftArtifact` + `ContinuityReport` + disabled-by-default `BranchSuggestion[]`。
本技能**只产出候选**：action 工具创建显式 divergence override（pending）+
**独立** pending Web ApprovalRequest（allow_divergence，payload_hash 绑定 exact
`draft_hash` + `canon_delta_hash`；publish_derivative_revision 绑定**完全相同**
的 hash——D-11/D-15）。**确定性发布属于后端**——只有确定性 revision publisher
（`app.services.derivative_generation.overrides.consume_publish_approval` →
`approve_override`）在独立 publish approval 批准后原子校验 approval action +
相同 hash 绑定 + allow_divergence 已批准 + 完整 revalidation + 冻结候选血缘 +
owner/novel/branch/fork scope，才把 approved override 物化为 append-only
Fanfiction Canon `derivative_revision`（D-37-03/D-37-04）。Agent 不直接写任何
Original Canon / user draft / generation/override/domain 表 / ApprovalRequest
决策 / published 状态；Original Canon 不可变、绝不 promotion；会话永远不是
事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: continue-derivative-story`，`version: 1.0.0`（Phase 37 绑定版本）。
- `allowed_tools`（编排 allowlist，9 个域工具）：
  - 7 个只读：`get_novel`、`get_chapter`、`search_novel_text`、
    `get_timeline`、`get_relationships`、`get_clues`、`get_narrative_memory`；
  - 2 个 action：`allow_divergence`（为 blocked/needs_override 候选创建显式
    divergence override + pending ApprovalRequest）、`publish_derivative_revision`
    （只在 allow_divergence 批准 + 完整 revalidation 通过后创建**独立** publish
    ApprovalRequest，绑定相同 hash）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- 最终 scope / source snapshot / fork 血缘由服务端确定性派生与强制
  （`project_id` / `chapter_id` / `intent` / `context_package_id` /
  `source_snapshot_hash` / `evidence_refs` 只是意图引用），**不是** Agent 工具——
  本技能绝不自行物化事实或分支。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, user_interpretation, derivative:autosave,
  derivative:direct_write, derivative_generation:write, approval_request,
  revision_service]`。
- `approval_required_for: [allow_divergence, publish_derivative_revision]`——
  两个 action 各自创建 **独立 pending Web ApprovalRequest**（D-11）；
  **绝不复用** allow_divergence approval；任何直接发布 / 域表写入 / promotion /
  写回 Original Canon / 伪造批准 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、ToolRuns、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact revision。

## 执行流程（branch context → Tools → DraftArtifact/ContinuityReport → divergence approval → revalidation → publish approval → deterministic publisher 只读 handoff）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`branch` / `fork`（可选，
  derivative mode）；`project_id` / `chapter_id` / `chapter_number` /
  `intent` / `context_package_id` / `source_snapshot_id` /
  `source_snapshot_hash` / `evidence_refs`；`requested_actions`
  （allow_divergence / publish_derivative_revision）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / source snapshot / fork 血缘由服务端强制（D-37-02）；stale
  source snapshot / 错误 owner/branch/fork / 未授权发布 → fail closed，
  不产出候选产物。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - `get_novel` / `get_chapter` / `search_novel_text`：只读 branch 上下文与
    leaf 证据，物化 citation lineage。
  - `get_timeline` / `get_relationships` / `get_clues` /
    `get_narrative_memory`：只读解释/创作参考（candidate-only，ADR-0002）。
  - 请求 divergence：调用 `allow_divergence`——服务端 override gate 只接受
    理由 + 受影响 leaf 证据（或候选已声明的 CanonDelta），校验 draft_hash /
    canon_delta_hash 从候选确定性血缘重放；创建一个 **pending**
    DerivativeOverride + **pending Web ApprovalRequest**（idempotency key 从
    override/scope 血缘确定性重放，D-37-03）。
  - 请求发布：仅在 allow_divergence approval 已批准 + 完整 revalidation 通过
    后调用 `publish_derivative_revision`——创建**独立** pending Web
    ApprovalRequest（绑定与前者完全相同的 draft_hash + canon_delta_hash）。
- Phase 37 确定性能力（budgeted candidate runner / contradiction & consistency
  gate / override / immutable PublishedDerivativeRevision）由后端
  `derivative_generation/` 服务承担；本技能只编排上述 allowlist 工具，
  确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选 DraftArtifact（服务端 gate + finalize）
- 模型结构化输出（DraftArtifact 信封：draft + continuity_report +
  branch_suggestions）走 26-06 normalizer；服务端 integrity gate 拒绝 schema
  drift（status 非 candidate、draft hash 漂移、BranchSuggestion 缺六字段/
  enabled 默认、evidence 越界、wrong branch）。
- agent_end stop → finalize 写入 candidate DraftArtifact + 不可变 revision；
  取消 → cancelled，0 写入。
- `branch_suggestions` 是 disabled-by-default 候选（六字段 +
  `enabled_by_default=false`）：只描述可供用户选择的分支选项，**绝不自动 fork**、
  不改变任何 Canon/branch 状态、**不能复用** allow_divergence approval。

### 第 4 步：divergence approval（后端权威，D-37-03）
- 用户 Web 批准 `allow_divergence` → 服务端确认（pending → approved）。
- 对已批准 divergence 重跑**完整确定性 validator**（revalidation）；
  失败/漂移 → fail closed，不发布。

### 第 5 步：独立 publish approval + deterministic publisher
- 仅当 revalidation 通过后创建**独立** `publish_derivative_revision`
  ApprovalRequest（绑定与 allow_divergence 完全相同的 draft_hash +
  canon_delta_hash；复用/漂移 → fail closed）。
- 用户 Web 批准 publish approval → 确定性 revision publisher
  （`consume_publish_approval`）原子校验 approval action + 相同 hash 绑定 +
  allow_divergence 已批准 + 完整 revalidation + 冻结候选血缘 +
  owner/novel/branch/fork scope，然后调用 `approve_override` 把 approved
  override 物化为 append-only Fanfiction Canon `derivative_revision`（绝不
  last-write-wins，CAS-guarded）。Original Canon 绝不变；任何 forged/expired/
  cancelled/rejected approval、stale revision、wrong branch/fork、schema drift
  → fail closed，无权威写入。

## 边界（fail closed）

- Original Canon 不可变：无任何写路径。
- Agent Service 不能直接写 Original Canon / generation/override/domain 表 /
  published state；divergence 与 publish 是**两个独立 approval**，绑定相同 hash。
- BranchSuggestion 是 disabled-by-default 候选：不自动 fork、不授予/复用任何
  approval（REQ-FORK-06 / D-37-05）。
- 无 shell / filesystem / ambient package / direct database 路径。
- 唯一官方输出：`DraftArtifact` + `ContinuityReport`（candidate-only）。
