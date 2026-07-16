---
phase: 17-frozen-single-book-qualification-and-candidate-verdict
source: approved-v0.8-requirements
requirements: [V08-QUAL-01, V08-QUAL-02, V08-QUAL-03, V08-QUAL-04, V08-QUAL-05]
planning_mode: non-interactive
execution_authorized: false
---

# Phase 17 Context

## Outcome

对一部显式 owner-scoped 小说和一个已完成、封存且结构验证通过的 narrative-memory candidate version，运行预先冻结的单书对比资格评测。hierarchical candidate 与同源 Phase 07 leaf/raw baseline 在相同 source snapshot、reading cutoff、问题、检索/生成预算和计费口径下比较；fresh PostgreSQL verifier 独立重算全部权威与生产 pointer before/after。最终固定命令只允许输出 `qualified_candidate` 或 `blocked`，且无论结果如何都不执行 promotion、active pointer 更新或 consumer cutover。

## Locked Decisions

- Phase 17 执行前置是 Phase 12–16 全部完成并独立验证通过；当前 Phase 13 WIP/暂停状态绝不能被本规划绕过或自动推进。
- 每个资格包必须在查看任何 candidate retrieval/answer/metric 输出前冻结并持久绑定：owner、novel、source snapshot、hierarchy build/checksum、candidate version/manifest、reading cutoff policy、question fixture、threshold policy、generator/judge lineage、prompt/schema/model/decoding/config/pricing/budget hashes。
- 题集是单书、版本化、不可变且人工预审的，至少显式覆盖 `local`、`cross_chapter_arc`、`whole_book_global`、`no_answer` 和 `spoiler` 五个 bucket。每题冻结 expected answerability、允许的 route、gold leaf evidence identity/rank relevance、cutoff 和禁止泄漏集合；不得从候选输出反向生成或修改题目/答案。
- candidate 与 baseline 使用同一个 paired case envelope。除 retrieval strategy 外，source、cutoff、query、top-k/leaf budget、answer prompt/schema、generator、judge、token/cost ceiling、timeout 和重试策略必须相同。baseline 只从 Phase 07 可见 leaf/raw evidence 检索，不读取上层 candidate claims。
- candidate 最终 citation 与 baseline citation 都必须经过 Phase 15 的服务器 Unicode code-point re-slice/hash validator；上层摘要、claim、routing/similarity score、stored excerpt 或聊天文本不能作为最终证据。
- generator 与 Judge 必须采用已冻结、已校准且相互隔离的 lineage。Judge 不能单独授予资格；确定性 evidence/spoiler/lineage/pointer gates 优先且任一硬失败直接 `blocked`。
- 指标必须逐 case、逐 bucket、逐 strategy 和 aggregate 完整输出：leaf recall/ranking、route hit/fallback、no-answer abstention、spoiler leakage、faithfulness/relevance、latency p50/p95、calls/tokens/cost/cache，以及 Phase 16 reuse/rebuild/carry-forward、observed actual 与 avoided upper-bound economics。必需分母为空、样本缺失、NaN、未知价格或缺少 lineage 不得填零或忽略，必须 `blocked`。
- 质量阈值在运行前随 policy 冻结。不得根据 candidate 结果调阈值；相对 baseline 与绝对安全阈值都需 fail closed。所有 spoiler、citation、scope、manifest、pointer 和必需指标门是零容忍。
- fresh verifier 使用独立数据库 session/transaction 和数据库原始行重算，不信任 worker/runner 自报。它重验 Phase 12 eligibility、Phase 13 graph/manifest/claim→leaf、Phase 14 complete build/report、Phase 15 retrieval manifests/citations/cutoff、Phase 16 reuse report，以及 owner/novel/snapshot/build scope。
- 资格运行允许写入 append-only qualification audit/result authority 和其受控 model-call/cost evidence，但不得写 narrative memory candidate facts、任何既有分析资产、production pointer/revision/journal 或 active baseline。
- 唯一产品可见结论是单书 candidate 范围内的 `qualified_candidate` 或 `blocked`。不得输出 `passed`、`promoted`、`active`、`production_ready` 等误导状态。
- 报告必须声明：结论不会替换 timeline、relationship、clue 或 Reader Chat，不代表任何 consumer 已切换，也不声称关闭 v0.3 的 100 confirmed、全项目 faithfulness/cost 或其他跨书缺口。

