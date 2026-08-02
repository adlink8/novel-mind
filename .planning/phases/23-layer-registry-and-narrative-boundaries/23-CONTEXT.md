# Phase 23 Context — Layer Registry and narrative boundaries

**Gathered:** 2026-07-27  
**Status:** Complete (retroactive verification); implementation already merged in PR #15/#19.

## Boundary

固定 S/D/R/A 四个正交命名空间，明确 Narrative Unit 与 Narrative Memory 的用途、Active 语义和消费顺序；Facet 只能作为带证据的只读投影。NM 继续 candidate-only，不创建 promotion、active pointer 或 Reader Chat cutover。

## Canonical decisions

- 语义粒度使用 S0–S6；数据成熟度、发布生命周期和软件架构分别使用 D*、R*、A*。
- 新字段区分 `chunk_level`、`semantic_level`、`release_status`；存量数据库列不做无授权强制迁移。
- PostgreSQL accepted facts 是结构事实源；Neo4j/Facet 只读 replay，不反写主结构。
- ADR-0001 与 ADR-0002 是后续 Phase 24+ 的架构词汇和边界依据。

## Verification scope

只做文档/契约验证和静态检查；不启动模型、不修改生产数据、不改变 NM 状态。
