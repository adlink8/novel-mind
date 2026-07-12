---
phase: 06-automated-quality-ci
level: 2
status: complete
date: 2026-07-12
---

# Phase 06 Research

## Recommendation

采用“确定性 PR 验证 + 真实服务集成 + nightly live 模型资格评测”的三层体系。测试选择必须显式，质量分必须绑定冻结数据、模型 lineage、policy 版本和依赖健康状态；最终 `ci-gate` 只聚合明确的上游结论，不自行重跑测试。

## Local Evidence

| Finding | Evidence | Planning consequence |
|---|---|---|
| pytest 默认排除 e2e | `backend/pytest.ini` 的 `addopts=-m "not e2e"` | 删除隐式选择，CI 命令显式选 marker |
| 语义 E2E 可假通过 | `backend/tests/test_rag_e2e.py::_try_generate_embeddings` 回退随机向量 | live 失败标记 blocked；随机向量迁至 vector-store contract |
| Eval 未生成答案 | `backend/app/services/eval_service.py` 只调用检索 | 新引擎必须执行 SUT answer 并持久化 answer/claims/usage |
| 异常伪装成质量 0 | `run_eval` catch-all 写 0 指标 | 状态与分数分离，异常 fail closed |
| 人工状态控制题集 | `/api/eval/datasets` candidate/confirmed/rejected | 旧 API 兼容，新 frozen fixture 成为资格真值 |
| 可复用完整性模式 | Phase 05 fixture hash、HMAC promotion envelope、prepare/commit、resume | 复用 hash/signature/journal，不另造弱协议 |

## Test and CI Design

- pytest 注册 `unit/integration/contract/live/e2e` 并启用 strict markers；每个测试至少有一个主要类别。官方 pytest 文档确认 strict markers 会拒绝未注册标记，JUnit 可由 `--junitxml` 生成。
- PR 使用 secretless 固定 fixture、PostgreSQL 16 service、固定 Chroma image 与 Chromium smoke；live 模型只在受控 main/nightly/self-hosted 环境运行。
- GitHub Actions 并发默认不互斥，因此 workflow 需要按 workflow/ref 设置 concurrency；PR 可 cancel-in-progress，nightly benchmark 不取消已开始的 baseline 比较。
- Playwright CI 安装固定浏览器版本，失败保留 trace、截图和 HTML report；390px 是独立 project，不只调整 CSS 单测。
- CodeQL 采用 GitHub 官方 advanced/default setup 二选一，避免重复扫描配置；Python 与 JavaScript/TypeScript 均纳入。

## RAG Evaluation Architecture

1. Source snapshot 按 owner/work/version 冻结原文；chunk identity 是 canonicalized content SHA-256，不依赖数据库 ID。
2. Generator 只从 evidence package 生成结构化 question、claims、answer、offset/quote hash、equivalent evidence sets、case type。
3. deterministic validator 验证 offset 对应文本、quote hash、claim support、去重、泄漏、no-answer/hard-negative 条件。
4. 不同模型族/权重的 Judge 按 0..4 rubric 独立评分；三项均>=3 且 critical ambiguity=0 才能 freeze，最多重生成 2 次。
5. SUT 对 frozen case 生成答案；deterministic scorer 计算 evidence/claim overlap 和 retrieval 指标，Judge 计算语义 faithfulness/relevance；三次重复得到一致性与置信界。
6. deterministic arbiter 读取版本化 policy、baseline、health、lineage、成本与延迟，唯一决定 qualified/regressed/blocked/quarantined/invalid。

## Compatibility Strategy

- 保留现有 `POST /api/eval/runs`、`GET /runs/{id}`、dataset list/patch 的请求形状；内部将旧请求适配为 legacy retrieval run，响应继续提供旧字段并追加 `job_id/status/quality_comparable/deprecation`。
- 新后台 API 提供 create/status/report/resume/cancel；旧同步客户端在短迁移期可获得 completed report，超出同步预算则返回 accepted/job id，而不是保持长事务。
- `candidate/confirmed` 只保留浏览与迁移用途；资格评测仅接受签名 frozen fixture。迁移器把可验证旧 gold_chunks 转换为 hash/offset evidence，无法证明的条目 quarantine。

## Package Legitimacy Audit

查询日期：2026-07-12。以下来源、版本与 owner 已核验；本次仅规划，不执行安装。Go 工具安装时必须启用 Go checksum database 验证，校验失败即停止。

| Package/tool | Locked version | Owner | Verified source | Status |
|---|---:|---|---|---|
| pytest-cov | 7.1.0 | pytest-dev | PyPI `pytest-cov` / github.com/pytest-dev/pytest-cov | VERIFIED |
| pytest-timeout | 2.4.0 | pytest-dev | PyPI `pytest-timeout` / github.com/pytest-dev/pytest-timeout | VERIFIED |
| vitest | 4.1.10 | Vitest | npm `vitest` / github.com/vitest-dev/vitest | VERIFIED |
| @vitest/coverage-v8 | 4.1.10 | Vitest | npm `@vitest/coverage-v8` | VERIFIED |
| @playwright/test | 1.61.1 | Microsoft | npm `@playwright/test` / github.com/microsoft/playwright | VERIFIED |
| oasdiff | v1.17.0 | oasdiff | github.com/oasdiff/oasdiff | VERIFIED |
| actionlint | v1.7.12 | rhysd | github.com/rhysd/actionlint | VERIFIED |
| chromadb Python | 1.5.9 | Chroma | PyPI `chromadb` | VERIFIED |

Locked commands: `go install github.com/oasdiff/oasdiff@v1.17.0`; `oasdiff breaking backend/openapi-baseline.json artifacts/openapi.json --fail-on ERR`; `go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12`; `actionlint -format JSON`.

## Service Locks

- Chroma: `chromadb/chroma:1.5.9@sha256:abcce7c335e2dab9f11ef629296f7309b09cb19ae4b34da32ac7e34ff5773140`; health `/api/v2/heartbeat`; Python client `chromadb==1.5.9`.
- PostgreSQL: `postgres:16.10`;执行前由 registry 解析 digest 并写入 CI lock manifest。digest 缺失、tag/digest 不匹配或 manifest 未签入均 fail closed。

## Execution Dependency Graph

`06-01 -> 06-02 -> 06-03 -> 06-04 -> 06-05 -> 06-06 -> 06-07`。拆分边界分别是测试基础、真实服务、fixture/calibration、SUT运行质量、API/browser、CI生产者DAG、最终聚合与远端保护；所有阈值、工具锁和安全边界跨计划保持不变。

## Risks and Mitigations

- Judge 自洽偏差：模型族隔离、校准集、盲评、重复三次、deterministic final arbiter。
- fixture 污染：冻结 snapshot、prompt/model/schema lineage、HMAC 签名、baseline promotion 审计。
- live outage：`blocked_dependency` 与 quality_comparable=false，禁止写 0 或沿用旧分。
- flaky infra：只 retry 一次，首次日志/trace 必须先保存；第二次通过仍记录 flake，PR 门要求 flake=0。
- fork secrets：`pull_request` secretless，不使用不受信代码可接触 secrets 的 `pull_request_target` 执行路径。

## Official References

- pytest configuration/reference: https://docs.pytest.org/en/stable/reference/reference.html
- pytest JUnit output: https://docs.pytest.org/en/stable/how-to/output.html
- GitHub Actions concurrency: https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
- GitHub CodeQL workflow configuration: https://docs.github.com/en/code-security/reference/code-scanning/workflow-configuration-options
- Playwright CI: https://playwright.dev/docs/ci
