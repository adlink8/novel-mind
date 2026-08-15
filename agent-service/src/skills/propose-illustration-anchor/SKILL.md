# propose-illustration-anchor（Phase 34 版本化锚点提议技能 / REQ-VIS-05 / REQ-AGENT-03/04/07）

给定一本小说（owner/novel/authority_space 血缘绑定，可选 branch/fork 用于
derivative mode）的 **精确 source span**（excerpt + anchor_hash +
chapter_content_hash + source snapshot）+ **proposal-ready AssetRevision**
（Phase 33 handoff，rights cleared），通过 **3 个只读域工具 + 2 个 action 工具**
（`publish_illustration` / `attach_illustration_to_text`）消费 Phase 34 确定性
锚点域能力，产出候选 `IllustrationAnchorProposal`（D-34-01..D-34-04）。本技能
**只创建候选 proposal**：action 工具创建 proposal（status=proposed）+ 服务端权威
pending Web ApprovalRequest（action + payload_hash 确定性重放，D-11/D-15）。
**确定性发布属于后端**——只有 Phase 34 deterministic publisher
（`app.services.illustration_anchors.publish`）在批准后原子校验 approval action +
payload hash + proposal_ready asset + exact source hash/range/version +
owner/novel/branch/fork scope，才创建 published asset 引用 + valid anchor +
publish manifest。Agent 不直接写任何 Original Canon / 域表 / ApprovalRequest 行 /
published 状态；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: propose-illustration-anchor`，`version: 1.0.0`（Phase 34 绑定版本）。
- `allowed_tools`（编排 allowlist，5 个域工具）：
  - 3 个只读：`get_novel`、`get_chapter`、`search_novel_text`；
  - 2 个 action：`publish_illustration`、`attach_illustration_to_text`（只创建
    候选 proposal + pending ApprovalRequest，服务端 proposal gate 只接受
    proposal-ready + rights cleared 的 AssetRevision 与精确 source span，
    D-34-01；绝不发布）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- proposal-ready AssetRevision 与精确 source span 由服务端确定性锚点服务按引用
  消费（`asset_revision_id` / `chapter_id` / `source_start` / `source_end` /
  `anchor_hash` / `chapter_content_hash` / `source_snapshot_id`），**不是**
  Agent 工具——本技能绝不自行物化事实。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, illustration:write, illustration:publish, approval_request,
  publisher]`。
- `approval_required_for: [publish_illustration, attach_illustration_to_text]`——
  两个 action 工具都创建 **pending Web ApprovalRequest**（D-11）；任何直接授予 /
  Publisher 调用 / promotion / 写回 Canon 或域表 / 伪造批准 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、ToolRuns、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact revision。

## 执行流程（novel/source refs → Tools → Proposal + ApprovalRequest → finalize → Phase 34 publisher 只读 handoff）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`chapter_id` / `chapter_number`；
  `proposal_key`；`source_snapshot_id` / `source_snapshot_hash`；`source_start` /
  `source_end`（+ 可选 `paragraph_start` / `paragraph_end`）；`excerpt` /
  `anchor_hash` / `chapter_content_hash`；`asset_revision_id`（proposal-ready）；
  `presentation`（caption/alt_text/citation）；可选 `branch` / `fork`（derivative
  mode）；`requested_actions`（publish_illustration / attach_illustration_to_text）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff / proposal_ready / 精确 hash/range 由服务端强制（D-34-01）；
  proposal_ready 校验失败或 source hash/range 与当前正文不匹配 → fail closed，
  不产出 proposal。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - `get_novel` / `get_chapter` / `search_novel_text`：只读原文与章节，物化 leaf
    证据跨度（excerpt + content_hash），验证 source span 存在且与
    `chapter_content_hash` 一致。
  - 提议锚点：调用 `publish_illustration`（提案发布资产锚点）或
    `attach_illustration_to_text`（把锚点绑定到精确文本跨度）——服务端 proposal
    gate 只接受 proposal-ready + rights cleared 的 AssetRevision 与精确 source
    span；创建一个**候选** proposal + **pending Web ApprovalRequest**
    （idempotency key 从 span/asset 血缘确定性重放，D-34-01）。
- Phase 34 确定性能力（proposal gate + approval + deterministic publisher）由
  后端 `illustration_anchors/` 服务承担；本技能只编排上述 allowlist 工具，确定性
  validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选 proposal（服务端 proposal gate）
- action 工具成功 → 服务端创建**一个**不可变 IllustrationAnchorProposal
  （status=proposed，candidate-only，D-34-01）+ 一个 pending ApprovalRequest
  （action + payload_hash 确定性重放）。proposal 携带 owner/novel/chapter/精确
  source span/hash、proposal-ready asset ref 与 branch/fork 血缘。
- 本技能/Agent 不直接写 IllustrationAnchorProposal 或 ApprovalRequest 行——它由
  服务端 proposal service 产出；Agent 通过 `proposal_id` / `approval_request_id`
  引用（只读）。

