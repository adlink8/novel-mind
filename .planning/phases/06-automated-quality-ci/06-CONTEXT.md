---
phase: 06-automated-quality-ci
status: ready_for_planning
created: 2026-07-12
depends_on: [04-llm, 05-narrative-knowledge-unit-layer]
---

# Phase 06: Automated Quality and CI - Context

## Phase Goal

补全自动化测试与 CI 缺陷，并把 RAG 日常质量验收改造成无需人工逐题确认、可冻结、可复跑、可审计、失败关闭的自动资格门。

## Current Facts

- 三个 GitHub Actions workflow 重复；默认 pytest 隐式排除 `e2e`，无 PostgreSQL/Chroma services、Alembic CI、coverage、JUnit、API contract、Playwright 与稳定 `ci-gate`。
- `test_rag_e2e.py` 在 Ollama 不可用时退回随机 embedding，语义断言可能假通过。
- `EvalService` 只检索、不生成 SUT answer；faithfulness/cost 未计算；逐题异常被吞成 0 分；candidate 与 confirmed 都能进入评测。
- 前端只有少量 API/utility 测试。
- Phase 04 已有 evidence-bound LLM gate；Phase 05 已有 frozen fixture hash、signed promotion evidence、fail-closed 与 durable resume 模式，可直接复用。

## Locked Decisions

- **D-01:** 质量评测采用双模型仲裁。Generator 与独立 Judge 必须来自不同模型族/权重；模型身份不满足隔离时运行失败关闭。deterministic arbiter 是最终资格门，Judge 不能单独 promote。
- **D-02:** 日常验收不需要人工逐题确认。自动 fixture 依次通过 Generator evidence-bound QA、确定性规则、独立 Judge 复核后 freeze；最多 regenerate 2 次，仍失败则 quarantine。
- **D-03:** 冻结 source snapshot 是真值。case 以 chunk content hash、evidence offsets、quote hash、claims 与 equivalent evidence sets 表达；数据库自增 gold chunk ID 不得作为唯一真值。
- **D-04:** 测试分类为 `unit/integration/contract/live`，允许组合 `e2e` 表示跨层范围；移除 pytest 默认隐式 e2e 排除。随机向量只允许验证 vector-store contract，不得参与语义质量或 live 测试。
- **D-05:** PostgreSQL 16 集成必须验证 migration heads、空库 upgrade/current/check、历史 revision upgrade、tsvector、约束与并发；Chroma 镜像必须 pin digest/version并有 healthcheck；覆盖 DB/Chroma 故障及恢复。
- **D-06:** 自动 RAG 流程固定为 `source snapshot -> Generator QA -> deterministic validation -> independent Judge -> freeze -> SUT answer -> deterministic + Judge scoring -> deterministic arbiter`，含 no-answer、hard-negative、3 次重复、漂移、成本、延迟与 durable run/resume。
- **D-07:** live 依赖不可用必须得到 `blocked_dependency`，不得生成可比较质量分；异常不得转成 0 分。candidate/confirmed 人工门退出资格路径，但旧 Eval API 保持兼容并有迁移期，不做一次性破坏替换。
- **D-08:** 指标必须包含 answer faithfulness/relevance、context precision/recall；critical unsupported claim rate=0；faithfulness 95% lower bound>=0.90；context recall@5 相对回归<=2pp；answer relevance 回归<=3pp；verdict consistency>=0.80；cost<=baseline+15%。p95 budgets 来自版本化 policy，缺失即失败关闭。
- **D-09:** 覆盖率门固定：backend overall line>=80/branch>=70，critical auth/security/import/promotion/rollback line>=90/branch>=85；frontend line>=75/branch>=65，hooks/store/API line>=85/branch>=75；diff coverage>=90。
- **D-10:** 稳定性门固定：PR flake=0；required checks 30 天失败率<0.1%；外部 infra 最多 retry 1 次且保存首次失败证据。
- **D-11:** 自动 fixture 门固定：所有 deterministic checks 通过；Judge faithfulness/coverage/sufficiency 各>=3/4；critical ambiguity=0。
- **D-12:** 浏览器矩阵至少 desktop 与 390px，覆盖注册、登录、上传、导入、搜索、阅读、eval、跨用户和错误状态，并保留 trace/screenshots。
- **D-13:** CI 收敛到单一 required `ci-gate`：PR secretless deterministic + PostgreSQL/Chroma + Chromium smoke；main full integration；nightly/self-hosted strict Ollama + dual-model benchmark。落实 fork 安全、最小 permissions、concurrency、timeouts、artifacts/retention、CodeQL、actionlint、依赖审计、baseline promotion 与告警。
- **D-14:** 时限目标：PR<=15 分钟；nightly<=60 分钟。超时按失败处理并产出诊断 artifact。
- **D-15:** Judge 与 Generator 必须同时满足不同模型族和不同实际 weights/revision；Judge revision 必须先通过冻结签名 calibration suite。calibration 覆盖 supported/partial/unsupported/contradictory/no-answer/hard-negative/equivalent evidence，critical false accept=0、3-repeat consistency>=0.80，否则 `invalid_lineage`。calibration 与 benchmark 使用不同 hash/domain。
- **D-16:** timeout 固定：unit 5s、contract 15s、integration 30s、browser 60s、live 180s；job timeout 固定：static 5m、unit 10m、integration 15m、browser 15m、live 45m、nightly 60m。
- **D-17:** artifact retention 固定：PR JUnit/coverage/OpenAPI 14d；Playwright failure 7d；main integration/service logs 30d；nightly signed RAG reports/baselines 180d。敏感原文禁止上传。
- **D-18:** GitHub Issue alert 只能由 schedule/protected-main 的独立 environment-approved job 发出，权限仅 `contents: read, issues: write`，不 checkout PR 代码，只消费 hash/schema 验证报告；fork 不可达。`workflow_dispatch` 必须验证 protected ref、environment 和 fixed benchmark commit。
- **D-19:** branch protection 必须以 `gh api` 幂等设置并回读验证唯一 required context `ci-gate`。权限不足时状态为 `blocked_external_configuration`，Phase 06 不得完成。

