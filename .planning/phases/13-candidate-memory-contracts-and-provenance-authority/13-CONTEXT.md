---
phase: 13-candidate-memory-contracts-and-provenance-authority
source: approved-v0.8-requirements
requirements: [V08-MEM-01, V08-MEM-02, V08-MEM-03, V08-MEM-04, V08-MEM-05]
---

# Phase 13 Context

## Outcome

建立与 timeline、relationship、clue 和 Reader Chat 隔离的 PostgreSQL candidate authority。系统能够持久化并验证 Chapter State、连续 Story Arc/Volume 与 Global Story Model，但本阶段不生成模型内容、不运行构建 worker，也不创建或移动任何生产 active pointer。

## Locked Decisions

- 只新增 additive、candidate-only 的叙事记忆权威；v0.8 没有 production promotion 或 active pointer 路径。
- PostgreSQL 是 version、node、edge、claim、source link、manifest 与验证报告的唯一事实源。
- 每个 candidate version 冻结 owner、novel、source snapshot、Phase 07 hierarchy build/checksum，以及 prompt、schema、model、decoding、config/policy lineage。
- Chapter State、Story Arc/Volume、Global Story Model 使用封闭层级与 strict typed payload；未知字段、自由文本事实、summary-only claim、跨包引用一律 fail closed。
- 每条 authoritative claim 必须通过显式 link 落到同一 source snapshot 的 leaf evidence，并由服务端按 Chapter.content offsets 重切、重算 hash。
- Story Arc/Volume 的章节范围必须连续且合法；Global 只能连接已验证的 arc；整个 node/edge 图必须无环且 scope 一致。
- manifest 由排序后的数据库 version/node/edge/claim/source-link rows 确定性重算，不信任调用方自报 checksum。
- candidate 创建、校验和失败报告不得调用 provider，不得修改既有 chunk/timeline/relationship/clue/chat 数据或任何生产 pointer/revision/journal。

## Scope

### In scope

- Alembic additive migration、ORM authority、数据库约束与 append-only/immutability enforcement。
- Strict Pydantic contracts and canonical serialization for the three memory levels and typed claims/state deltas.
- Candidate persistence and deterministic provenance/manifest validator.
- PostgreSQL integration and adversarial tests for scope, DAG, range, source re-slice, manifest and no-pointer invariants.

### Out of scope

- Provider calls、预算、exact cache、checkpoint、取消恢复与 bottom-up worker（Phase 14）。
- 分层 query routing、下钻、citation rendering 与 Reader Chat cutover（Phase 15）。
- dirty closure/carry-forward（Phase 16）与单书质量资格（Phase 17）。
- 新产品 UI、production promotion、active pointer、GraphRAG/RAPTOR/Neo4j/LangChain 或新生产依赖。

## Verification Standard

- 迁移从当前真实 single Alembic head 升级并通过 PostgreSQL 约束检查。
- strict contracts 拒绝 extra fields、未知 enum、summary-only authority 和非法 source refs。
- fresh PostgreSQL validator 能从任一 claim 下钻到真实 leaf evidence，并重算 offsets/hash/snapshot/scope 与 canonical manifest。
- 对抗测试覆盖跨 owner/novel/version/build、断链、环、范围重叠/空洞、宽泛引用、tampered content/hash/manifest。
- before/after observer 证明 Phase 13 不创建生产 pointer，也不改变现有生产 authority。

---

*Context derived non-interactively from the user-approved v0.8 A.A requirements and continuous GSD execution authorization.*
