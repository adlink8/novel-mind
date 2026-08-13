# propose-world-model-candidates（Phase 27 版本化世界模型候选技能 / D-01..D-06）

给定一本小说（owner/novel/branch + 可选 version/cutoff），通过 7 个只读域工具
收集证据，产出一条**血缘绑定**的 World Model Candidate Artifact。本技能只提案
**候选投影**（typed world-model candidates），不写任何 Canon / 域状态；唯一
输出通道是后端确定性 finalizer 持久化的 `WorldModelCandidateArtifact`
（candidate-only）。**确定性 WorldModel Validator/Gate 拥有 legality /
state-transition / publication 权威**——Agent 永远不能直接发布 Canon fact
（D-01 / D-02 / D-06：会话与 Agent 提案都不是事实源）。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: propose-world-model-candidates`，`version: 1.0.0`（Phase 27 绑定版本）。
- `allowed_tools`（编排 allowlist，仅 7 个只读域工具）：`get_events`、
  `get_character_state`、`get_character_knowledge`、`get_relationships`、
  `get_world_rules`、`get_evidence_span`、`search_novel_text`。
- **不得调用** allowlist 外的任何工具（含 `get_narrative_memory` 等）——
  发现即 fail closed（Pi 只能编排声明的 Domain Tool allowlist）。
- `read_permissions: [canon, derivative, world_model]`；`write_permissions: []`
  （Agent 零域写入）；`forbidden_spaces: [canon:original, derivative:write]`。
- `approval_required_for: [world_model:user_interpretation]`——本技能唯一可能
  发起的审批动作是 **user interpretation 的 owner 作用域确认**（D-06）。
  该确认只能由后端 ApprovalRequest 决策路径完成；Agent **不直接持有**任何
  审批/发布权力。`canon_fact` 发布所需审批（D-01）是 Gate/Publisher 的权威，
  不在本技能授权内。
- `budget` per-run 上限：`max_calls=30`、`max_input_tokens=40000`、
  `max_output_tokens=8000`、`max_cost_usd=1.50`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  input hash、source snapshot、model/runtime lineage、ToolRun 血缘与不可变
  Artifact revision（artifact_id / revision_id 由后端确定性 finalizer 分配）。

## 执行流程（Scope → Tool Calls → Evidence → Candidates → Artifact）

### 第 1 步：Scope 归一化
- 从运行输入读取 `novel_id`（必填，与路径 novel 一致）与可选 `branch` /
  `version_id` / `cutoff` / `focus` / `source_snapshot`。
- 输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能不修改
  input，保证重放可追溯。
- `cutoff` / `version_id` 只是声明意图；最终 scope 由服务端强制
  （D-05：超过截止点 → beyond_cutoff；错误 owner/version → blocked）。

### 收敛预算（硬约束，先于一切编排）
- 本技能 per-run `max_calls=30`，**工具调用是稀缺预算**：
  `search_novel_text` 最多 3 次；`get_evidence_span` 最多 6 次（只为最终
  入选 claim 的引用物化）；其余世界模型工具合计不超过 8 次。
- **claims 上限 5 条**（宁缺毋滥）；已有 ≥1 个物化 span + ≥1 条可写
  claim 时，立即停止工具调用、输出最终 JSON。

### 第 2 步：Tool Calls（只读证据收集）
- 每个工具调用都走 25.2-02 门面（`POST /api/agent-tools/{tool_name}`），携带
  运行内部令牌；响应是 JSON 安全 payload。
- 典型编排：
  - 事件/因果 → `get_events`（version/cutoff 服务端强制）。
  - 角色状态/目标/动机 → `get_character_state`（subject，D-05 cutoff/POV）。
  - 角色知识 → `get_character_knowledge`（subject，D-05）。
  - 世界规则/例外 → `get_world_rules`（D-04 例外 first-class）。
  - 关系 → `get_relationships`（人物关系图）。
  - 正文检索（候选区间发现，命中自带 chapter_id/offsets）→ `search_novel_text`。
  - leaf 证据原文 → `get_evidence_span`（chapter+offsets 物化；`content_hash`
    可选——省略时服务端计算返回，提供时校验匹配）。
- 所有失败映射为冻结 agent-tool 错误码（forbidden/not_found/beyond_cutoff/
  budget_exceeded/timeout/output_too_large/invalid_input/upstream_error）。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：EvidenceRef materialization + Frozen Manifest
- 从工具响应挑选**可直接引用**的 leaf/raw 证据，每个物化为一个 evidence_ref
  （D-07/D-08：evidence_key、chapter_id/number、source_start/end、
  content_hash、source_snapshot_hash、excerpt）。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest 白名单
  校验每个 evidence_ref）。
- 把所有物化 evidence_ref 冻结成不可变 manifest
  `{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`；冻结后不得
  再增删证据（finalize 落库并作为引用白名单）。

### 第 4 步：Candidates（typed world-model candidates）
- 把工具证据综合成**候选主张**（claim），每项含：`claim_kind`（event /
  causal_edge / character_state / character_knowledge / world_rule /
  rule_exception / entity / entity_link）、`claim_key`、`proposition`、
  `authority`（D-01 四 label：canon_fact / probable_inference /
  literary_interpretation / user_interpretation）、`confidence`、
  `disclosure_cutoff`、`evidence_refs`（⊆ 冻结白名单）、`details`。
- `authority` 标签原样保留；**绝不静默升级** probable_inference /
  literary_interpretation / user_interpretation 为 canon_fact（D-01）。
- `candidates.tool_runs` 记录本 run 使用的工具与调用次数（ToolRun 血缘）。
- 候选只是提案；Gate 逐条裁决后才可能成为持久化投影（D-02 immutable
  candidates-only）。

## 输出骨架（硬性契约，fail closed）

- **禁止在不调用任何工具的情况下直接给出最终回答**：必须先调用本技能
  allowlist 内的工具收集证据（至少 1 次成功调用），再输出最终 JSON。
- 最终回答**必须且仅**是一个 JSON 对象（无 markdown、无解释文字、无 code
  fence），顶层**必须**包含 `candidates` 对象，骨架如下：

```json
{
  "candidates": {
    "claims": [
      {
        "claim_kind": "world_rule",
        "claim_key": "稳定的机器可读键",
        "proposition": "候选主张的自然语言陈述",
        "authority": "probable_inference",
        "confidence": 0.6,
        "disclosure_cutoff": 1,
        "evidence_refs": ["仅填工具响应里实际返回的证据 key"],
        "details": {}
      }
    ]
  }
}
```

- `claim_kind` ∈ event / causal_edge / character_state / character_knowledge /
  world_rule / rule_exception / entity / entity_link；`authority` 四值见第 4 步，
  绝不静默升级。
- `evidence_refs` 只能引用**本 run `get_evidence_span` 实际返回**的
  `evidence_key`（选择制：先 `search_novel_text` 发现区间，再
  `get_evidence_span` 物化，最后按 key 引用）；编造或引用未物化的 key →
  run 失败，零写入。
- 若工具证据不足以提出任何候选：仍输出骨架 JSON（`claims` 可以为空数组），
  在 `details` 中说明 abstain 原因；绝不输出散文。

### 第 5 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、
  enum canonicalization、无歧义 container-shape 修复；本技能不引入任何本地
  修复路径。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash 重放
  + 受保护字段门 + leaf-evidence 资格门）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  WorldModelCandidateArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段（evidence_refs/owner/cutoff/
  authority/branch/fork/approval）合成尝试 → 稳定 `blocked`，零写入。

### 第 6 步：World Model Candidate Artifact
- 组装信封（字段镜像后端 `WorldModelCandidateArtifact`，见 output.schema.json）：
  - `type="world_model_candidate"`，
    `schema_version="world-model-candidate.v1"`
  - `owner_id` / `novel_id` / `branch`
  - `producing_skill` / `producing_skill_version` / `skill_version_id`
  - `model_lineage`（provider/model/revision）与 `source_versions`
  - `input_hash`（来自 run）
  - `evidence_refs`（必须 ⊆ frozen manifest 白名单）
  - `candidates`（projection_version / tool_runs / claims）
  - `status="candidate"`，`parent_revision=null`
  - `normalization`（raw_hash / repaired_hash / normalization_actions /
    warnings，26-06 必需 trail）
- 调用后端 `POST /api/agent/skill-runs/{run_id}/finalize`（或等价 finalize
  入口，stop_reason="stop"），由后端**确定性 finalizer**写入 candidate 产物
  + 首个不可变修订并分配 artifact_id / revision_id。本技能自身**不写任何
  持久化行**。

## 边界（必须遵守，fail closed）
- **Agent 禁止直接发布 Canon fact**：信封内的 canon_fact 主张只有在确定性
  Gate 显式审批后才可能持久化；Agent 任何直接 publish / promotion /
  active-pointer 动作 → fail closed。
- 只读：不写 Canon、不写衍生内容、不写任何域状态（write_permissions 为空）。
- 不调用 allowlist 外的任何工具（`get_narrative_memory` 等一律拒绝）。
- 引证只能来自冻结 manifest；任何未知 ref 会让 run 以 `failed_validation`
  失败，且什么都不写。
- 审批边界：本技能只声明 `world_model:user_interpretation` 一个审批动作；
  user interpretation 主张必须经 owner 作用域确认（D-06）。attempted 审批
  伪造 / 越权审批 / 直接状态写入 → fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  schema drift / unsafe normalization / 未知工具 / 无证据启发式候选 →
  稳定 blocked/cancelled，无官方 Artifact 或域写入。