### 第 4 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08）。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest
  白名单校验）。
- 摘要、解释文本、proposal 本身、approval 状态**都不是** leaf 证据，不得进入
  evidence_refs（D-08）。

### 第 5 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest
  （`{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`）。
- 冻结后不得再增删证据；finalize 落库到 run 上作为引证白名单。

### 第 6 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、enum
  canonicalization、无歧义 container-shape 修复；本技能**不引入任何本地修复
  路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门 + `IllustrationAnchorProposal`
  域校验——proposal_status 恒为 proposed、branch/authority_space 门、
  source-snapshot 血缘绑定）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  IllustrationAnchorProposal 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval/approval_state）→ 稳定 `blocked`，零写入。

### 第 7 步：IllustrationAnchorProposal（官方输出）
- 组装锚点提议信封（字段镜像后端 `IllustrationAnchorProposalArtifact`，见
  `output.schema.json` / D-34-01）：
  - 信封：`type="illustration_anchor_proposal"`，
    `schema_version="illustration-anchor-proposal.v1"`；`owner_id` / `novel_id` /
    `branch` / `producing_skill="propose-illustration-anchor"` /
    `producing_skill_version="1.0.0"` / `skill_version_id` / `model_lineage` /
    `source_versions` / `input_hash` / `evidence_refs`（必须 ⊆ 冻结 manifest
    白名单）/ `tool_runs`（ToolRun 血缘，含 action 工具）/ `status="candidate"` /
    `parent_revision=null` / `normalization`（26-06 trail）。
  - 负载 `illustration_anchor_proposal`：`proposal_key` / `authority_space`
    （original | derivative）/ `fork`（仅 derivative）/ `chapter_id` /
    `chapter_number` / `source_snapshot_id` + `source_snapshot_hash` / `range`
    （source_start/source_end + 可选 paragraph）/ `excerpt` / `anchor_hash` /
    `chapter_content_hash` / `proposal_asset_revision_id` / `presentation` /
    `requested_action`（publish_illustration | attach_illustration_to_text）/
    `proposal_status` **恒为 "proposed"** / `approval_request_id`（null——
    服务端分配）。
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。
  `skill_run_id` / `artifact_id` / `revision_id` / `approval_request_id` /
  `proposal_id` 由服务端分配，本技能**不写任何持久化行**。
- Agent 创建 candidate Artifact 后**暂停**：状态推进（proposed → pending_approval
  → valid）**只**由服务端 proposal/approval/publisher 完成；本技能/Agent **不能**
  直接推进、授予或伪造状态，也**不能**调用 publisher / 写 published 状态。

## 候选纪律（D-34-01..D-34-04，必须遵守）
- 唯一官方状态机是 `proposed → pending_approval → valid`（外加
  needs_repair/invalid），**仅前向**；finalize 写入时 proposal_status 恒为
  `proposed`；任何非 proposed 声称（approval bypass / pending_approval 伪造 /
  valid / published）→ fail closed。
- 每次 run 绑定 owner、novel、authority_space、可选 branch/fork（derivative
  mode）、SkillVersion、SkillRun、ToolRuns、source/input hashes、model/runtime
  lineage 与 Artifact revision（不可变、可版本化）。
- 输出是 **candidate proposal**，直到用户 Web 审批 + 确定性 publisher 完成；
  它**绝不**自动进入 reader/export（D-34-01/02）。
- offset/hash 不匹配 → stale，**绝不**静默移动到邻近段落（D-34-01）；repair 必须
  提出新候选 proposal 并重新审批（D-34-03）。
- `publish_illustration` 与 `attach_illustration_to_text` 都要求 Web
  ApprovalRequest；浏览器只渲染、FastAPI 是唯一决策权威（D-11）。

## 边界（必须遵守，fail closed）
- 零域写入：不写 Canon、不写 illustration 域表、不写 ApprovalRequest 行、不写
  published 状态、不写 anchor 表、不产生任何域事实。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation` 失败，
  且什么都不写。
- **无 ApprovalRequest 直接授予、无 Publisher 越权、无 Canon/域写回**：
  action 工具只创建 pending ApprovalRequest；attempted approval 伪造（envelope
  status 非 candidate / proposal_status 非 proposed / 受保护字段 / forbidden
  Tool/action）→ fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong branch/fork / stale base revision /
  stale input_hash / stale source hash/range / schema drift / unsafe
  normalization / 未知工具 / forbidden Tool/action / approval bypass / publisher
  调用 / Original 突变 → 稳定 blocked/cancelled，零官方写入（0 artifact 行 + 0
  revision 行 + 0 域写入）。
- Phase 34 handoff 是**只读**的：确定性 publisher 只接受 proposal-ready +
  rights cleared 的 AssetRevision 与精确 source span 的 approved proposal；
  candidate / stale / wrong-scope 修订一律拒绝。
