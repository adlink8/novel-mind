# Phase 31: 三重知识空间契约 — Context

**Gathered:** 2026-07-27
**Status:** In progress

## Phase Boundary

建立 Original Canon、User Interpretation、Fanfiction Canon 的可持久化空间契约，
明确 authority、namespace、version、citation 语义，并让原作分析/检索/评测/NM 输入
在代码层拒绝创作空间内容。此阶段不生成文本、不调用模型、不 promotion NM、不修改任何
active pointer，也不切换 Reader Chat 的消费层。

## Decisions

- `original_canon` 是原作章节与原作分析事实的唯一输入空间。
- `user_interpretation` 是用户解释/override 空间，可引用原作，但不能冒充原作事实。
- `fanfiction_canon` 是创作项目空间，只能被创作域消费，默认不可索引到原作 raw chunks、units、facets、评测语料或 NM builder。
- 每个 artifact 必须绑定 owner、novel、space、namespace、version_key、authority 和 citation policy。
- 负向边界优先 fail-closed；未知空间、跨 novel 引用和未经声明的 authority 一律拒绝。

## Deferred / Forbidden

- 不实现 Phase 30 的 Narrative Memory promotion、active pointer CAS 或 Reader Chat cutover。
- 不执行真实创作生成、付费模型调用或生产迁移。

## Verification Shape

- ORM metadata/import smoke and migration head check.
- Unit tests for space validation, owner/novel scope, original-pipeline rejection, and citation policy.
- Existing retrieval, facet and reader-chat suites remain green.
