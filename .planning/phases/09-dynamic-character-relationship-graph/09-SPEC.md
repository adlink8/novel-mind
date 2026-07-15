---
phase: 09-dynamic-character-relationship-graph
status: locked-for-planning
created: 2026-07-13
requirements: [REQ-REL-01, REQ-REL-02, REQ-REL-03, REQ-REL-04, REQ-REL-05, REQ-REL-06]
domain: fiction-only
---

# Phase 09: Dynamic Character Relationship Graph — Specification

## Goal

用户可在全局 `/analysis` 工作台查看与当前小说分析版本一致、随叙事位置演化、默认按阅读进度防剧透且能回到原文证据的人物关系图。

## Current Baseline

- Phase 04 已持久化 relation candidate、LLM judgment、evidence refs 与 deterministic gate；`KnowledgeRelationJudgment(status="accepted", gate_status="accepted")` 是现有接受关系入口，PostgreSQL 是事实源。
- `CharacterRelation` 仍是可变单行快照，只有 `chapter_first_seen`，不能表示关系变化、版本或历史有效区间；不得把它升级为 Phase 09 事实源。
- `/api/characters/{novel_id}` 与 `/relations` 仍返回空数组；`/extract` 返回 501。
- Phase 08 已交付 `AnalysisVersion`、active pointer、running candidate、证据 offsets、人工 override 模式、阅读进度 cutoff 与持久化 `timeline_full_book` 开关。
- `/analysis` 当前仅有 ECharts 时间线。Phase 09 在该页增加关系工作区，不改变时间线图表库或数据模型。

## Falsifiable Requirements

### REQ-REL-01 — Evidence-gated fiction facts only

关系图只消费 fiction 小说中通过 schema/evidence/threshold/conflict 门的 accepted relationship observations。每个 observation 必须绑定 owner、novel、analysis version、source accepted judgment、candidate/judgment lineage 和至少一个有效 evidence ref。

Acceptance:

- 给定 candidate、仅有 LLM structured output、向量相似度、聊天消息或未接受 judgment，图 API 返回的节点和边数量均不增加。
- 给定跨 owner、跨 novel、history profile、缺 evidence、伪造 evidence ID 或未接受 judgment，写入被拒绝且 reason code 可断言。
- 每条 API edge 可从 observation 追到 Phase 04 accepted judgment 和冻结证据位置。

### REQ-REL-02 — Append-only temporal evolution

人物关系以 append-only observation 表示；每条 observation 绑定 narrative valid interval 和一个 analysis version。查询某叙事位置时通过确定性 fold 计算可见状态，不更新或删除过去 observation 来表示变化。

Acceptance:

- 同一人物对从 `ally` 变为 `enemy` 时至少新增一条 observation；旧 observation 的数据库行、checksum 和 interval 保持不变。
- 在转变前后查询得到不同关系状态；回查旧位置得到与新增 observation 前字节等价的结果。
- 重分析创建不同 version 下的 observation 集，active/running candidate/历史 version 不混合。

### REQ-REL-03 — Owner/version/spoiler-scoped API

图谱 API 必须同时约束 owner、novel、analysis version 和 spoiler cutoff。默认 cutoff 来自持久化阅读进度；没有阅读进度时沿用 Phase 08 第一章规则。全书请求只在同 owner/novel 已持久化 `timeline_full_book=true` 时生效。

Acceptance:

- owner A 对 owner B 的 novel/version 请求返回 404，且响应不暴露存在性、计数、标签或人物名。
- 阅读到第 N 章时，响应中不存在 N+1 章才出现的节点、边、关系类型、筛选选项、统计或证据摘要。
- `full_book=true` 但未持久化 Phase 08 开关时仍按 cutoff；开关持久化后才返回全书关系。
- API 不接受由客户端声明 owner，也不接受跨 novel/version 的人物或 evidence IDs。

### REQ-REL-04 — Analysis relationship workspace

`/analysis` 增加可缩放关系工作区；用户可按人物、关系类型和叙事位置筛选，选择节点/边查看证据侧栏，并与现有主时间线叙事位置联动。不得把 plot summary、chapter summary、theme、pace 或其他阶段中间摘要暴露为菜单。

