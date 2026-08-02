# answer-reading-question（D-14 首技能）

给定一个问题与一本小说（fixture），产出一条**血缘绑定**的 Cited Answer 产物。
本技能只读，不写任何 Canon / 衍生内容；唯一输出通道是 Cited Answer Artifact
（D-01 / D-11：会话永远不是事实源）。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: answer-reading-question`，`version: 1.0.0`。
- `allowed_tools`（白名单，仅 6 个只读域工具）：`get_novel`、`get_chapter`、
  `search_novel_text`、`get_timeline`、`get_relationships`、`get_clues`。
- **不得调用** `get_narrative_memory` 或任何白名单外工具；发现即 fail closed。
- `read_permissions: [canon, derivative]`；`write_permissions: []`（只读）；
  `forbidden_spaces: [canon:original, derivative:write]`。
- `approval_required_for: []`——本技能无任何审批动作，无 ApprovalRequest 发起者。
- `budget` per-run 上限：`max_calls=20`、`max_input_tokens=30000`、
  `max_output_tokens=6000`、`max_cost_usd=1.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 不持久化任何行：产物仅由后端确定性 finalizer 写入。

## 执行流程（Question → QueryPlan → Tool Calls → EvidenceRef → Frozen Manifest → Cited Answer Artifact）

### 第 1 步：Question 归一化
- 从运行输入读取 `question`（非空字符串）与 `novel_id`（与路径 novel 一致）。
- 可选 `chapter_range`：`{chapter_start, chapter_end}`（含端点，章节号语义）。
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
- 本技能**不得调用** `get_narrative_memory`（allowed_tools 白名单排除，保持
  D-01 干净直到 Validator 故事落地；见 25.2-RESEARCH Open Question 4）。

### 第 3 步：Tool Calls
- 每个工具调用都走 25.2-02 门面（`POST /api/agent-tools/{tool_name}`），
  携带运行内部令牌；响应是 JSON 安全 payload。
- 记录每次调用的 `tool_name` / `params` / 响应摘要；所有失败映射为冻结
  agent-tool 错误码（forbidden/not_found/beyond_cutoff/budget_exceeded/
  timeout/output_too_large/invalid_input/upstream_error）。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。

### 第 4 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的证据，每个证据物化为一个 evidence_ref：
  `evidence_key`（形如 `selection:1`）、`source_type`、`source_id`、
  `chapter_id` / `chapter_number`、`source_start` / `source_end`、
  `content_hash`、`excerpt`、`version_lineage`。
- 只物化工具实际返回的内容；**绝不编造**引用（服务端 finalize 会用冻结
  manifest 白名单校验每个 citation）。

### 第 5 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest：
  `{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`。
- 冻结后不得再增删证据；finalize 把它落库到 run 上，作为 citation 白名单。

### 第 6 步：Cited Answer Artifact
- 组装 Cited Answer 信封（字段镜像后端 `CitedAnswerArtifact`，见
  `output.schema.json` / D-10）：
  - `type="cited_answer"`，`schema_version="cited-answer.v1"`
  - `owner_id` / `novel_id` / `branch`
  - `producing_skill` / `producing_skill_version` / `skill_version_id`
  - `model_lineage`（provider/model/revision）与 `source_versions`
  - `input_hash`（来自 run）
  - `evidence_refs`（必须 ⊆ frozen manifest 白名单）
  - `answer`：`answer_blocks`（每块 `block_id` / `text` / `evidence_refs`，
    引证必须合法）
  - `status="candidate"`，`parent_revision=null`
- 调用后端 `POST /api/agent/skill-runs/{run_id}/finalize`（或等价 finalize
  入口，stop_reason="stop"），由后端**确定性 finalizer**写入 candidate 产物
  + 首个不可变修订。本技能自身**不写任何持久化行**。

## 边界（必须遵守）
- 只读：不写 Canon、不写衍生章节、不产生任何域事实。
- 不调用 `get_narrative_memory`；不访问白名单外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 会让 run 以 `failed_validation`
  失败，且什么都不写。
- 取消（cancel_requested 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行。
