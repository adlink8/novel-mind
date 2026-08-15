# answer-reading-question（Phase 26 版本化只读技能 / D-11 / D-14）

给定一个问题与一本小说（可选 Reader/Analysis anchor 与 source snapshot），
产出一条**血缘绑定**的 Cited Answer Artifact。本技能只读，不写任何
Canon / 衍生内容；唯一输出通道是后端确定性 finalizer 持久化的
CitedAnswerArtifact（D-01 / D-11：会话永远不是事实源）。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: answer-reading-question`，`version: 1.0.0`（Phase 26 绑定版本）。
- `allowed_tools`（编排 allowlist，仅 6 个只读域工具）：`get_novel`、
  `get_chapter`、`search_novel_text`、`get_timeline`、`get_relationships`、
  `get_clues`。
- **不得调用** `get_narrative_memory` 或任何 allowlist 外工具；发现即
  fail closed（Pi 只能编排声明的 Domain Tool allowlist）。
- `read_permissions: [canon, derivative]`；`write_permissions: []`（只读）；
  `forbidden_spaces: [canon:original, derivative:write]`。
- `approval_required_for: []`——本技能**无任何审批动作**：不发起
  ApprovalRequest、不调用 Publisher、不执行 promotion / active-pointer /
  任何域写入。任何尝试的 ApprovalRequest / Publisher 动作 → fail closed。
- `budget` per-run 上限：`max_calls=20`、`max_input_tokens=30000`、
  `max_output_tokens=6000`、`max_cost_usd=1.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact
  revision（artifact_id / revision_id 由后端确定性 finalizer 在写入时分配）。

## 执行流程（Question → QueryPlan → Tool Calls → EvidenceRef → Frozen Manifest → 共享 normalizer/validator → Cited Answer Artifact）

### 第 1 步：Question 归一化
- 从运行输入读取 `question`（非空字符串）与 `novel_id`（与路径 novel 一致）。
- 可选 Phase 26 锚定：`chapter_range`（Analysis 结构锚，含端点）或
  `selection`（Reader 选区锚：chapter_id / source_start / source_end /
  chapter_content_hash），以及 `source_snapshot`（D-07 冻结快照血缘）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。

### 第 2 步：QueryPlan
- 按问题形态选择工具组合，例如：
  - 事实定位 → `search_novel_text`（query 取问题主干，top_k ≤ 10）。
  - 章节上下文 → `get_chapter`（chapter_id 来自小说章节清单）。
  - 人物/关系 → `get_relationships`（按人物名）。
  - 事件/时间线 → `get_timeline`（章节范围锚定）。
  - 伏笔/线索 → `get_clues`。
  - 元数据兜底 → `get_novel`。
- Phase 26 确定性能力（维度检索、证据物化、冻结 manifest）由后端
  QueryPlan 服务（`services/queryplan/`）与确定性 finalizer 承担；本技能
  只编排上述 allowlist 工具，确定性服务拥有 legality 权威。
- **不得调用** `get_narrative_memory`（allowlist 排除，保持 D-01 干净；
  叙事记忆维度数据仅经服务端候选通道，heuristic recall candidate-only）。

### 第 3 步：Tool Calls
- 每个工具调用都走 25.2-02 门面（`POST /api/agent-tools/{tool_name}`），
  携带运行内部令牌；响应是 JSON 安全 payload。
- 记录每次调用的 `tool_name` / `params` / 响应摘要；所有失败映射为冻结
  agent-tool 错误码（forbidden/not_found/beyond_cutoff/budget_exceeded/
  timeout/output_too_large/invalid_input/upstream_error）。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 4 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08）：`evidence_key`（形如 `evidence:1`）、
  `source_type`、`source_id`、`chapter_id` / `chapter_number`、
  `source_start` / `source_end`、`content_hash`、`excerpt`、
  `version_lineage`。
- 只物化工具实际返回的内容；**绝不编造**引用（服务端 finalize 会用冻结
  manifest 白名单校验每个 citation）。
- 摘要、score、routing 元数据、聊天文本、heuristic 候选**都不是** leaf
  evidence，不得进入 evidence_refs（D-08 / D-15）。

### 第 5 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest：
  `{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`。
- 冻结后不得再增删证据；finalize 把它落库到 run 上，作为 citation 白名单
  （leaf-evidence 权威校验在服务端 `validate_answer_against_manifest`）。

### 第 6 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、
  enum canonicalization、无歧义 container-shape 修复；本技能**不引入任何
  本地修复路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在
  官方 CitedAnswerArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段（evidence_refs/owner/cutoff/
  authority/branch/fork/approval）合成尝试 → 稳定 `blocked`，零写入。

### 第 7 步：Cited Answer Artifact
- 组装 Cited Answer 信封（字段镜像后端 `CitedAnswerArtifact`，见
  `output.schema.json` / D-10 / 26-06）：
  - `type="cited_answer"`，`schema_version="cited-answer.v1"`
  - `owner_id` / `novel_id` / `branch`
  - `producing_skill` / `producing_skill_version` / `skill_version_id`
  - `model_lineage`（provider/model/revision）与 `source_versions`（source
    snapshot 血缘）
  - `input_hash`（来自 run）
  - `evidence_refs`（必须 ⊆ frozen manifest 白名单）
  - `answer`：`answer_blocks`（每块 `block_id` / `text` / `evidence_refs`，
    引证必须合法；无 evidence 必须 abstain，D-09）
  - `status="candidate"`，`parent_revision=null`
  - `normalization`（raw_hash / repaired_hash / normalization_actions /
    warnings，26-06 必需 trail）
- 调用后端 `POST /api/agent/skill-runs/{run_id}/finalize`（或等价 finalize
  入口，stop_reason="stop"），由后端**确定性 finalizer**写入 candidate 产物
  + 首个不可变修订并分配 artifact_id / revision_id。本技能自身**不写任何
  持久化行**。

## 边界（必须遵守，fail closed）
- 只读：不写 Canon、不写衍生章节、不产生任何域事实。
- 不调用 `get_narrative_memory`；不访问白名单外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 会让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest、无 Publisher**：本技能不发起审批、不执行发布或
  promotion；attempted ApprovalRequest / Publisher 动作 → fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  schema drift / unsafe normalization / 未知工具 → 稳定 blocked/cancelled，
  无官方 Artifact 或域写入。
- heuristic recall（候选）永远 candidate-only：无 evidence_refs 的
  cited_answer → `BLOCKED_NO_EVIDENCE`，不进 cited-answer 网关。
