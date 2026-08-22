# evaluate-reading-skill-runs（Phase 29 版本化阅读 QA 评估技能 / REQ-QA-01..03 / REQ-AGENT-03 / D-02..D-05）

给定 owner/novel/branch 范围内的**冻结** SkillRun / ToolRun / Artifact /
FrozenManifest 与 dataset 血缘引用，通过 **4 个只读域工具**读取冻结的数据集/证据
记录，产出**血缘绑定**的 SkillEvaluationArtifact。裁决（verdict）只允许
`qualified_candidate` 或 `blocked`（D-05：无 promotion）。本技能只读，不写任何
Canon / 数据集 / 领域状态；唯一输出通道是后端确定性 finalizer 持久化的
SkillEvaluationArtifact（D-02/D-04：终态明确，无 silent pending；会话永远不是
事实源）。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: evaluate-reading-skill-runs`，`version: 1.0.0`（Phase 29 绑定版本）。
- `allowed_tools`（编排 allowlist，4 个只读域工具）：`get_novel`、
  `get_evidence_span`、`get_narrative_memory`、`search_novel_text`。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- `read_permissions: [canon, narrative_memory, qualification]`；
  `write_permissions: []`（只读）；`forbidden_spaces: [canon:original,
  qualification:write, derivative:write]`。
- `approval_required_for: []`——本技能**无任何审批动作**：不发起
  ApprovalRequest、不调用 Publisher、不执行 promotion / active-pointer /
  任何域写入。任何尝试的 ApprovalRequest / Publisher 动作 → fail closed。
- `budget` per-run 上限：`max_calls=80`、`max_input_tokens=60000`、
  `max_output_tokens=12000`、`max_cost_usd=5.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  ToolRuns、input hash、source snapshot、model/runtime lineage 与不可变
  Artifact revision（artifact_id / revision_id 由后端确定性 finalizer 在
  写入时分配）。

## 执行流程（冻结输入 → Tools → 确定性评估 → SkillEvaluationArtifact）

### 第 1 步：冻结输入归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）与冻结评估引用：
  `dataset`（`dataset_version` + `source_snapshot_hash`，D-02）与
  `evaluated_run`（被评估 run / artifact / revision id + `content_hash`）。
- 可选 `branch`（衍生分支）与 `evaluation`（`top_k`；`cutoff` 服务端强制）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 被评估的 SkillRun / ToolRun / Artifact / FrozenManifest 记录必须是**冻结**
  终态（completed/failed + 不可变 revision）；running 等可变 Agent 状态绝不
  作为评估证据（D-05）。最终 scope / cutoff 由服务端强制。

### 第 2 步：工具编排（只读冻结数据集/证据记录）
- 按评估需求选择 allowlist 工具，例如：
  - 小说与章节 scope → `get_novel`。
  - 冻结 leaf 证据跨度（D-07/D-08）→ `get_evidence_span`。
  - 叙事记忆候选视图（versions/tree）→ `get_narrative_memory`
    （`release_status: "candidate"`，候选数据不是 Canon）。
  - 原文检索（候选/基线证据基线）→ `search_novel_text`。
- Phase 29 确定性评估（gold set 审计、检索/引证/答案度量、parity、two-value
  verdict）由后端 `qualification/runner.py` 承担（不可变 runner）；本技能只
  编排上述 allowlist 工具，确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码（forbidden/not_found/beyond_cutoff/budget_exceeded/
  timeout/output_too_large/invalid_input/upstream_error）。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08）：`evidence_key`、`source_type`、`source_id`、
  `chapter_id`/`chapter_number`、`source_start`/`source_end`、`content_hash`、
  `version_lineage`。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest
  白名单校验）。

### 第 4 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest
  （`{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`）。
- 冻结后不得再增删证据；finalize 落库到 run 上作为引证白名单。

### 第 5 步：确定性评估（D-02..D-05，评估的是一次冻结）
- 后端 `run_qualification` 对**冻结** gold set / candidate / baseline /
  manifest 逐项校验（header 血缘 → manifest 契约与 parity → dataset 审计 →
  sample coverage parity → provider/budget → rubric 审计），产出密封
  `QualificationReport`。
- verdict 只允许 `qualified_candidate` 或 `blocked`；`blocked_reasons`
  逐个暴露，绝不隐藏在一个聚合分数后（D-03）。
- **绝不重跑可变 Agent 会话状态**：评估对象是冻结的 SkillRun/ToolRun/Artifact/
  Manifest/model/source/dataset 版本，任何可变 replays 都不是评估证据。

### 第 6 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、enum
  canonicalization、无歧义 container-shape 修复；本技能**不引入任何本地修复
  路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  SkillEvaluationArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval）→ 稳定 `blocked`，零写入。

### 第 7 步：SkillEvaluationArtifact
- 组装评估信封（字段镜像后端 `SkillEvaluationArtifact`，见
  `output.schema.json`）：
  - `type="skill_evaluation"`，`schema_version="skill-evaluation.v1"`
  - `owner_id` / `novel_id` / `branch`
  - `producing_skill` / `producing_skill_version` / `skill_version_id`
  - `model_lineage`（provider/model/revision）与 `source_versions`
  - `input_hash`（来自 run）
  - `evidence_refs`（必须 ⊆ frozen manifest 白名单）
  - `evaluated_run`：被评估冻结 SkillRun + ToolRun 血缘（`run_id`、
    `status`（仅 completed/failed）、`branch`、`input_hash`、`tool_runs`）
  - `evaluated_artifact`：被评估冻结 Artifact 修订血缘（`artifact_id`、
    `revision_id`、`type`、`schema_version`、`content_hash`、`status`）
  - `report`：密封 QualificationReport（`verdict` / `checksum` / `header` /
    `buckets` / `blocked_reasons` / `operations` / `manifest`，D-02/D-05）
  - `tool_runs`：`[{tool_name, calls}]`（本 run 的 ToolRun 血缘）
  - `status="candidate"`，`parent_revision=null`
  - `normalization`（raw_hash / repaired_hash / normalization_actions /
    warnings，26-06 必需 trail）
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。本技能
  自身**不写任何持久化行**。

## 权威边界（必须遵守，fail closed）
- **评估冻结，不重跑可变状态**：被评估对象只读冻结的 SkillRun / ToolRun /
  Artifact / Manifest / model / source / dataset 版本；可变 Agent 会话状态
  绝不作为评估证据（D-05）。
- 只读：不写 Canon、不写数据集、不产生任何域事实；裁决（verdict）不授予任何
  promotion / active-pointer 能力。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest、无 Publisher**：本技能不发起审批、不执行发布或
  promotion；attempted ApprovalRequest / Publisher 动作 → fail closed。
  Agent/UI 无法审批、覆盖或变更裁决。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  schema drift / 未知工具 / 未声明权限 / report checksum 重放失败 /
  verdict 非 two-value（如 promotion）/ unsafe normalization / approval
  bypass → 稳定 blocked/cancelled，无官方 Artifact 或域写入。
