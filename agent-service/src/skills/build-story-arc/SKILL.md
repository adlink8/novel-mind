# build-story-arc（Phase 28 版本化故事弧技能 / D-05 / D-07 / D-09）

给定一本小说（owner/novel/branch 血缘绑定），通过 **8 个只读域工具**收集章节与
叙事记忆候选证据，产出一条**血缘绑定**的 StoryArcArtifact——包含
OutlineCandidateArtifact（连续弧线候选）与 MainlineCandidateArtifact
（Volume/Global 候选投影）。本技能只读，不写任何 Canon / 叙事记忆；唯一输出通道是
后端确定性 finalizer 持久化的 StoryArcArtifact（candidate-only）。生成**绝不**
写入或提升到 Canon（D-09）；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: build-story-arc`，`version: 1.0.0`（Phase 28 绑定版本）。
- `allowed_tools`（编排 allowlist，8 个只读域工具）：`get_chapter`、
  `get_evidence_span`、`get_events`、`get_character_state`、
  `get_relationships`、`get_clues`、`get_world_rules`、
  `get_narrative_memory`。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- `read_permissions: [canon, derivative, narrative_memory]`；
  `write_permissions: []`（只读）；`forbidden_spaces: [canon:original,
  derivative:write]`。
- `approval_required_for: []`——本技能**无任何审批动作**：不发起
  ApprovalRequest、不调用 Publisher、不执行 promotion / active-pointer /
  任何域写入。任何尝试的 ApprovalRequest / Publisher 动作 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=60000`、
  `max_output_tokens=12000`、`max_cost_usd=3.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  ToolRuns、input hash、source snapshot、model/runtime lineage 与不可变
  Artifact revision。

## 执行流程（novel/range → Tools → EvidenceRef → candidate → 确定性校验 → StoryArcArtifact）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；可选 `chapter_range`（弧线
  构建范围锚，含端点）、`branch`、`cutoff` 与 `source_snapshot`（D-07 冻结
  快照血缘）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff 由服务端强制（D-05/D-12）；显式 cutoff 超限 →
  `beyond_cutoff`。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - 章节正文与证据跨度 → `get_chapter`、`get_evidence_span`。
  - 事件/人物状态/规则信号（可选 traceable signals，D-06）→ `get_events`、
    `get_character_state`、`get_world_rules`、`get_relationships`、
    `get_clues`。
  - 叙事记忆候选视图（versions/tree）→ `get_narrative_memory`
    （`release_status: "candidate"`，候选数据不是 Canon）。
- Phase 28 确定性能力（弧线规划、边界不确定性、Volume/Global 投影、终态聚合）
  由后端 `narrative_memory/` 服务承担（arc_planner / global_builder /
  builder_worker 等）；本技能只编排上述 allowlist 工具，确定性 validators
  拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08）。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest
  白名单校验）。
- 摘要、score、routing 元数据、聊天文本、heuristic 候选**都不是** leaf
  evidence，不得进入 evidence_refs（D-08）。

### 第 4 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest
  （`{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`）。
- 冻结后不得再增删证据；finalize 落库到 run 上作为引证白名单。

### 第 5 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、enum
  canonicalization、无歧义 container-shape 修复；本技能**不引入任何本地修复
  路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  StoryArcArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval）→ 稳定 `blocked`，零写入。

### 第 6 步：StoryArcArtifact
- 组装故事弧信封（字段镜像后端 `StoryArcArtifact`，见 `output.schema.json` /
  D-05/D-09）：
  - `type="story_arc"`，`schema_version="story-arc.v1"`
  - `owner_id` / `novel_id` / `branch`
  - `producing_skill` / `producing_skill_version` / `skill_version_id`
  - `model_lineage` 与 `source_versions`
  - `input_hash`（来自 run）
  - `evidence_refs`（必须 ⊆ frozen manifest 白名单）
  - `outline_candidate`：OutlineCandidateArtifact（schema_version、
    policy_version、owner/novel/version、source_snapshot_hash、
    hierarchy_build_id、hierarchy_checksum、input_hash、chapter_min/max、
    arcs、covered_ranges、gaps、overlaps、`candidate_status="candidate"`、
    lineage、checksum）
  - `mainline_candidate`：MainlineCandidateArtifact（volumes、
    global_projection、covered_ranges、gaps、overlaps、
    `candidate_status="candidate"`、lineage、checksum）
  - `tool_runs`：`[{tool_name, calls}]`（ToolRun 血缘）
  - `status="candidate"`，`parent_revision=null`
  - `normalization`（raw_hash / repaired_hash / normalization_actions /
    warnings，26-06 必需 trail）
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。本技能
  自身**不写任何持久化行**。

## 候选纪律（D-05/D-09，必须遵守）
- Outline / Mainline 候选保留 source snapshot/range、input hashes、证据血缘、
  边界不确定性与 `candidate_status="candidate"`。
- 候选**绝不进入 Canon**（绝不因生成而写入或提升到 Canon）：不调用 Publisher、
  不做 promotion、不动 active-pointer、不写 Canon 表；任何 `candidate_status`
  非 `candidate` 或尝试 Canon 提升 → fail closed。
- 覆盖（coverage）如实保留：gap 章节（isolated/blocked/absent）与重叠显式
  呈现，绝不伪装为完整事实。
- 情绪记忆明确 out of scope：schema 与 Artifact 不含任何 emotional-memory
  字段；Agent 会话状态与情绪记忆绝不作为领域事实。

## 边界（必须遵守，fail closed）
- 只读：不写 Canon、不写衍生章节、不产生任何域事实；叙事记忆候选保持
  candidate-only。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest、无 Publisher**：本技能不发起审批、不执行发布或
  promotion；attempted ApprovalRequest / Publisher 动作 → fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  schema drift / unsafe normalization / 未知工具 / Canon 提升尝试 →
  稳定 blocked/cancelled，无官方 Artifact 或域写入。
