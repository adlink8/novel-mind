# compile-scene-spec（Phase 32 版本化 Scene Spec / Prompt 编译技能 / REQ-VIS-03 / REQ-AGENT-02/03/04）

给定一本小说（owner/novel/branch 血缘绑定）的 **frozen key-scene set + 已批准
Visual Bible revision + source snapshot 引用**，通过 **2 个只读域工具**
（`get_visual_bible` / `get_evidence_span`）收集证据，产出**血缘绑定**的
`SceneSpecArtifact` 与 `PromptArtifact`——确定性编译器（D-32-01..D-32-04）按引用
消费 validated SceneCandidate 与 Visual Bible 版本，无 unsupported Canon；本技能
**只读**，不写任何 Canon / Visual Bible / key-scene 集 / scene_spec 域表；唯一输出
通道是后端确定性 finalizer 持久化的候选 Artifact。`scene_spec:approve` 用户审查只
授权 Phase 33 消费（D-32-04），生成**绝不**写入或提升到 Canon / Visual Bible /
active reader state（D-32-01/D-32-02）；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: compile-scene-spec`，`version: 1.0.0`（Phase 32 绑定版本）。
- `allowed_tools`（编排 allowlist，2 个只读域工具）：`get_visual_bible`、
  `get_evidence_span`。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- validated SceneCandidate / provider capabilities 由服务端确定性编译器按引用
  消费（`candidate_set_id` / `candidate_key` / `visual_bible_version_id` /
  `source_snapshot_id`），**不是** Agent 工具——本技能绝不自行物化事实。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, visual_bible:write, key_scene:write, scene_spec:write]`。
- `approval_required_for: [scene_spec:approve]`——用户审查/批准只授权 Phase 33
  消费（`FrozenSceneSpecView` / `FrozenPromptRevisionView` 的 `approved_only`
  门），是显式、append-only 的服务端状态迁移（D-32-04）。Agent 只能创建
  candidate Artifact 并暂停等待审批；**不能**直接授予或伪造批准。除声明审批动作
  外的任何 ApprovalRequest / Publisher / promotion / active-pointer / 写回
  Canon 或 Visual Bible 的动作 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  ToolRuns、input hash、source snapshot、model/runtime lineage 与不可变
  Artifact revision。

## 执行流程（novel/spec refs → Tools → EvidenceRef → 确定性编译 → 校验 → SceneSpecArtifact / PromptArtifact → 用户审查/批准）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`spec_key`（D-32-03 幂等重放
  溯源）；`candidate_set_id` / `candidate_key`（frozen key-scene set + approved
  candidate，D-32-02 唯一消费目标）；`visual_bible_version_id`（approved Visual
  Bible revision，服务端重验）；`source_snapshot_id`；可选 `branch` /
  `revision_number` / `policy_hash` / `config_hash`。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff / evidence / Visual Bible revision 由服务端强制
  （D-32-01/D-32-02）；candidate 章节超过剧透截止点或 Visual Bible revision
  与 set 冻结血缘不符 → fail closed，不产出 spec。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - 已批准 Visual Bible 修订（服务端重验 owner/approved/hash）→
    `get_visual_bible`。
  - 章节原文 leaf 证据跨度（evidence_refs 唯一物化通道）→ `get_evidence_span`。
- Phase 32 确定性能力（evidence-to-spec compile + provider-neutral prompt
  派生）由后端 `scene_spec/` 与 `prompt_compiler/` 服务承担；本技能只编排上述
  allowlist 工具，确定性 validators 拥有 legality 权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：EvidenceRef materialization
- 从工具响应中挑选**可直接引用**的 leaf/raw 证据，每个证据物化为一个
  evidence_ref（D-07/D-08 / D-32-02）。
- 只物化工具实际返回的内容；**绝不编造**引用（finalize 用冻结 manifest
  白名单校验）。
- 摘要、prompt 段、不确定项、解释文本、Visual Bible 派生描述**都不是** leaf
  证据，不得进入 evidence_refs（D-08 / D-32-02）。

### 第 4 步：Frozen Manifest
- 把所有物化 evidence_ref 冻结成一个不可变 manifest
  （`{"evidence_refs": [...], "manifest_checksum": <sha256>, ...}`）。
- 冻结后不得再增删证据；finalize 落库到 run 上作为引证白名单。

### 第 5 步：确定性编译（服务端权威，D-32-01..D-32-04）
- 服务端确定性编译器按引用读取 frozen key-scene set 的 approved candidate
  与已批准 Visual Bible revision，校验 candidate/snapshot/cutoff/spoiler 血缘
  与 Visual Bible manifest hash 重放后，编译 `SceneSpecContract`：
  - 每个 subject/setting/composition 细节要么链接到 Visual Bible entity
    （stable ID + revision hash），要么链接到 leaf evidence；缺失引用 → 以
    reason-coded `uncertainties`（missing_evidence / future_spoiler /
    conflicting_claim / ambiguous_reference）呈现，**绝不**渲染进正向 prompt 段。
  - 负向约束从 Visual Bible constraints 列表确定性渲染；未支持 scope / 无文本
    约束 → fail closed（无 unsupported constraint 伪装成 canon）。
  - style profile 按 sorted-key 确定性渲染；continuity 携带场景实体 stable IDs。
