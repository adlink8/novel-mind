# Phase 12: Read-only Asset Audit and Eligibility - Context

**Gathered:** 2026-07-15
**Status:** Ready for planning
**Source:** Approved v0.8 requirements

<domain>
## Phase Boundary

在任何 v0.8 模型调用或上层候选写入前，对一部 owner-scoped 小说的现有 Phase 07 hierarchy 及 Phase 08/09/11 可选分析资产执行只读资格审计。输出机器可处理的资产清单、唯一资格状态、原因码和最小重建范围。本阶段不构建叙事记忆候选。

</domain>

<decisions>
## Implementation Decisions

### Eligibility contract
- 每项资产的结果只能是 `reusable_exact`、`rebuild_required`、`blocked` 或 `optional_unavailable`。
- hierarchy 是 required source；无 active build、版本/manifest/树/offset/hash/覆盖率不合法时，在任何 provider 调用前 fail closed。
- timeline、relationship、clue 是 optional source；缺失、不可用或 lineage 不匹配必须显式报告，不能被解释为“该小说没有对应事实”。
- 报告按 owner、novel、asset type 和 version/build identity 定位，并提供稳定 reason codes 与最小重建范围。

### Safety boundary
- 审计只读：不调用模型，不修复现有数据，不创建候选，不改变 active pointer、active revision 或其他生产状态。
- 测试必须证明审计前后 provider 调用数为零、数据库权威状态和生产指针不变。
- 不引入新生产依赖，不新增产品 UI，不改变现有时间线、人物关系、线索或 Reader Chat 消费路径。

### the agent's Discretion
- 报告 schema 的内部字段组织、reason code 命名以及纯函数/adapter 的文件拆分。
- Phase 12 首个切片可先交付纯契约与内存只读 adapter，再接 PostgreSQL 和运维入口。
- API 与 CLI 的具体组合由现有项目约定决定，但不得扩大成用户产品功能。

</decisions>

<canonical_refs>
## Canonical References

### Milestone contract
- `.planning/REQUIREMENTS.md` — V08-AUDIT-01..04 与 v0.8 scope boundaries。
- `.planning/ROADMAP.md` — Phase 12 goal、success criteria 与三项初始 plan。
- `.planning/research/SUMMARY.md` — candidate sidecar、先审计后调用模型、no-pointer-cutover 决策。
- `.planning/research/ARCHITECTURE.md` — 现有资产边界及分层数据流。
- `.planning/research/PITFALLS.md` — snapshot/lineage 混用与控制面越界风险。

### Existing authority
- `backend/app/models/chunk_build.py` — Phase 07 build、active pointer 与 hierarchy node PostgreSQL 权威。
- `backend/app/services/chunking/pg_store.py` — 现有 hierarchy 读取与持久化边界；Phase 12 只能复用读取侧。
- `backend/app/services/chunking/hierarchy.py` — hierarchy invariants。
- `backend/app/models/timeline.py` — Phase 08 version/pointer authority。
- `backend/app/models/relationship.py` — Phase 09 relationship observations/version lineage。
- `backend/app/models/clue.py` — Phase 11 clue lifecycle/version lineage。

</canonical_refs>

<specifics>
## Specific Ideas

- 报告必须区分 required hierarchy failure 与 optional domain source unavailable。
- “在 provider call 前阻断”应能由一个显式 guard/eligibility predicate 被后续 Phase 14 直接复用，而不是只写在 CLI 文案中。
- 先完成 12-01 的纯契约与 adapter seam，并通过无副作用单元测试，再实现 PostgreSQL 深审计。

</specifics>

<deferred>
## Deferred Ideas

- Chapter State、Story Arc/Volume、Global Story Model schema 与 PostgreSQL 表属于 Phase 13。
- 任何模型生成、预算、checkpoint 与 cache 属于 Phase 14。
- 分层检索、局部重建和单书资格比较分别属于 Phase 15–17。
- 生产 promotion、active pointer、Reader Chat 切换和新 UI 均不在 v0.8 范围内。

</deferred>

---

*Phase: 12-read-only-asset-audit-and-eligibility*
*Context gathered: 2026-07-15 from approved requirements*
