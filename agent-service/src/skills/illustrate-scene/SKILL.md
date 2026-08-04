# illustrate-scene（Phase 33 版本化场景插图生成技能 / REQ-VIS-04 / REQ-AGENT-02/03/04）

给定一本小说（owner/novel/branch 血缘绑定，可选 fork/derivative mode）的
**已批准 PromptRevision**（其 SceneSpec/Visual Bible/source-snapshot 血缘非
stale）+ 提供商能力，通过 **7 个只读域工具 + 1 个候选生成 action 工具**
（`generate_image_candidate`）消费 Phase 33 确定性插图域能力，产出血缘绑定的
`IllustrationRevision`（D-33-01..D-33-04）。本技能**只创建候选**：durable
worker 产出候选 AssetRevision，确定性 budget/rights/fidelity/consistency
validator 推进 `candidate → validated → proposal_ready`；Phase 33 **绝不**
创建 ApprovalRequest、不调用 publisher、不发 published 状态——approval 与
确定性 publication 由 Phase 34 IllustrationAnchorProposal 拥有。Agent 不直接
写任何 Original Canon / 域表 / ApprovalRequest 行 / published 状态；会话永远
不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: illustrate-scene`，`version: 1.0.0`（Phase 33 绑定版本）。
- `allowed_tools`（编排 allowlist，8 个域工具）：
  - 7 个只读：`get_novel`、`get_chapter`、`search_novel_text`、
    `get_timeline`、`get_relationships`、`get_clues`、`get_narrative_memory`；
  - 1 个 action：`generate_image_candidate`（只创建候选生成作业，
    服务端 generation gate 只接受已批准且非 stale 的 PromptRevision，
    D-33-01；绝不发布）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- validated SceneSpec / Prompt / Visual Bible / provider capabilities 由
  服务端确定性插图服务按引用消费（`prompt_revision_id` /
  `visual_bible_version_id` / `scene_spec_revision_id` /
  `source_snapshot_id`），**不是** Agent 工具——本技能绝不自行物化事实。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, illustration:write, illustration:publish,
  approval_request, publisher]`。
- `approval_required_for: []`——**Phase 33 无 ApprovalRequest / Publisher /
  published 状态**：approval 与确定性 publication 完全属于 Phase 34。
  任何 ApprovalRequest 直接授予 / Publisher 调用 / promotion / 写回 Canon
  或域表 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  ToolRuns、input hash、source snapshot、model/runtime/generator lineage 与
  不可变 Artifact revision。

## 执行流程（novel/prompt refs → Tools → 候选作业 → AssetRevision → 确定性 validator → IllustrationRevision → Phase 34 只读 handoff）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`prompt_revision_id`（已批准
  PromptRevision，服务端重验 approved + 非 stale）；`visual_bible_version_id`；
  `scene_spec_revision_id`；`source_snapshot_id`；可选 `branch` / `fork`
  （derivative mode）/ `provider` / `model` / `width` / `height`。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff / evidence / Visual Bible revision 由服务端强制
  （D-33-01）；候选资产超过剧透截止点或 PromptRevision 血缘非 stale 校验失败
  → fail closed，不产出候选作业。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - 已批准 Visual Bible / 原文证据 / 叙事记忆 → 对应只读工具读取候选信号。
  - 候选生成：调用 `generate_image_candidate`（`prompt_revision_id` +
    `job_key` + provider 能力）——服务端 generation gate 只接受已批准且
    非 stale 的 PromptRevision；创建**一个** durable 候选作业
    （idempotency key 从血缘确定性重放，D-33-01）。
- Phase 33 确定性能力（durable 作业 + worker 产出候选 AssetRevision +
  一致性 evaluator + review/proposal gate）由后端 `illustrations/` 服务承担；
  本技能只编排上述 allowlist 工具，确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选资产（服务端 durable worker）
- 候选作业成功 → durable worker 产出**一个**不可变候选 AssetRevision
  （candidate-only，D-33-03）：确定性 bytes + 血缘（SceneSpec/prompt/
  Visual Bible/source-snapshot/model/config）+ provider request id +
  provenance/rights。候选资产**绝不**自动进入 reader/export。
- 本技能/Agent 不直接写 AssetRevision——它由 worker 产出；Agent 通过
  `asset_revision_id` 引用（只读）。

### 第 4 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08）。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest
  白名单校验）。
- 摘要、解释文本、候选图像描述、consistency score 本身**都不是** leaf 证据，
  不得进入 evidence_refs（D-08 / D-33-04）。

### 第 5 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest
  （`{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`）。
