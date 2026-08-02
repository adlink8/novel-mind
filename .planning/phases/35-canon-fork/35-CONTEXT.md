# Phase 35 Context — Canon Fork

## Decisions

- **D-35-01** 三空间固定为 `Original Canon`、`User Interpretation`、`Fanfiction Canon`；每个空间必须有独立 `authority`、`namespace`、`version` 与 `citation` 规则。Canonical refs: `REQ-FORK-01`, `REQ-CRE-01`, Issue #29（用户指定的范围权威；本次公开 URL 读取返回 404，未能独立复核）。
- **D-35-02** 原作索引只读；创作内容不得进入原作检索索引、评测语料或 facet 生产链，并必须用负向污染测试证明。Canonical refs: `REQ-FORK-02`, `REQ-CRE-02`, `ROADMAP.md#Phase 35`。
- **D-35-03** Canon Fork 必须冻结 `owner`、`novel`、原作 `version`、`cutoff`、source snapshot/hash 和 citation lineage；默认不产生或切换生产 active pointer。Canonical refs: `STATE.md#Baseline Decisions`, `ROADMAP.md#Execution Rules`, `REQ-CRE-05`。
- **D-35-04** Phase 22 仍为 BLOCKED、Nightly 0/3；Phase 35–39 的规划或实现不得解除该门。Canonical refs: `.planning/STATE.md#Current Position`。

## Agent Consumer Contract

- Skill / mode: create-canon-fork.
- Inputs: Original snapshot read-only + interpretation + fork intent.
- Official output: CanonForkProposal; CanonDeltaArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: create_canon_fork Web approval.
- Deterministic authority: namespace/snapshot/cutoff/immutable-Original validator + materializer.
- Forbidden: Original Canon mutation or active-pointer move; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- 在现有 SQLAlchemy/PostgreSQL 与 Pydantic contract 模式内选择表拆分、namespace key 形式和 API 路径，但不得削弱 D-35-01 至 D-35-03。
- 可复用 `NarrativeMemory`、Reader Chat 和 Clue/Timeline 的 manifest、hash、override 与 owner-scope 结构；新增字段仍须由确定性代码校验。

## Deferred Ideas (OUT OF SCOPE)

- 生产 NM promotion、active-pointer cutover、生产 A/B；统一留在 `999.x`，需显式授权。
- 让 Chat 或 LLM 直接写入 Canon、facet、原作索引或 accepted facts。
- 本阶段不实现编辑器、生成、视觉资产或导出功能；这些由 Phase 36–39 负责。

## Research Scope

Issue #29 的公开页面在本次读取中返回 404；因此以下研究只把用户明确约束、`ROADMAP.md`、`REQUIREMENTS.md`、`STATE.md` 和现行代码当作可核实范围，不将旧 `docs/技术架构.md` 中的 fanfiction 草案升级为权威契约。