## Agent Discretion

- 新模型/表/服务/endpoint 的精确名称，以及兼容 API 的版本化方式，只要旧调用在迁移期仍工作且返回明确 deprecation metadata。
- 仅工具接线细节可裁量；工具与版本已在 RESEARCH 锁定，不得替换。
- nightly 告警媒介；若仓库未配置外部通知凭据，创建/更新 GitHub Issue 或 job summary 作为 secretless 默认通道。

## Deferred Ideas

- 不在本阶段构建通用人工标注平台。
- 不把生产用户反馈自动写成 frozen truth。
- 不替换 Phase 05 promotion/rollback 协议，也不引入新的向量数据库。
- 不要求 PR 执行付费或私有模型调用。

## Acceptance Thresholds

以上 D-08 至 D-14 的数字全部锁定；任何 policy、baseline、snapshot、model lineage 或 live dependency 缺失均不得降级为可比较分数。

## Seven-Plan Delivery Map

1. `06-01` 测试分类与确定性基础（wave 1）。
2. `06-02` PostgreSQL/Chroma 真实服务（wave 2，依赖 01）。
3. `06-03` frozen fixture、adversarial contracts、G/J 隔离与 calibration（wave 3，依赖 01/02）。
4. `06-04` SUT scoring、policy、durable worker、兼容迁移与 live dual-model（wave 4，依赖 01..03）。
5. `06-05` OpenAPI、frontend/component 与 Playwright（wave 5，依赖 01..04）。
6. `06-06` unified CI DAG、fork safety、artifacts/security、nightly、baseline/alert（wave 6，依赖 01..05）。
7. `06-07` ci-gate aggregate、branch protection、外部配置阻断与最终 release gate（wave 7，依赖 01..06）。
