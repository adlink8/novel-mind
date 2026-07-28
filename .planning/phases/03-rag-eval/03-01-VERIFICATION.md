---
phase: 03-rag-eval
verified: 2026-06-13T16:25:00+08:00
status: gaps_found
score: 5/8
---

# 03-01 Verification

## Result

评测基础设施可运行，但 GitHub Issue #2 的质量闭环验收未完成。

| 验收项 | 状态 | 证据 |
|---|---|---|
| eval dataset 数据结构 | VERIFIED | 三表 ORM + migration `518675fa18f8`，Alembic check 通过 |
| AI 候选题生成 | VERIFIED | `generate_eval_candidates.py` + 8 个测试 |
| 人工确认/驳回 | VERIFIED | PATCH API + `/eval` UI；owner 隔离回归测试 |
| 100 条 confirmed | MISSING | 数据库为 10 confirmed / 90 candidate |
| `run_rag_eval.py` | VERIFIED | 可输出 JSON 与 Markdown；损坏导入脚本已重写 |
| baseline vector vs hybrid | VERIFIED | 两策略均有真实运行记录，另有 bm25 |
| recall/precision/faithfulness/latency/cost | PARTIAL | 检索指标与延迟存在；faithfulness/cost 为 null |
| 错误案例记录 | VERIFIED | EvalResult.is_error_case + report error_cases |

## Boundary Verification

- 所有 eval API 强制认证。
- dataset/run 查询、更新、触发均通过 `Novel.owner_id` 隔离；跨用户返回 404。
- Schema 仅接受已实现策略，`hybrid_worker` 不再伪装为可用。

## Commands

- Backend non-e2e: 239 passed
- RAG e2e: 12 passed（清除 localhost 代理后）
- Ruff: passed
- Bandit: 0 medium/high
- pip-audit: timed out after 180 seconds; dependency vulnerability state not re-verified
- Alembic upgrade/current/check: passed, head `518675fa18f8`
- Frontend lint/test/build: passed, 22 Vitest

## Required Closure

1. 人工校准 gold chunks，并将高质量题目确认到 100 条。
2. 重新运行 bm25 / baseline_vector / hybrid_search，取得可解释的非零基线。
3. 实现 faithfulness 与 cost，或从 Issue #2 验收标准中明确拆出后续 issue。
4. 将长时评测改为持久化后台任务，避免阻塞 HTTP 请求。
