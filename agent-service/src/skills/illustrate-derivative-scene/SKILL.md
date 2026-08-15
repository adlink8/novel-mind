# illustrate-derivative-scene（Phase 38 版本化 branch-aware derivative visual 技能 / REQ-FORK-04 / REQ-AGENT-02/03/04/07）

给定一本小说（owner/novel/branch 血缘绑定，必选 fork 用于 derivative mode）的
**只读 branch 上下文 + 已批准 derivative Visual Bible fork version + frozen
canonical derivative Scene Spec + 已存储 candidate asset 血缘**（通过 7 个只读
域工具消费），通过 **1 个 action 工具**（`publish_derivative_visual`）消费 Phase
38 确定性 derivative visual 域能力（D-38-01..D-38-04：fork Visual Bible →
canonical Scene Spec compiler → candidate asset storage + cross-chapter
consistency → review seam + PublishedDerivativeVisualAsset），产出候选
`BranchVisualBibleArtifact`（携带 `BranchIllustrationRevision`）。
本技能**只产出候选**：action 工具只为同一 candidate 创建 pending Web
ApprovalRequest（action=`publish_derivative_visual`，payload_hash 绑定 candidate
冻结血缘——asset_id/content_hash/scene_spec_hash/divergence_manifest_hash/
consistency_verdict/source_snapshot_hash/fork_id；D-11/D-15）。**确定性发布属于
后端**——只有确定性 review seam
（`app.services.derivative_visual.review.review_candidate_asset` →
`apply_derivative_asset_review`）在独立 approval 被用户批准后原子校验 approval
action + 相同 hash 绑定 + fork scope + 合法 review 转移，才把 candidate 物化为
approved published asset（D-38-03/D-38-04）。Agent 不直接写任何 Original Canon /
Visual Bible / Scene Spec / candidate / domain 表 / ApprovalRequest 决策 /
published 状态；**Original Visual Bible 不可变、绝不覆盖**（REQ-FORK-04）。

## 版本与契约（镜像 skill.yaml / D-09）

- `name: illustrate-derivative-scene`，`version: 1.0.0`（Phase 38 绑定版本）。
- `allowed_tools`（编排 allowlist，8 个域工具）：
  - 7 个只读：`get_novel`、`get_chapter`、`search_novel_text`、
    `get_timeline`、`get_relationships`、`get_clues`、`get_narrative_memory`；
  - 1 个 action：`publish_derivative_visual`（为已存储 candidate 创建
    **独立** pending Web ApprovalRequest，绑定候选冻结血缘）。
- **不得调用** allowlist 外任何工具；发现即 fail closed（Pi 只能编排声明的
  Domain Tool allowlist）。
- 最终 scope / source snapshot / fork 血缘由服务端确定性派生与强制
  （`visual_fork_version_id` / `scene_spec_hash` / `candidate_asset_id` /
  `source_snapshot_hash` / `evidence_refs` 只是意图引用），**不是** Agent 工具——
  本技能绝不自行物化事实或分支。
- `write_permissions: []`（Agent 零域写入）；`forbidden_spaces:
  [canon:original, user_interpretation, derivative:autosave,
  derivative:direct_write, derivative_visual:write, approval_request,
  review_service, published_assets]`。
- `approval_required_for: [publish_derivative_visual]`——action 创建**独立**
  pending Web ApprovalRequest（D-11）；任何直接发布 / 域表写入 / promotion /
  写回 Original Visual Bible / 伪造批准 → fail closed。
- `budget` per-run 上限：`max_calls=40`、`max_input_tokens=40000`、
  `max_output_tokens=12000`、`max_cost_usd=4.00`；超限 fail closed。
- 输入/输出 schema 见 `input.schema.json` / `output.schema.json`。
- 每次 run 经 `skill_version_id` 绑定 SkillVersion；产物绑定 SkillRun、ToolRuns、
  input hash、source snapshot、model/runtime lineage 与不可变 Artifact revision。

## 执行流程（branch context → Tools → BranchVisualBibleArtifact → publish_derivative_visual approval → review seam 确定性发布）

