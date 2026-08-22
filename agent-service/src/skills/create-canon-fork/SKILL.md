# create-canon-fork（Phase 35 版本化 canon fork 技能 / REQ-FORK-01 / REQ-AGENT-03/04/07）

给定一本小说（owner/novel/branch 血缘绑定，可选 fork 用于 derivative mode）的
**只读 Original Canon snapshot** + **fork 意图**（fork_key、requested cutoff、
full_book_requested、expected source snapshot hash）+ **delta 意图**（delta_key /
delta_content / evidence refs），通过 **7 个只读域工具 + 1 个 action 工具**
（`create_canon_fork`）消费 Phase 35 确定性 fork 域能力，产出候选
`CanonForkProposal` + `CanonDeltaArtifact`（D-35-01..D-35-04）。本技能**只创建
候选 fork**：action 工具创建 candidate fork（status=candidate）+ 服务端权威
pending Web ApprovalRequest（action=create_canon_fork，payload_hash 确定性重放，
D-11/D-15）。**确定性 fork 物化属于后端**——只有 Phase 35 deterministic Fork
materializer（`app.services.canon_fork.materializer.materialize_approved_fork`）
在批准后原子校验 approval action + payload hash + 冻结 CanonForkManifest +
source snapshot 重放 + CanonDeltaArtifact 血缘 + owner/novel/branch/fork scope，
才把 candidate fork 物化为 approved（status=approved）。Agent 不直接写任何
Original Canon / canon fork 域表 / ApprovalRequest 行 / published 状态；
Original Canon 不可变、active pointer 恒 false；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: create-canon-fork`，`version: 1.0.0`（Phase 35 绑定版本）。
- `allowed_tools`（编排 allowlist，8 个域工具）：
  - 7 个只读：`get_novel`、`get_chapter`、`search_novel_text`、`get_timeline`、
    `get_relationships`、`get_clues`、`get_narrative_memory`；
  - 1 个 action：`create_canon_fork`（只创建候选 fork + pending
    ApprovalRequest，服务端 proposal gate 只接受 frozen fork manifest +
    server-derived cutoff + 精确 source snapshot，D-35-03；绝不物化）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- fork 的最终 scope / cutoff / full_book_authorized / 精确 source snapshot /
  citation lineage 由服务端确定性 snapshot 服务派生（`fork_key` /
  `requested_cutoff_chapter` / `full_book_requested` /
  `expected_source_snapshot_hash` 只是意图引用），**不是** Agent 工具——本技能
  绝不自行物化事实。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, canon_fork:write, canon_fork:materialize, approval_request,
  fork_materializer]`。
- `approval_required_for: [create_canon_fork]`——action 工具创建 **pending Web
  ApprovalRequest**（D-11）；任何直接授予 / materializer 调用 / promotion /
  写回 Original Canon 或域表 / 伪造批准 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、ToolRuns、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact revision。

## 执行流程（novel/source refs → Tools → Fork + ApprovalRequest → finalize → materializer 只读 handoff）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`branch` / `fork`（可选，
  derivative mode）；`fork_key`；`requested_cutoff_chapter` /
  `full_book_requested` / `expected_source_snapshot_hash`（意图引用）；
  `delta_key` / `delta_content` / `delta_evidence_refs`；`requested_actions`
  （create_canon_fork）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff / full_book_authorized / source snapshot / citation
  lineage 由服务端强制（D-35-03）；stale source snapshot 或未来 cutoff 或未授权
  full-book → fail closed，不产出 fork。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - `get_novel` / `get_chapter` / `search_novel_text`：只读原文，物化 leaf
    证据跨度与 citation lineage。
  - `get_timeline` / `get_relationships` / `get_clues` /
    `get_narrative_memory`：只读解释/创作参考（candidate-only，ADR-0002）。
  - 提议 fork：调用 `create_canon_fork`——服务端 proposal gate 只接受 frozen
    fork manifest + server-derived cutoff + 精确 source snapshot；创建一个
    **候选** fork（status=candidate）+ **pending Web ApprovalRequest**
    （idempotency key 从 fork/delta 血缘确定性重放，D-35-03）。
- Phase 35 确定性能力（snapshot/cutoff/immutable-Original validator +
  materializer）由后端 `canon_fork/` 服务承担；本技能只编排上述 allowlist 工具，
  确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选 fork（服务端 proposal gate）
- action 工具成功 → 服务端创建**一个**不可变 candidate CanonFork
  （status=candidate，active=false）+ pending Web ApprovalRequest
  （action=create_canon_fork）。Agent 输出保持 candidate-only。
- 绝不直接写 Original Canon / active pointer / ApprovalRequest 决策 / published
  状态；approval 决策权威只在 FastAPI（D-11）。

### 第 4 步：结构化输出与 finalize
- 模型结构化输出（CanonForkProposalArtifact 信封：proposal + delta）走 26-06
  normalizer；服务端 integrity gate 拒绝 schema drift（proposal_status /
  delta_status 非 proposed、source drift、wrong branch、缺 evidence、trail 漂移）。
- agent_end stop → finalize 写入 candidate Artifact + 不可变 revision；取消 →
  cancelled，0 写入。

### 第 5 步：Approval + deterministic materializer（后端权威）
- 用户 Web 批准 `create_canon_fork` → 服务端确认（pending → approved）。
- 确定性 Fork materializer（`materialize_approved_fork`）原子校验 approval
  action + payload hash + fork manifest 重放 + source snapshot 重放 + delta
  base revision + owner/novel/branch/fork scope，然后物化 candidate →
  approved（active 恒 false）。Original Canon 绝不变；任何 forged/expired/
  cancelled/rejected approval、stale revision、wrong branch/fork、schema drift
  → fail closed，无权威写入。

## 边界（fail closed）

- Original Canon 不可变：无任何写路径。
- Agent Service 不能直接写 Original Canon / domain tables / published state。
- 无 shell / filesystem / ambient package / direct database 路径。
- 唯一官方输出：`CanonForkProposal` + `CanonDeltaArtifact`（candidate-only）。
