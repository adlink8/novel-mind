# build-visual-bible（Phase 30 版本化 Visual Bible 技能 / REQ-VIS-01 / REQ-AGENT-02/03/04）

给定一本小说（owner/novel/branch 血缘绑定），通过 **6 个只读域工具**收集证据，
产出**血缘绑定**的 VisualBibleArtifact——携带完整 `VisualBibleVersionContract`
（entities / claims / evidence refs / reference assets）。本技能只读，不写任何
Canon / visual bible 版本 / review 事件；唯一输出通道是后端确定性 finalizer
持久化的 VisualBibleArtifact（candidate-only）。`visual_bible:approve` 用户批准
后版本才成为 accepted visual authority（D-30-04）；生成**绝不**写入或提升到
Canon / active pointer（D-30-01）；会话永远不是事实源。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: build-visual-bible`，`version: 1.1.5`（契约更新：选择制证据 + 程序产出哈希 + 收敛预算 + 文本发现通道 + 正文证据约束 + 调用示例 + 文档一致性）。
- `allowed_tools`（编排 allowlist，6 个只读域工具）：`get_novel`、
  `get_chapter`、`get_evidence_span`、`get_character_state`、
  `get_world_rules`、`search_novel_text`。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。reference assets 经运行输入元数据提供
  （`read_permissions: [canon, visual_bible, reference_assets]`），**绝不**通过
  默认 bash / filesystem / file-edit / 任意执行工具读取。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, visual_bible:write, derivative:write]`。
- `approval_required_for: [visual_bible:approve]`——用户批准是成为 accepted
  visual authority 的显式、append-only 服务端状态迁移（D-30-04）。Agent 只能
  创建 candidate Artifact 并暂停等待审批；**不能**直接授予或伪造批准。除声明
  审批动作外的任何 ApprovalRequest / Publisher / promotion / active-pointer
  动作 → fail closed。
- `budget` per-run 上限：`max_calls=60`、`max_input_tokens=60000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`；示例见
  `examples/basic.json` 与 `tests/basic.json`（skill-local fixture）。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、
  ToolRuns、input hash、source snapshot、model/runtime lineage 与不可变
  Artifact revision。

## 执行流程（novel/cutoff/source → Tools → EvidenceRef → candidate → 确定性校验 → VisualBibleArtifact → 用户批准）

### 收敛预算（硬约束，先于一切编排）
- 本技能 per-run `max_calls=60`，**工具调用是稀缺预算**，按下限规划：
  `search_novel_text` **最多 4 次**（文本发现主通道：不同关键词各一次
  即可，命中相似就停手；`get_novel` 全书响应可能超限，**不要**用它逐章
  找证据）；`get_chapter` **最多 4 次**（只读最相关章节）；
  `get_evidence_span` **最多 8 次**（只为最终入选 claim 物化）；
  `get_character_state` / `get_world_rules` 合计不超过 4 次。
- **实体上限 5 个、claim 上限 8 条**（宁缺毋滥）；达到 1 个合格实体 +
  1 条带证据的 canon_fact claim 即可收尾输出。
- 任何时候只要已有 ≥1 个物化 span，就**立即停止搜索、输出最终 JSON**；
  反复换章节/关键词读取 = 烧光预算 = run 失败。

### 调用示例（照抄参数形状，不要发明字段）
1. 文本发现：`search_novel_text({"novel_id": <id>, "query": "慕师靖 外貌", "top_k": 5})`
   —— 命中行带 `chapter_id` / `chunk_id` / `content_snippet`。
2. 证据物化：`get_evidence_span({"novel_id": <id>, "chapter_id": <命中行的 chapter_id>, "chunk_id": <命中行的 chunk_id>})`
   —— **不要**自己算 source_start/source_end，不要传 chapter_number /
   evidence_key / excerpt 等任何其他字段。
3. 收尾输出（得到 ≥1 个 span 后立即输出，不再调用任何工具）：
   ```json
   {"visual_bible": {"entities": [{"entity_key": "char-x", "entity_type": "character", "description": "…", "authority": "canon_fact"}], "claims": [{"entity_key": "char-x", "authority": "canon_fact", "description": "…", "evidence_keys": ["<第 2 步返回的 evidence_key>"]}]}}
   ```

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；可选 `branch`、`cutoff`、
  `source_snapshot`（D-30 冻结快照血缘）与 `reference_assets`（read-only
  参考资产元数据）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / cutoff / evidence 由服务端强制（D-30-01/D-30-02）；显式 cutoff
  超限 → `beyond_cutoff`。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - 小说/章节正文与证据跨度 → `get_novel`、`get_chapter`、`get_evidence_span`。
  - 人物/世界规则信号（可选 traceable signals，D-30-02）→
    `get_character_state`、`get_world_rules`（candidate 信号不是事实）。
- Phase 30 确定性能力（evidence 物化、authority-label 校验、rights 裁决、
  review/versioning）由后端 `visual_bible/` 服务承担；本技能只编排上述
  allowlist 工具，确定性 validators 拥有 legality 权威。
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
  VisualBibleArtifact 的 `normalization` 字段（必须、可重放）。
