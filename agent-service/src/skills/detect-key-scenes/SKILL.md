# detect-key-scenes（Phase 31 版本化关键场景检测技能 / REQ-VIS-02 / REQ-AGENT-02/03/04）

给定一本小说（owner/novel/branch 血缘绑定），通过 **5 个只读域工具**收集证据与候选信号，
产出**血缘绑定**的 SceneCandidateArtifact——携带完整 `SceneCandidateSetContract`
（ordered candidates / diversity keys / evidence refs / spoiler cutoff /
salience reasons / advisory `speaker_dialogue_signal`）。本技能只读，不写任何
Canon / key-scene 候选集 / review 决策；唯一输出通道是后端确定性 finalizer
持久化的 SceneCandidateArtifact（candidate-only）。`key_scene:approve` 用户选择/
审查后候选集才 frozen（D-31-04）；生成**绝不**写入或提升到 Canon / active reader
state（D-31-01）；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: detect-key-scenes`，`version: 1.0.0`（Phase 31 绑定版本）。
- `allowed_tools`（编排 allowlist，5 个只读域工具）：`get_events`、
  `get_character_state`、`get_relationships`、`get_visual_bible`、
  `get_evidence_span`。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, key_scene:write, visual_bible:write, derivative:write]`。
- `approval_required_for: [key_scene:approve]`——用户选择/审查是成为 frozen
  key-scene 集的显式、append-only 服务端状态迁移（D-31-04）。Agent 只能创建
  candidate Artifact 并暂停等待审批；**不能**直接授予或伪造批准。除声明审批动作
  外的任何 ApprovalRequest / Publisher / promotion / active-pointer 动作 →
  fail closed。
- `budget` per-run 上限：`max_calls=60`、`max_input_tokens=60000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  ToolRuns、input hash、source snapshot、model/runtime lineage 与不可变
  Artifact revision。

## 执行流程（novel/cutoff/source → Tools → EvidenceRef → candidate → 确定性校验 → SceneCandidateArtifact → 用户选择/审查）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；可选 `branch`、`version_key`、
  `cutoff_chapter`、`source_snapshot`（D-31 冻结快照血缘）与
  `approved_visual_bible_revision_id/hash`（D-31-01 已批准 Visual Bible 血缘）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff / evidence 由服务端强制（D-31-01/D-31-02）；显式 cutoff
  超限 → `beyond_cutoff`。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - 世界模型事件/因果候选 → `get_events`。
  - 角色状态/目标/动机（D-31 候选信号）→ `get_character_state`。
  - 角色关系图（cast/place/time 坐标佐证）→ `get_relationships`。
  - 已批准 Visual Bible 修订（D-31-01 服务端重验）→ `get_visual_bible`。
  - 章节原文 leaf 证据跨度（evidence_refs 唯一物化通道）→ `get_evidence_span`。
- Phase 31 确定性能力（boundary detection / multi-signal scoring /
  diversity-aware ranking / spoiler cutoff）由后端 `key_scenes/` 服务承担；
  本技能只编排上述 allowlist 工具，确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08 / D-31-02）。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest
  白名单校验）。
- 摘要、score、diversity key、`speaker_dialogue_signal` 偏移、聊天文本、
  heuristic 候选**都不是** leaf evidence，不得进入 evidence_refs（D-08 / D-31-05）。

### 第 4 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest
  （`{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`）。
- 冻结后不得再增删证据；finalize 落库到 run 上作为引证白名单。

### 第 5 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、enum
  canonicalization、无歧义 container-shape 修复；本技能**不引入任何本地修复
  路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门 + `SceneCandidateSetContract`
  域校验）→ 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  SceneCandidateArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval/approval_state）→ 稳定 `blocked`，零写入。

### 第 6 步：SceneCandidateArtifact
- 组装关键场景信封（字段镜像后端 `SceneCandidateArtifact`，见
  `output.schema.json` / D-31-01..D-31-05）：
  - `type="scene_candidate"`，`schema_version="scene-candidate.v1"`
  - `owner_id` / `novel_id` / `branch`
  - `producing_skill="detect-key-scenes"` / `producing_skill_version="1.0.0"` /
    `skill_version_id`
  - `model_lineage` 与 `source_versions`
  - `input_hash`（来自 run）
  - `evidence_refs`（必须 ⊆ frozen manifest 白名单）
  - `scene_candidate_set`：完整 `SceneCandidateSetContract`（schema_version、
    artifact_kind、owner/novel、version_key、revision_number、
    parent_set_id、source_snapshot_id/hash、cutoff_chapter、
    schema/policy/manifest hash、detector_id/version、
    approved_visual_bible_revision_id/hash、candidates、`review_state="candidate"`）
  - `tool_runs`：`[{tool_name, calls}]`（ToolRun 血缘）
  - `status="candidate"`，`parent_revision=null`
  - `normalization`（raw_hash / repaired_hash / normalization_actions /
    warnings，26-06 必需 trail）
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。
  `skill_run_id` / `artifact_id` / `revision_id` 由服务端分配，本技能**不写任何
  持久化行**。
- Agent 创建 candidate Artifact 后**暂停等待用户选择/审查**：`key_scene:approve`
  由 FastAPI review/freeze 端点执行（score/diversity/density/spoiler validator
  门 fail closed）；本技能/Agent **不能**直接授予或伪造批准。

## 候选纪律（D-31-01/D-31-04/D-31-05，必须遵守）
- 每个候选带 `evidence_ranges`（唯一 citation 权威，D-31-02）：source snapshot
  + chapter/range + content hash + cutoff；越界 offset / 错 hash / 跨 owner
  证据 → fail closed。
- `speaker_dialogue_signal`（REQ-VIS-06 / D-31-05）是**诊断候选元数据**：
  `speaker_offsets` / `dialogue_offsets` / `confidence` / `warnings` 只说明
  heuristic 如何召回/排序候选；**绝不**进入 evidence_ranges、绝不作为 citation
  或 Canon 权威、绝不作为审批/发布原因。缺失或歧义归因保持
  `unavailable`/`ambiguous` + warnings，绝不静默升级。
- 候选是**不可变、可版本化**的 candidate：`review_state` 恒为 `"candidate"`；
  只有显式、append-only 的 `key_scene:approve`（服务端 review/freeze 事件，
  幂等 decision_key）才把状态迁移到 `approved`/frozen。绝不因生成而写入或提升到
  Canon / active reader state（D-31-01）。
- 用户选择/审查 + 确定性 score/diversity/spoiler 校验后候选集才 frozen；无批准
  无 promotion。

## 边界（必须遵守，fail closed）
- 只读：不写 Canon、不写 key-scene 候选集、不写 review 决策、不产生任何域事实。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest 直接授予、无 Publisher 越权**：本技能只创建 candidate
  Artifact 并暂停等待审批；`key_scene:approve` 的授权只属于 FastAPI 确定性
  review/freeze 端点；attempted approval 伪造（envelope status 非 candidate /
  scene_candidate_set.review_state 非 candidate / 受保护字段）→ fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  schema drift / unsafe normalization / 未知工具 / approval bypass /
  heuristic 信号被当作证据（进入 evidence_refs 或审批原因）→ 稳定
  blocked/cancelled，无官方 Artifact 或域写入。
