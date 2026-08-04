# prepare-export（Phase 39 版本化 branch-aware derivative export 技能 / REQ-FORK-05 / REQ-AGENT-02/03/04/07）

给定一本小说（owner/novel/branch 血缘绑定，必选 fork 用于 derivative mode）的
**只读 branch 上下文 + 已批准 published derivative revisions / assets /
citations / export policy**（通过 4 个只读域工具消费），通过 **2 个 action 工具**
（`approve_export` / `materialize_export`）消费 Phase 39 确定性 derivative
export 域能力（D-39-01..D-39-03：frozen ExportSnapshot + sealed manifest +
bounded provenance package + three-dimension audit），产出候选
`ExportPreparationArtifact`（携带完整 branch-aware 血缘：artifact/revision IDs、
SkillRun/ToolRun IDs、owner/novel/branch/fork、source snapshot、base revision、
input/content hashes、evidence refs、runtime/model/generator lineage、validator
report、approval/materialization lineage）。

本技能**只产出候选**：`approve_export` action 只为已 finalize 的候选
ExportPreparationArtifact 创建 pending Web ApprovalRequest（action=
`approve_export`，payload_hash 绑定 artifact revision + 确定性
`preparation_hash`，D-11/D-15）。**确定性物化属于后端**——只有确定性
materializer（`app.services.derivative_export.materializer.materialize_export`）
在独立 approval 被用户批准后原子校验 approval action + 相同 preparation_hash
绑定 + owner/novel/branch/fork/project scope + 冻结 manifest 重放，才把候选
artifact 推进为 approved 并产出可复现 bundle（frozen manifest 复算）。Agent
不直接写任何 Original Canon / 域表 / ApprovalRequest 决策 / Artifact 状态 /
bundle；**download 只读、永不改变 Artifact status 或 approval lineage**
（D-39-02）。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: prepare-export`，`version: 1.0.0`（Phase 39 绑定版本）。
- `allowed_tools`（编排 allowlist，6 个域工具）：
  - 4 个只读：`get_novel`、`get_chapter`、`search_novel_text`、
    `get_narrative_memory`；
  - 2 个 action：`approve_export`（为已 finalize 候选 artifact 创建独立
    pending Web ApprovalRequest）、`materialize_export`（只接受
    approved artifact + preparation_hash 匹配的 approve_export
    ApprovalRequest，产出可复现 bundle）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- 最终 scope / source snapshot / base revision / preparation_hash 血缘由
  服务端确定性派生与强制（`project_id` / `fork` / `source_snapshot_hash` /
  `content_hash` / `evidence_refs` 只是意图引用），**不是** Agent 工具——
  本技能绝不自行物化 manifest 或 bundle。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, user_interpretation, derivative:autosave,
  derivative:direct_write, derivative_export:write, approval_request,
  materializer_service, published_assets]`。
- `approval_required_for: [approve_export]`——`approve_export` 创建**独立**
  pending Web ApprovalRequest（D-11）；`materialize_export` 只接受已批准的
  artifact 血缘，任何直接物化 / 域表写入 / 状态推进 / 伪造批准 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、ToolRuns、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact revision。

## 执行流程（branch context → Tools → candidate ExportPreparationArtifact → approve_export approval → 确定性 materialize_export）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`branch` / `fork`（derivative
  mode 必须提供）；`project_id`（derivative project）；`source_snapshot_id` /
  `source_snapshot_hash` / `content_hash`（frozen 血缘意图引用）/
  `evidence_refs`；`requested_action`（approve_export / materialize_export）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / source snapshot / base revision 血缘由服务端强制（D-39-01）；
  stale source snapshot / 错误 owner/branch/fork / 未授权物化 → fail closed，
  不产出候选产物。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - `get_novel` / `get_chapter` / `search_novel_text`：只读 branch 上下文与
    leaf 证据，物化 citation lineage。
  - `get_narrative_memory`：只读解释/创作参考（candidate-only，ADR-0002）。
  - 请求审批/物化：调用 `approve_export`（服务端为已 finalize 候选 artifact
    创建 **pending** Web ApprovalRequest，payload_hash 绑定 artifact
    revision + preparation_hash；wrong owner/branch/fork/stale hash →
    fail closed）；approval 被用户批准后调用 `materialize_export`（确定性
    materializer 只接受 approved artifact + 匹配 preparation_hash，产出
    可复现 bundle）。
- Phase 39 确定性能力（snapshot freeze / manifest seal / package /
  materializer）由后端 `derivative_export/` 服务承担；本技能只编排上述
  allowlist 工具，确定性 materializer 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选 ExportPreparationArtifact（服务端 gate + finalize）
- 模型结构化输出（ExportPreparationArtifact 信封：preparation =
  ExportPreparationPayload）走 26-06 normalizer；服务端 integrity gate 拒绝
  schema drift（status 非 candidate、review_state 非 candidate、evidence 越界、
  wrong branch、authority_space 非 derivative / 缺 fork、source hash 漂移）。
- agent_end stop → finalize 写入 candidate ExportPreparationArtifact + 不可变
  revision；取消 → cancelled，0 写入。

### 第 4 步：approve_export approval（后端权威，D-39-01）
- 用户 Web 批准 `approve_export` → 服务端确认（pending → approved）。
- 确定性 materializer 重放 approval + preparation_hash + artifact revision +
  scope；失败/漂移 → fail closed，不物化。

### 第 5 步：确定性 materialize_export 物化
- 仅当 approval 已批准后，确定性 materializer（`materialize_export`）原子校验
  approval action + 相同 preparation_hash 绑定 + artifact revision 血缘 +
  owner/novel/branch/fork/project scope，把候选 artifact 推进为 approved，并
  通过 39-01/02 package/serializer 服务从 frozen manifest 复算产出 bundle
  （Markdown/EPUB/package 字节可复现）。download 只读、永不改变 Artifact
  status / approval lineage。
- Original Canon / Original 空间绝不变；任何 forged/expired/cancelled/rejected
  approval、stale hash、wrong branch/fork、schema drift、pending/rejected
  artifact → fail closed，无 bundle 或权威写入。

## 边界（fail closed）

- Original Canon 不可变：无任何写路径。
- Agent Service 不能直接写 Original Canon / 域表 / Artifact 状态 / bundle /
  published 状态；`approve_export` 只创建候选 approval——确定性 materializer
  拥有 approved 物化与 bundle 产出。
- 无 shell / filesystem / ambient package / direct database 路径。
- 唯一官方输出：`ExportPreparationArtifact`（candidate-only）。
