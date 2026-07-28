---
phase: 16-dependency-aware-local-rebuild-and-carry-forward
source: approved-v0.8-requirements
requirements: [V08-REUSE-01, V08-REUSE-02, V08-REUSE-03, V08-REUSE-04]
planning_mode: non-interactive
execution_authorized: false
---

# Phase 16 Context

## Outcome

比较一个已验证的父 candidate 与一个新的、通过 Phase 12 审计的目标 source/hierarchy，确定性构建 source/evidence → Chapter State → Story Arc/Volume → Global Story Model 依赖图，计算最小可证明安全的 dirty closure。未进入 dirty closure 的资产以语义 checksum-identical、目标 lineage 有效的方式复制到新的显式 candidate version；无法证明边界或依赖稳定时保守扩散。最终报告可复核 rebuilt/carried/stale、实际与避免的 calls/tokens/cost、dirty ranges 和 cache reuse，且全过程仍是 candidate-only。

## Locked Decisions

- Phase 16 只能在 Phase 13、14、15 全部完成并独立验证，且用户重新明确授权实施后执行；当前暂停的 Phase 13 不得由本规划推进。
- Change oracle、依赖图、dirty closure、carry-forward 选择和复用经济估算均为确定性脚本逻辑，禁止 provider、embedding、rerank、Reader Chat 或模型判定。
- Oracle 输入必须显式指定 owner、novel、父 candidate version、目标 candidate version、父/目标 Phase 07 build、父/目标 source snapshot、目标 Phase 12 eligibility report 与冻结 policy；不得解析 current/active pointer。
- 依赖图从数据库权威重建，不信任调用方自报 dependency JSON：Phase 07 leaf/source links → Chapter State claims/nodes → frozen Arc/Volume boundary and child edges → Global；可选 timeline/relationship/clue 依赖只在父候选实际消费且 lineage 可证明时加入。
- 变更分类至少覆盖 chapter edit、insert、delete、reorder、evidence split/merge、arc-boundary change、optional-source lineage change 和 dependency uncertainty，并使用封闭 reason codes。
- Carry-forward 的“checksum-identical”指 authoritative node/claim typed content、visibility/uncertainty、semantic dependency 与 evidence content identity保持一致；数据库 ID、目标 version/build/snapshot scope 和重新绑定的 source-link checksum 可以变化，但必须由目标 authority 重算并在报告中区分。
- 只有当稳定 chapter identity、目标 leaf 等价映射、直接 evidence closure、边界、子依赖、optional-source lineage 和 schema/model/policy compatibility 全部可证明时，资产才可 carry。仅文本相似、embedding、标题、摘要或相同长度不构成等价证明。
- Arc/Volume 仅在边界 checksum 相同、完整子集 clean 且跨章依赖稳定时 carry；Global 仅在全部中层节点 clean/carried 且全图依赖稳定时 carry。
- chapter insert/delete/reorder、边界变化、跨章状态延续或 dependency lineage 无法证明稳定时，从最早不确定点保守扩散到受影响 arc/volume、必要后缀和 Global；不能保留可能 stale 的父节点。
- Carry-forward 只向新的显式、未 seal candidate version 插入严格 Phase 13 rows，并重绑目标 Phase 07 leaf；不得 UPDATE/DELETE 父 candidate，不得复制 manifest/seal/report，不得移动任何 pointer。
- Oracle 和 carry-forward 本身零 provider 调用。执行 dirty stages 时，只把冻结 rebuild plan 交给 Phase 14 worker；所有实际调用仍必须经过 Phase 14 eligibility/budget/cache/checkpoint/cancel contract。
- Phase 15 检索产物与 query/trace 不是构建依赖或事实来源；Phase 16 只回归验证新 candidate 在 Phase 15 显式版本读取下仍满足 scope/citation 安全。
- Reuse report 分开记录 observed actual、deterministic full-rebuild upper bound、avoided upper bound、exact-cache reuse 与 carry-forward reuse，禁止把估算成本伪装成实际节省或 Phase 17 qualification。

## Scope

### In scope

- Strict rebuild scope/change/dependency/decision/report contracts and canonical hashing.
- Additive candidate-only persistence for immutable rebuild plans/items and append-only reuse reports.
- Lossless PostgreSQL dependency graph reconstruction and deterministic change oracle.
- Conservative dirty propagation and stable reason/range output for edit/insert/delete/reorder/boundary/uncertainty fixtures.
- Exact semantic carry-forward into a new explicit version with target leaf rebind, idempotency and stale-reference rejection.
- Phase 14 stage filtering so only frozen dirty stages may call provider; clean assets use carry-forward or exact cache.
- Reuse economics from persisted plans, Phase 14 stages/calls/budgets/cache and database manifests.
- Unit, PostgreSQL, concurrency, adversarial, fixed-CLI and fresh-observer no-pointer verification.

### Out of scope

- Executing unfinished Phase 13–15 work, reanalyzing production books during planning, or running any test/migration in this planning task.
- Provider calls inside the oracle, graph builder, carry-forward copier or report calculator.
- Fuzzy semantic equivalence, embeddings, LLM boundary decisions or automatic acceptance of uncertain reuse.
- Production promotion, active/current pointer, rollback, consumer cutover, all-library rebuild or product UI.
- Phase 15 routing/citation changes and Phase 17 quality qualification/verdict.

## Verification Standard

- Fixed unit fixtures produce byte-stable dependency graphs, change sets, dirty closures and reason ordering for edit/insert/delete/reorder/boundary/optional-source cases.
- PostgreSQL tests reconstruct dependencies from scoped rows, reject cross-owner/novel/version/build/snapshot data and prove oracle/carry-forward/report paths make zero provider calls.
- Carry-forward tests prove semantic node/claim checksums stay identical, target source links are freshly rebound/revalidated, and parent rows/manifests remain byte-equivalent.
- Conservative cases prove uncertain mapping/boundary/cross-chapter lineage expands to the correct suffix/parent/Global and never carries a stale ancestor.
- Phase 14 integration tests prove only dirty stages can reserve budget/call; clean assets remain Phase 16 carry-forward rebuild items and create no Phase 14 stage, provider attempt or embedding/index write; resume remains stage-idempotent.
- Independent report recomputation reconciles plans, manifests, stage/call/budget rows, cache hits and full-rebuild upper-bound assumptions.
- Fresh observer proves no chunk/timeline/clue/narrative-unit/narrative-memory pointer, revision or journal changes through plan, carry, dirty execution, seal and report.

## Execution Gate

These files are planning artifacts only. Do not implement or run Phase 16 until the user separately re-authorizes execution and Phases 13, 14 and 15 are complete and independently verified.