- 冻结后不得再增删证据；finalize 落库到 run 上作为引证白名单。

### 第 6 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、enum
  canonicalization、无歧义 container-shape 修复；本技能**不引入任何本地修复
  路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门 + `IllustrationRevision` 域校验
  ——review_state 恒为 candidate、branch/authority_space 门、source-snapshot
  血缘绑定）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  IllustrationRevision 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval/approval_state）→ 稳定 `blocked`，零写入。

### 第 7 步：IllustrationRevision（官方输出）
- 组装插图修订信封（字段镜像后端 `IllustrationRevisionArtifact`，见
  `output.schema.json` / D-33-01..D-33-04）：
  - 信封：`type="illustration_revision"`，
    `schema_version="illustration-revision.v1"`；`owner_id` / `novel_id` /
    `branch` / `producing_skill="illustrate-scene"` /
    `producing_skill_version="1.0.0"` / `skill_version_id` / `model_lineage` /
    `source_versions` / `input_hash` / `evidence_refs`（必须 ⊆ 冻结 manifest
    白名单）/ `tool_runs`（ToolRun 血缘，含 `generate_image_candidate`）/
    `status="candidate"` / `parent_revision=null` / `normalization`
    （26-06 trail）。
  - 负载 `illustration_revision`：`revision_key` / `revision_number` /
    `asset_revision_id`（worker 产出的候选资产引用）/ `authority_space`
    （original | derivative）/ `fork`（仅 derivative）/ SceneSpec/prompt/
    Visual Bible/source-snapshot 血缘 + cutoff / provider/model/generator
    血缘 / `rights_status` / `consistency_verdict` + `fixture_set_hash` /
    `budget_settled_calls` + `budget_settled_cost_usd` / `review_state`
    **恒为 "candidate"**。
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。
  `skill_run_id` / `artifact_id` / `revision_id` 由服务端分配，本技能**不写任何
  持久化行**。
- Agent 创建 candidate Artifact 后**暂停**：状态推进（candidate → validated →
  proposal_ready）**只**由 Phase 33 确定性 validator（budget/rights/
  fidelity/consistency gate）与显式 review 事件完成；本技能/Agent **不能**直接
  推进、授予或伪造状态，也**不能**创建 ApprovalRequest / 调用 publisher /
  写 published 状态（全部属于 Phase 34）。

## 候选纪律（D-33-01..D-33-04，必须遵守）
- 唯一官方状态机是 `candidate → validated → proposal_ready`，**仅前向**；
  只有 Phase 33 确定性 validator 能推进状态。finalize 写入时 review_state
  恒为 `candidate`；任何非 candidate 声称（approval bypass / proposal_ready
  伪造 / published）→ fail closed。
- 每次 run 绑定 owner、novel、authority_space、可选 branch/fork（derivative
  mode）、SkillVersion、SkillRun、ToolRuns、source/input hashes、
  model/runtime lineage 与 Artifact revision（不可变、可版本化）。
- generated 输出是 **candidate**，直到显式人审/确定性 gate 才可能成为
  `proposal_ready`；它**绝不**自动进入 reader/export（D-33-03）。
- identity/style consistency 分数是 **review signal，不是 canon**（D-33-04）：
  绝不因分数把候选提升为事实或自动批准。
- 未支持细节 / 冲突主张 / 缺失引用只能以 review signals（consistency
  verdict / budget evidence / rights status）呈现，**绝不**伪装成 canon。

## 边界（必须遵守，fail closed）
- 零域写入：不写 Canon、不写 Visual Bible、不写 key-scene 集、不写
  scene_spec/prompt 域表、不写 illustration 域表、不写 ApprovalRequest 行、
  不写 published 状态、不产生任何域事实。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest 直接授予、无 Publisher 越权、无 Canon/域写回**：
  `approval_required_for` 为空——Phase 33 从不创建 ApprovalRequest；attempted
  approval 伪造（envelope status 非 candidate / review_state 非 candidate /
  受保护字段 / forbidden Tool/action）→ fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong branch/fork / stale base revision /
  stale input_hash / stale visual_bible_revision_hash / schema drift /
  unsafe normalization / 未知工具 / approval bypass / publisher 调用 /
  Original 突变 → 稳定 blocked/cancelled，无官方 Artifact 或域写入。
- `proposal_ready` handoff 是**只读**的：Phase 34 IllustrationAnchorProposal
  只接受 proposal_ready + rights cleared + 完整血缘的修订；candidate /
  validated / stale / wrong-scope 修订一律拒绝。
