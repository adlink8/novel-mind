# detect-key-scenes（Phase 31 版本化关键场景检测技能 / REQ-VIS-02 / REQ-AGENT-02/03/04）

给定一本小说（owner/novel/branch 血缘绑定），通过 **6 个只读域工具**收集证据与候选信号，
产出**血缘绑定**的 SceneCandidateArtifact——携带完整 `SceneCandidateSetContract`
（ordered candidates / diversity keys / evidence refs / spoiler cutoff /
salience reasons / advisory `speaker_dialogue_signal`）。本技能只读，不写任何
Canon / key-scene 候选集 / review 决策；唯一输出通道是后端确定性 finalizer
持久化的 SceneCandidateArtifact（candidate-only）。`key_scene:approve` 用户选择/
审查后候选集才 frozen（D-31-04）；生成**绝不**写入或提升到 Canon / active reader
state（D-31-01）；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: detect-key-scenes`，`version: 1.1.0`（契约更新：选择制证据 + 程序产出哈希）。
- `allowed_tools`（编排 allowlist，6 个只读域工具）：`get_events`、
  `get_character_state`、`get_relationships`、`get_visual_bible`、
  `get_evidence_span`、`search_novel_text`。
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

### 收敛预算（硬约束，先于一切编排）
- 本技能 per-run `max_calls=60`，**工具调用是稀缺预算**，按下限规划：
  `search_novel_text` **最多 4 次**（不同关键词各一次即可，命中相似就停手）；
  `get_evidence_span` **最多 8 次**（每个候选 1 次，只为最终入选的候选物化）；
  其余工具合计不超过 6 次。
- **候选上限 5 个**（宁缺毋滥）；达到 1 个合格候选即可收尾输出。
- 任何时候只要已有 ≥1 个物化 span，就**立即停止搜索、输出最终 JSON**；
  反复换关键词搜索 = 烧光预算 = run 失败。

### 第 0 步：文本发现（选择制证据的前提）
- 用 `search_novel_text` 按问题/主题检索正文，命中行携带 `chapter_id` /
  `source_start` / `source_end` / `content_snippet`——**只能从这些命中（或
  其它工具返回的明文跨度）里挑选候选场景区间，绝不凭空编造 offsets**。

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
  - 正文检索（候选区间发现）→ `search_novel_text`。
- Phase 31 确定性能力（boundary detection / multi-signal scoring /
  diversity-aware ranking / spoiler cutoff）由后端 `key_scenes/` 服务承担；
  本技能只编排上述 allowlist 工具，确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：EvidenceRef materialization
- 对选定的区间调 `get_evidence_span`（chapter_id + source_start +
  source_end）物化 leaf 证据；`content_hash` **可选且通常省略**——服务端
  会从原文切片确定性计算并返回（提供时校验匹配，不匹配 → fail closed）。
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

### 第 6 步：SceneCandidateArtifact（模型只产语义，哈希一律程序产出）
- **输出骨架（硬约束）**：模型输出必须是单个 JSON 对象（可 markdown fence
  包裹），顶层为 `scene_candidate_set`，其 `candidates` 每项只携带：
  - `evidence_key`（**必填**：从本 run 的 `get_evidence_span` 工具结果里
    选择；引用未物化的 key → fail closed）；
  - 可选语义字段：`coordinates`（cast/place/time/pov）、
    `salience_reasons`、`score_total`、`score_breakdown`、`diversity_key`。
  - `salience_reasons[].reason_code` **只能**取冻结枚举（其余值 → fail
    closed）：plot_turn / emotional_peak / character_salience /
    visual_expressiveness / arc_impact / quiet_emotional / dialogue_turn；
    `score` ∈ [0,1]。不确定就少给（宁缺毋滥）。
- **绝不输出任何哈希/血缘字段**（`source_hash` / `source_snapshot_hash` /
  `schema_hash` / `policy_hash` / `manifest_hash` / `candidate_key` /
  `chapter_number` / `spoiler_cutoff` / `review_state` 等）：这些由运行时
  投影确定性注入（模型写出也会被忽略）；`manifest_hash` 是对整个集合
  canonical payload 的重放哈希，只有程序能算。
- 信封其余部分（`type="scene_candidate"` / lineage / `tool_runs` /
  `status="candidate"` / `normalization` trail）由运行时组装，模型不参与。
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。
  `skill_run_id` / `artifact_id` / `revision_id` 由服务端分配，本技能**不写任何
  持久化行**。
- Agent 创建 candidate Artifact 后**暂停等待用户选择/审查**：`key_scene:approve`
  由 FastAPI review/freeze 端点执行（score/diversity/density/spoiler validator
  门 fail closed）；本技能/Agent **不能**直接授予或伪造批准。

## 候选纪律（D-31-01/D-31-04/D-31-05，必须遵守）
- 每个候选的证据（唯一 citation 权威，D-31-02）通过 `get_evidence_span`
  物化后按 `evidence_key` 选择引用：source snapshot + chapter/range +
  content hash + cutoff 全部由服务端/运行时校验与投影；越界 offset /
  错 hash / 跨 owner 证据 → fail closed。
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