## Scope

### In scope

- Strict frozen qualification fixture/policy/case/bucket/paired-run/report/verdict contracts and canonical hashes.
- 单书 fixture 的预冻结、人工审阅 metadata、gold leaf references、no-answer/spoiler adversarial cases 与 deterministic fixture validation.
- 同源 leaf/raw baseline adapter、paired hierarchical-vs-baseline runner、isolated generator/Judge integration and exact cost accounting.
- Retrieval/routing/answer/safety/latency/cost/cache/reuse complete metric aggregation and predeclared threshold evaluator.
- Additive append-only qualification run/report/call audit authority, fresh PostgreSQL verifier, production pointer before/after digest and fixed CLI.
- Unit, PostgreSQL integration, adversarial, deterministic replay and fixed-command tests.

### Out of scope

- 创建、补建、修复或重分析 Phase 13–16 candidate；缺失依赖只能阻断。
- promotion、rollback、active/current pointer、consumer binding、Reader Chat/timeline/relationship/clue/search cutover 或产品 UI。
- 生成评测题、查看结果后改 gold/threshold、全书库批量资格、跨书统计推广或生产流量 A/B。
- 把 candidate summary/claim、Judge 分数、聊天文本或 similarity 当成证据。
- 宣称 v0.3 的 100 confirmed、跨书 faithfulness/cost 或全项目质量缺口已关闭。

## Execution Preconditions

在任何 Phase 17 实现或运行开始前，必须同时满足：

1. 用户重新明确授权执行；当前 planning-only gate 已解除。
2. Phase 13 WIP 完成并独立验证，Phase 14–16 按顺序执行且各自 verification 为通过状态。
3. 单书 owner/novel/candidate version 显式提供，Phase 12 hierarchy 为 `reusable_exact`，Phase 13 manifest sealed/verified，Phase 14 build complete，Phase 15 experiment artifacts 与 Phase 16 reuse authority可读。
4. fixture/policy 已在结果生成前冻结并取得独立 checksum；未知价格、未校准 Judge、预算未批准或 provider dependency 不健康时保持零新 provider 调用并输出 `blocked`。
5. 运行前 fresh observer 已保存所有生产 pointer/revision/journal 的 canonical before digest；无法取得完整快照时不得开始付费评测。

## Verification Standard

- 冻结 fixture 的 hash 在运行前后 byte-identical，candidate 输出不能影响问题、gold、bucket、cutoff 或 policy。
- paired runner 证明两策略除 retrieval strategy/trace 外共享完全相同的 case envelope、source/cutoff/generator/Judge/budget/price lineage；不具可比性直接阻断。
- 每个 bucket 有非零、预声明样本并产生完整指标；no-answer 和 spoiler 使用专门零容忍/abstention gates。
- fresh PostgreSQL verifier 从真实 Chapter/content、Phase 07 leaf、Phase 13–16 authority 重算所有 lineage、manifest、citation 与 reuse economics；runner report 只作待核对输入。
- before/after 证明 chunk/timeline/clue/quality baseline 以及所有现有 production selectors/revisions/journals byte-equivalent，并证明没有 narrative-memory active pointer/promotion surface。
- 固定命令正常资格返回退出码 0 + `qualified_candidate`；任何依赖、数据、质量、安全、完整性或 observer 失败返回非零 + `blocked`，stdout canonical digest 与 PostgreSQL audit 匹配。

## Execution Gate

这些文件仅为规划资产。本轮不得实现、迁移、运行测试、调用 provider 或执行单书资格。只有用户另行明确授权且 Phase 13–16 全部完成并验证后，才能从 17-01 开始。

---

*Context derived non-interactively from the approved v0.8 qualification requirements and the explicit instruction to plan without execution.*