Acceptance:

- 桌面与 390px Playwright 可完成：选小说 → 切换关系工作区 → 筛选人物/类型/叙事位置 → 缩放/平移 → 选边 → 打开证据 → 跳到章节。
- 关系工作区使用 Cytoscape.js；现有 `timeline-chart.tsx` 继续使用 ECharts，未引入第二套时间线。
- 键盘可通过与画布同源的节点/边列表选择关系；画布与辅助列表展示相同 visible set。
- 主时间线选择或叙事位置控件改变时，关系 API cutoff 和图状态同步变化。

### REQ-REL-05 — Protected manual corrections

人物合并、关系类型和 narrative valid interval 的人工修正以 append-only protective override 保存。重分析只产生新机器 observation/version；不得静默覆盖、删除或错误套用 override。

Acceptance:

- 新版本可唯一匹配 stable evidence signature 时自动 relink override，并保留 author、时间、supersession 与 provenance。
- 无法唯一匹配时状态变为 `needs_relink`，旧 override 保留且不会自动应用到错误关系。
- 同一字段的新修正通过 `supersedes_id` 追加；旧 override 行保持不变。
- API 对 machine/manual provenance 明确区分，撤销修正通过新增 superseding record 完成。

### REQ-REL-06 — Replayable projection and release quality

PostgreSQL observation 是唯一事实源；Neo4j 只能读取 accepted observations 构建可删除重放的派生投影。构建、查询、缓存、Cytoscape 交互和降级必须有版本化评测及发布门禁。

Acceptance:

- 清空 Neo4j/投影 adapter 后，从同一 PostgreSQL accepted observation manifest 重放得到相同 node/edge checksum；投影失败不改变 PostgreSQL acceptance 或 API truth。
- 单元、PostgreSQL 集成、OpenAPI/前端契约、owner/spoiler/adversarial、Playwright 和性能命令全部通过后才生成 passed release verdict。
- 正常图（≤200 nodes/≤600 edges）启用完整交互；大图（≤500/≤1500）进入无动画、简化标签/边样式模式；超过硬上限返回 `filters_required`，不发送部分事实冒充完整图。
- release evidence 绑定 fixture、source judgment set、analysis version、policy、schema、frontend package lock 和命令输出 digest。

## Scope

### In Scope

- fiction 人物关系 observation candidate/judgment/gate/accepted persistence
- narrative valid interval、version isolation、active/running candidate 查询
- 人物 identity merge 与关系字段 protective overrides
- owner/version/spoiler scoped graph API、visible-set-first filters/counts/evidence
- optional replayable Neo4j projection boundary
- `/analysis` Cytoscape.js 关系工作区、ECharts 时间线联动、证据侧栏
- migration、unit/integration/contract/adversarial/Playwright/performance/release gate

### Out of Scope

- history corpus、history ontology、历史人物图；该产品范围已移除
- Phase 10 选区 AI、会话、消息、聊天检索或把聊天内容写为关系事实
- Phase 11 clue/foreshadow candidate、五状态生命周期或线索 UI
- 社区发现、中心性排名、路径推荐、关系预测和图生成文本
- 暴露 Phase 08 的剧情摘要、主题、节奏、章节总结等中间产物

## Cross-Phase Data Contracts

- Phase 10 只可读取 Phase 09 已通过 owner/version/spoiler 过滤的关系图 query service；本阶段不创建 conversation/session/message 表或 API。
- Phase 11 可把 accepted observation ID 与 evidence refs 作为未来 clue 关联目标；本阶段不创建 clue candidate/status/UI。

## Success Criteria

- REQ-REL-01..06 每项均有自动化、可失败的证明命令。
- 所有事实写入都能证明来自 accepted judgment + valid evidence + deterministic gates。
- 任意叙事位置和分析版本的图状态可从 append-only rows 重算。
- 人工修正、spoiler、owner 和 version 边界在 API 与浏览器链路均 fail closed。
- Phase 10/11 只获得明确的只读依赖契约，没有提前实现产品功能。

