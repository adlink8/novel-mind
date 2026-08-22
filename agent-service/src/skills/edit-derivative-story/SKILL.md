# edit-derivative-story（Phase 36 版本化 derivative editor 技能 / REQ-FORK-02 / REQ-AGENT-03/04/07）

给定一本小说（owner/novel/branch 血缘绑定，可选 fork 用于 derivative mode）的
**只读 branch 上下文** + **派生 chapter 编辑意图**（project_id / chapter_id /
base_revision / proposal_key / 候选 Markdown patch / source snapshot 血缘 /
evidence refs），通过 **6 个只读域工具 + 1 个 action 工具**
（`apply_derivative_edit`）消费 Phase 36 确定性 derivative 编辑域能力，产出候选
`DerivativeEditProposal`（D-36-01..D-36-04）。本技能**只提议 patch**：action 工具
创建候选 proposal（proposal_status=proposed）+ 服务端权威 pending Web
ApprovalRequest（action=apply_derivative_edit，payload_hash 确定性重放，
D-11/D-15）。**确定性应用属于后端**——只有确定性 Revision Service
（`app.services.derivative_editor.revisions.apply_agent_edit`）在用户 Web 批准后
原子校验 approval action + payload hash + 冻结 proposal artifact 血缘 +
owner/novel/branch/fork scope + 同一 base_revision CAS，才把 approved proposal
应用为 append-only `agent_proposal` 修订。Agent 不直接写任何 Original Canon /
user draft（autosave）revisions / derivative 域表 / ApprovalRequest 行 /
published 状态；Original Canon 不可变、user autosave 与 agent proposal 走分离
端点/事件类型/actor 标签/CAS 路径；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: edit-derivative-story`，`version: 1.0.0`（Phase 36 绑定版本）。
- `allowed_tools`（编排 allowlist，7 个域工具）：
  - 6 个只读：`get_novel`、`get_chapter`、`get_timeline`、
    `get_relationships`、`get_clues`、`get_narrative_memory`；
  - 1 个 action：`apply_derivative_edit`（只创建候选 proposal + pending
    ApprovalRequest，服务端 proposal gate 只接受冻结 source snapshot 血缘 +
    有效 project/chapter scope + base_revision CAS 锚，D-36-02；绝不直接应用）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- 最终 scope / base_revision CAS / 精确 source snapshot / fork 血缘由服务端
  确定性派生与强制（`project_id` / `chapter_id` / `chapter_number` /
  `proposal_key` / `base_revision` / `content` / `source_snapshot_hash` /
  `evidence_refs` 只是意图引用），**不是** Agent 工具——本技能绝不自行物化事实。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, user_interpretation, derivative:autosave,
  derivative:direct_write, approval_request, revision_service]`。
- `approval_required_for: [apply_derivative_edit]`——action 工具创建 **pending Web
  ApprovalRequest**（D-11）；任何直接应用 / revision_service 调用 / promotion /
  写回 Original Canon / user autosave / 域表 / 伪造批准 → fail closed。
- `budget` per-run 上限：`max_calls=30`、`max_input_tokens=30000`、
  `max_output_tokens=10000`、`max_cost_usd=3.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、ToolRuns、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact revision。

## 执行流程（branch context → Tools → Proposal + ApprovalRequest → finalize → Revision Service 只读 handoff）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`branch` / `fork`（可选，
  derivative mode）；`project_id` / `chapter_id` / `chapter_number` /
  `proposal_key` / `base_revision` / `content`（候选 Markdown patch）/
  `source_snapshot_id` / `source_snapshot_hash` / `evidence_refs`；
  `requested_actions`（apply_derivative_edit）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / base_revision CAS / source snapshot / fork 血缘由服务端强制
  （D-36-02）；stale source snapshot / 错误 owner/branch/fork / 未授权应用 →
  fail closed，不产出 proposal。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - `get_novel` / `get_chapter`：只读 branch 上下文，物化 leaf 证据跨度与
    citation lineage。
  - `get_timeline` / `get_relationships` / `get_clues` /
    `get_narrative_memory`：只读解释/创作参考（candidate-only，ADR-0002）。
  - 提议 patch：调用 `apply_derivative_edit`——服务端 proposal gate 只接受冻结
    source snapshot 血缘 + 有效 project/chapter scope + base_revision CAS 锚；
    创建一个**候选** DerivativeEditProposal（proposal_status=proposed）+
    **pending Web ApprovalRequest**（idempotency key 从 proposal/scope 血缘
    确定性重放，D-36-02）。
- Phase 36 确定性能力（CAS Revision Service / append-only history / scope
  validator）由后端 `derivative_editor/` 服务承担；本技能只编排上述 allowlist
  工具，确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选 proposal（服务端 proposal gate）
- action 工具成功 → 服务端创建**一个**不可变 candidate DerivativeEditProposal
  （proposal_status=proposed）+ pending Web ApprovalRequest
  （action=apply_derivative_edit）。Agent 输出保持 candidate-only。
- 绝不直接写 Original Canon / user draft（autosave）revisions / active pointer /
  ApprovalRequest 决策 / published 状态；approval 决策权威只在 FastAPI（D-11）。

### 第 4 步：结构化输出与 finalize
- 模型结构化输出（DerivativeEditProposalArtifact 信封：proposal）走 26-06
  normalizer；服务端 integrity gate 拒绝 schema drift（proposal_status 非
  proposed、source drift、content hash drift、wrong branch、缺 evidence、trail
  漂移）。
- agent_end stop → finalize 写入 candidate Artifact + 不可变 revision；取消 →
  cancelled，0 写入。

### 第 5 步：Approval + deterministic Revision Service（后端权威）
- 用户 Web 批准 `apply_derivative_edit` → 服务端确认（pending → approved）。
- 确定性 Revision Service（`apply_agent_edit`）原子校验 approval action +
  payload hash 重放 + 冻结 proposal artifact 血缘 + owner/novel/branch/fork
  scope + 同一 base_revision CAS，然后应用 approved proposal 为 append-only
  `agent_proposal` 修订（绝不 last-write-wins）。Original Canon 绝不变；任何
  forged/expired/cancelled/rejected approval、stale revision、wrong
  branch/fork、schema drift → fail closed，无权威写入。

## 边界（fail closed）

- Original Canon 不可变：无任何写路径。
- Agent Service 不能直接写 Original Canon / user draft（autosave）revisions /
  published state；user_autosave 与 agent_proposal 走分离端点/事件类型/actor
  标签/CAS 路径；user_autosave 绝不授予 `apply_derivative_edit` 审批。
- 无 shell / filesystem / ambient package / direct database 路径。
- 唯一官方输出：`DerivativeEditProposal`（candidate-only）。