- 从 `SceneSpecContract` 确定性派生 provider-neutral `PromptRevisionContract`
  （canonical sections + negative constraints + uncertainties + 可重放
  `input_hash`/`prompt_hash`；prompt 字符串**不是**权威，D-32-01）。不调用任何
  provider（Phase 32 不触发生成，D-32-04）。
- 本技能不自己改任何 spec/prompt 字段；全部派生在服务端确定性完成。

### 第 6 步：共享 normalizer + 严格 validator（26-06 / D-16）
- 模型结构化输出先经**共享 26-06 normalizer**：只允许声明式 alias、enum
  canonicalization、无歧义 container-shape 修复；本技能**不引入任何本地修复
  路径**。
- 修复后的 payload 经**严格 post-repair validator**（schema/lineage hash
  重放 + 受保护字段门 + leaf-evidence 资格门 + `SceneSpecContract` /
  `PromptRevisionContract` 域校验——Canon/Visual Bible 一致性 + 未支持细节拒绝）
  → 通过才允许 finalize。
- `normalization_actions`、`raw_hash`、`repaired_hash`、`warnings` 保留在官方
  SceneSpecArtifact / PromptArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval/approval_state）→ 稳定 `blocked`，零写入。

### 第 7 步：SceneSpecArtifact / PromptArtifact
- 组装场景规格信封（字段镜像后端 `SceneSpecArtifact` / `PromptArtifact`，见
  `output.schema.json` / D-32-01..D-32-04）：
  - SceneSpecArtifact：`type="scene_spec"`，`schema_version="scene-spec.v1"`；
    携带完整 `SceneSpecContract` 负载（spec_key / scene_candidate_hash /
    visual_bible_revision_hash / source snapshot / cutoff / compiler lineage /
    details / negative_constraints / uncertainties / `review_state="candidate"`）。
  - PromptArtifact：`type="prompt"`，`schema_version="prompt-revision.v1"`；
    携带完整 `PromptRevisionContract` 负载 + 其派生自的 `SceneSpecContract`
    负载（prompt 派生血缘，D-32-03）。
  - 两者共有：`owner_id` / `novel_id` / `branch` / `producing_skill=
    "compile-scene-spec"` / `producing_skill_version="1.0.0"` /
    `skill_version_id` / `model_lineage` / `source_versions` / `input_hash`
    （来自 run）/ `evidence_refs`（必须 ⊆ 冻结 manifest 白名单）/ `tool_runs`
    （ToolRun 血缘）/ `status="candidate"` / `parent_revision=null` /
    `normalization`（26-06 trail）。
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。
  `skill_run_id` / `artifact_id` / `revision_id` 由服务端分配，本技能**不写任何
  持久化行**。
- Agent 创建 candidate Artifact 后**暂停等待用户审查/批准**：`scene_spec:approve`
  由 FastAPI review 端点执行（stale/hash/Canon-Visual Bible 一致性门 fail
  closed），批准**只**把 spec/prompt 标记为 Phase 33 消费输入；本技能/Agent
  **不能**直接授予或伪造批准，也**不能**借此写回 Canon 或 Visual Bible。

## 候选纪律（D-32-01..D-32-04，必须遵守）
- spec/prompt 是**不可变、可版本化**的 candidate：`review_state` 恒为
  `"candidate"`；只有显式、append-only 的 `scene_spec:approve`（服务端 review
  事件，幂等 event/decision key）才把状态迁移到 `approved`，且该批准**只授权
  Phase 33 消费**（`approved_only` 门），绝不写回 Canon / Visual Bible /
  key-scene 集（D-32-04）。绝不因生成而写入或提升到 Canon / active reader
  state（D-32-01）。
- 每个正向/负向 clause 都保留 provenance：evidence 引用唯一物化通道是
  leaf evidence（source snapshot + chapter/range + content hash + cutoff）；
  Visual Bible 引用携带 stable ID + revision hash；越界 offset / 错 hash /
  跨 owner 证据 → fail closed（D-32-02）。
- 未支持细节（unsupported canon、未来剧透、冲突主张、缺 Visual Bible 引用）
  只能以 reason-coded uncertainties 呈现，**绝不**进入正向 prompt 段或伪装成
  canon（D-32-02）；prompt 字符串永远不是权威（D-32-01）。

## 边界（必须遵守，fail closed）
- 只读：不写 Canon、不写 Visual Bible、不写 key-scene 集、不写 scene_spec/
  prompt 域表、不产生任何域事实。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest 直接授予、无 Publisher 越权、无 Canon/Visual Bible
  写回**：本技能只创建 candidate Artifact 并暂停等待审批；`scene_spec:approve`
  的授权只属于 FastAPI 确定性 review 端点；attempted approval 伪造
  （envelope status 非 candidate / scene_spec.review_state 非 candidate /
  受保护字段）→ fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  stale visual_bible_revision_hash / schema drift / unsafe normalization /
  未知工具 / approval bypass / 未支持细节被当作 Canon → 稳定 blocked/cancelled，
  无官方 Artifact 或域写入。
