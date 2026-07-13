# Phase 07 Context — Semantic and Hierarchical Chunking

**Status:** COMPLETE (2026-07-13) — plans 07-01..07-06 executed; see `07-VERIFICATION.md`.  
**Residuals:** production PostgreSQL hierarchy/build tables and full `indexing_service` / `hybrid_search` wiring (logic covered by `InMemoryBuildStore` + unit/integration tests).

## Goal

在保留原始规则 chunk 和原文证据底座的前提下，把现有 `ChunkingService` 演进为可审计的 semantic/hierarchical candidate pipeline：规则负责完整初切、边界置信度、硬约束和 fallback；LLM 仅裁决低置信边界；确定性服务负责 chapter → scene → evidence 组装、不可变 candidate、增量刷新、索引 reconcile、质量资格、promotion 与 rollback。

## Decisions

- **D-01 — 六计划顺序锁定。** 07-01 到 07-06 分别覆盖 REQ-CHUNK-01；02；03+07；04；05+06；08，执行波次严格为 1→6。
- **D-02 — 保留 deterministic baseline。** 现有规则 chunker 是 A 组和原始证据底座；Phase 07 在其上增加稳定 source offsets、snapshot 和 manifest lineage，不删除或改写其可追溯性。
- **D-03 — 规则先行。** 所有相邻 atomic span 边界均由规则产生 proposal、版本化 confidence 和 reason codes；chapter edge、非法 offset 与 hard max 是 LLM 不可覆盖的硬约束。
- **D-04 — LLM 最小权限。** 仅低置信边界可调用 LiteLLM；输出只允许 strict schema 的 `split|merge|abstain`、输入 span ID 上下文保留建议和枚举 reason code。LLM 无 DB/session、filesystem、retriever、network、tools/function calls、索引、发布或 active pointer 权限，也不得生成或改写正文与事实。
- **D-05 — 确定性 fallback 与预算。** provider 不可用、timeout/refusal、schema/ID/offset/hash 非法或预算耗尽时，使用规则 fallback，并将原因、attempt、usage、latency 和 lineage 明确写入 manifest；fallback 完成不等于取得质量资格。
- **D-06 — 层级与原始回退并存。** PostgreSQL 保存 chapter → scene → evidence 的权威关系和 source coordinates；检索以 evidence 命中、scene 扩展、chapter 导航，raw chunks 始终可回退。
- **D-07 — candidate 不移动 active。** 新 chunker 只构建 immutable candidate collection/build；构建、索引或质量失败时 active pointer 保持原值。任何原地覆盖 active 的路径均禁止。
- **D-08 — 增量与恢复复用既有协议。** source chapter 或 chunker lineage 变化只重切受影响章节，未变节点 carry-forward；精确 reconcile 清理 orphan/stale vectors；prepare/commit、CAS、rollback/restore 沿用 Phase 05 已验证语义。
- **D-09 — Phase 06 是唯一发布资格裁决者。** A/B 必须共享冻结语料、质量 policy 和 source snapshot，比较质量、faithfulness、成本、延迟、fallback 与 lineage；缺失或不一致即 `quality_comparable=false`，不得 promotion。
- **D-10 — 质量 lineage 前置依赖。** 07-06 明确依赖 REQ-AUTO-11 及 `.planning/phases/06-automated-quality-ci/06-08-PLAN.md` 提供的 chunker/version/config/manifest/source snapshot 五元 lineage 和同 snapshot 比较契约。
- **D-11 — 不新增框架。** 继续使用现有 FastAPI、SQLAlchemy、PostgreSQL、Chroma、LiteLLM 和 Pydantic；不引入 LangChain、LlamaIndex、LangGraph、agent SDK 或新的编排框架。
- **D-12 — 验收必须自动化。** 每个 implementation slice 最后一步均为 `Test, Fix, and Confirm`，覆盖 offset/coverage/tree、schema/adversarial、candidate/reconcile/rollback 和 frozen A/B release verifier。

## the agent's Discretion

- 在不改变上述契约的前提下，执行者可按仓库实际模型与迁移命名调整具体类名、表名和 Alembic revision ID。
- 可将研究建议的 `backend/app/services/chunking/` 模块边界合并或细分，但公共职责、lineage 字段、权限边界和计划文件所有权不得弱化。
- 测试文件可复用现有 fixture helper；若实际仓库中 Phase 06 的服务文件名与 06-08 计划预期不同，应接入真实接口而不是复制一套平行 evaluator。

## Deferred Ideas

- 动态检索 few-shot 示例或 semantic cache。
- provider outage 时透明切换模型。
- 让 LLM 生成正文、事实、SQL、工具调用或发布决定。
- 删除 deterministic baseline/raw chunk lineage。
- 引入新的 RAG、agent 或 workflow 框架。

## Scope Boundaries

- 本阶段只演进 chunk candidate pipeline 和它与既有索引、检索、Phase 06 质量门的连接。
- 不重新设计小说导入、embedding provider、知识图谱或 narrative unit 业务语义。
- 首个真实 cutover 仍遵循既有 operator approval/release policy；Phase 07 不扩大 LLM 权限。

## Requirement Mapping

| Requirement | Locked plan |
|---|---|
| REQ-CHUNK-01 | 07-01 |
| REQ-CHUNK-02 | 07-02 |
| REQ-CHUNK-03, REQ-CHUNK-07 | 07-03 |
| REQ-CHUNK-04 | 07-04 |
| REQ-CHUNK-05, REQ-CHUNK-06 | 07-05 |
| REQ-CHUNK-08 | 07-06 |

## Source Coverage Audit

| Source | ID/item | Plan | Status |
|---|---|---:|---|
| GOAL | semantic/hierarchical candidate pipeline with raw evidence and Phase 06 release selection | 01-06 | COVERED |
| REQ | REQ-CHUNK-01..08 | 01-06 | COVERED |
| RESEARCH | offsets/manifests, confidence proposals, bounded adjudication, hierarchy, immutable lifecycle, A/B gates | 01-06 | COVERED |
| AI-SPEC | strict schemas, no tools/write/publish, fallback/budget, exact lineage, fail-closed qualification | 01-06 | COVERED |
| CONTEXT | D-01..D-12 | 01-06 | COVERED |