### 第 1 步：范围归一化
- 从运行输入读取 `novel_id`（与路径 novel 一致）；`branch` / `fork`（derivative
  mode 必须提供）；`visual_fork_version_id`（已批准 derivative Visual Bible
  fork version）/ `scene_spec_hash`（frozen canonical derivative Scene Spec）/
  `candidate_asset_id`（已存储 candidate asset）/ `source_snapshot_id` /
  `source_snapshot_hash` / `evidence_refs`；`requested_action`
  （publish_derivative_visual）。
- 把输入原样交给后端计算 `input_hash`（规范化 JSON 的 SHA-256）；本技能
  **不修改** input，保证重放可追溯。
- 最终 scope / source snapshot / fork 血缘由服务端强制（D-38-03）；stale
  source snapshot / 错误 owner/branch/fork / 未授权发布 → fail closed，
  不产出候选产物。

### 第 2 步：工具编排
- 按范围形态选择 allowlist 工具，例如：
  - `get_novel` / `get_chapter` / `search_novel_text`：只读 branch 上下文与
    leaf 证据，物化 citation lineage。
  - `get_timeline` / `get_relationships` / `get_clues` /
    `get_narrative_memory`：只读解释/创作参考（candidate-only，ADR-0002）。
  - 请求发布：调用 `publish_derivative_visual`——服务端 action 工具只为同一
    candidate 创建 **pending** Web ApprovalRequest（payload_hash 绑定候选
    冻结血缘；blocked candidate / wrong owner / branch / fork → fail closed）。
- Phase 38 确定性能力（fork / Scene Spec compiler / candidate store +
  consistency / review seam / published query）由后端 `derivative_visual/`
  服务承担；本技能只编排上述 allowlist 工具，确定性 validators 拥有 legality
  权威。
- 每个工具调用都走 25.2-02 门面，携带运行内部令牌；所有失败映射为冻结
  agent-tool 错误码。
- 工具调用受技能 `budget` 的 per-run call/token 上限约束，超限 fail closed。
- 取消（`cancel_requested`）或工具超时/越界 → 不物化证据，走稳定取消/失败路径。

### 第 3 步：候选 BranchVisualBibleArtifact（服务端 gate + finalize）
- 模型结构化输出（BranchVisualBibleArtifact 信封：revision =
  BranchIllustrationRevision）走 26-06 normalizer；服务端 integrity gate 拒绝
  schema drift（status 非 candidate、review_state 非 candidate、evidence 越界、
  wrong branch、authority_space 非 derivative / 缺 fork、source hash 漂移）。
- agent_end stop → finalize 写入 candidate BranchVisualBibleArtifact + 不可变
  revision；取消 → cancelled，0 写入。

### 第 4 步：publish_derivative_visual approval（后端权威，D-38-03）
- 用户 Web 批准 `publish_derivative_visual` → 服务端确认（pending → approved）。
- 确定性 review seam 重放 approval + payload hash + fork scope + 合法 review
  转移；失败/漂移 → fail closed，不发布。

### 第 5 步：确定性 review seam 物化
- 仅当 approval 已批准后，确定性 review seam
  （`review_candidate_asset`）原子校验 approval action + 相同 hash 绑定 +
  fork scope，并走 38-04 状态机把 candidate 的 review_state 从当前可批准状态
  （candidate / needs_review）推进为 approved——published asset 立即对
  owner/project/fork 可见（38-03 `published_assets`）。`blocked` candidate
  （identity drift / 未声明 divergence）转移集为空 → 绝不可能被批准。
- Original Visual Bible 绝不变；任何 forged/expired/cancelled/rejected
  approval、stale revision、wrong branch/fork、schema drift → fail closed，
  无权威写入。

## 边界（fail closed）

- Original Visual Bible 不可变：无任何写路径。
- Agent Service 不能直接写 Original Canon / Visual Bible / Scene Spec /
  candidate/domain 表 / published state；`publish_derivative_visual` 只创建
  候选 approval——确定性 review seam 拥有 approved publication。
- 无 shell / filesystem / ambient package / direct database 路径。
- 唯一官方输出：`BranchVisualBibleArtifact`（携带 `BranchIllustrationRevision`，
  candidate-only）。