- 任何 unsafe / ambiguous 修复、受保护字段合成（evidence_refs/owner/cutoff/
  authority/branch/fork/approval/approval_state）→ 稳定 `blocked`，零写入。

### 第 6 步：VisualBibleArtifact（模型只产语义，哈希一律程序产出）
- **输出骨架（硬约束）**：模型输出必须是单个 JSON 对象（可 markdown fence
  包裹），顶层为 `visual_bible`，只携带：
  - `entities`（至少 1 个）每项只携带：`entity_key`（必填，稳定语义键）、
    `entity_type`（character/place/item/faction/style）、`description`、
    `authority`（四标签之一）；
  - `claims`（至少 1 个）每项只携带：`entity_key`（必填，引用上面声明的
    实体）、`authority`、`description`，以及：
    - `canon_fact` claim 必须带 `evidence_keys`（**必填**：从本 run 的
      `get_evidence_span` 工具结果里选择；引用未物化的 key → fail
      closed）；
    - interpretation 类 claim（probable_inference /
      literary_interpretation / user_interpretation）必须带 `author` 与
      `rationale`；
  - 可选：`style_profile`（对象）、`constraints`（对象数组）。
- **chapter_number 必须 ≥ 1**：chapter 0 是前言/声明页，其 span 不能作为
  证据（投影 fail closed）；搜索命中前言页时换关键词物化正文章节的 span。
- **至少 1 条 canon_fact claim**（带从物化 span 选择的 `evidence_keys`）：
  纯 interpretation 输出没有任何 leaf 证据，会被信封层拒绝（fail
  closed）；拿不准的写成 interpretation，但外观/衣着/场景等文本可直接
  佐证的特征必须写成 canon_fact。
- **绝不输出任何哈希/血缘字段**（`stable_id` / `claim_key` / `claim_hash` /
  `schema_hash` / `policy_hash` / `manifest_hash` / `source_snapshot_id` /
  `source_snapshot_hash` / `cutoff_chapter` / `disclosure_cutoff` /
  `review_state` / `content_hash` 等）：这些由运行时投影确定性注入（模型
  写出也会被忽略）；`claim_hash` / `manifest_hash` 是对 canonical payload
  的重放哈希，只有程序能算。
- 信封其余部分（`type="visual_bible"` / lineage / `tool_runs` /
  `status="candidate"` / `normalization` trail）由运行时组装，模型不参与。
- 调用后端 finalize 入口（stop_reason="stop"），由后端**确定性 finalizer**
  写入 candidate 产物 + 首个不可变修订并分配 artifact_id / revision_id。
  `skill_run_id` / `artifact_id` / `revision_id` 由服务端分配，本技能**不写任何
  持久化行**。
- Agent 创建 candidate Artifact 后**暂停等待用户批准**：`visual_bible:approve`
  由 FastAPI review 端点执行（evidence/rights/authority-label validator 门
  fail closed）；本技能/Agent **不能**直接授予或伪造批准。

## 候选纪律（D-30-01/D-30-04，必须遵守）
- 每个 visual claim 带 `authority` 四标签之一：`canon_fact` /
  `probable_inference` / `literary_interpretation` / `user_interpretation`
  （D-30-02）。`canon_fact` 必须带可物化的 leaf evidence（source snapshot +
  chapter/range + hash + cutoff）；interpretation 类 claim
  （probable_inference / literary_interpretation / user_interpretation）必须带
  `author` 与 `rationale`。任何无证据的 canon_fact 或越界 offset / 错 hash /
  跨 owner entity → fail closed。
- 版本是**不可变、可版本化**的 candidate：`review_state` 恒为
  `"candidate"`；只有显式、append-only 的 `approve` 动作（服务端 review 事件，
  幂等 event_key）才把状态迁移到 `approved`。绝不因生成而写入或提升到 Canon /
  active pointer（D-30-01）。
- reference assets（generated/reference）以 `rights_status` 记录；未 `cleared`
  的资产无法通过 approval gate，绝不静默成为 canon（D-30-04）。
- 证据/rights 校验 + 用户批准后版本才成为 accepted visual authority；无批准
  无 promotion。

## 边界（必须遵守，fail closed）
- 只读：不写 Canon、不写 visual bible 版本、不写 review 事件、不产生任何域
  事实。
- 不调用 allowlist 外的任何工具。
- 引证只能来自冻结 manifest；任何未知 ref 让 run 以 `failed_validation`
  失败，且什么都不写。
- **无 ApprovalRequest 直接授予、无 Publisher 越权**：本技能只创建 candidate
  Artifact 并暂停等待审批；`visual_bible:approve` 的授权只属于 FastAPI 确定性
  review 端点；attempted approval 伪造（envelope status 非 candidate /
  review_state 非 candidate / 受保护字段）→ fail closed。
- 取消（`cancel_requested` 或 stop reason 为 aborted/cancel）→ run 以
  `cancelled` 结束，0 artifact 行 + 0 revision 行（cancel-without-write）。
- wrong owner / wrong skill_version / wrong cutoff / stale input_hash /
  schema drift / unsafe normalization / 未知工具 / approval bypass /
  未清除 rights 的批准尝试 → 稳定 blocked/cancelled，无官方 Artifact 或域写入。
