---
audit_type: status-refresh
project: NovelMind
date: 2026-07-26
snapshot: master @ e6aa317 (clean tree, synced with origin)
method: git/CI 实况核查 + 两轮代码逐项核验
refreshes: ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md
planning_authority: 规划已按标准 GSD 格式写入 ROADMAP.md（Phase 21–34）与 REQUIREMENTS.md；本文件只记录事实，不含计划。
---

# 现状核查快照（2026-07-26）

## 1. 2026-07-17 审计后已解决的问题

| 编号 | 问题 | 状态 | 证据 |
|---|---|---|---|
| NM-DATA-006 | Clue payoff 状态机阻断（P0） | **已修复** | `2cf8562`：`clues/gates.py` ACTIVE 纳入 REINFORCEMENT/PAYOFF；`worker.py` 物化 ACTIVE→REINFORCED→PAID_OFF 链；回归测试 `test_worker_versions.py` |
| NM-ENG-006 | 审计脚本 schema 漂移 | **已修复** | `2cf8562`：`_audit_novel_gaps.py` 改用真实表名 |
| NM-ENG-003 | 前端生产构建失败 | **已修复** | `relationship-graph.tsx` Css.Core 断言；实测 `tsc --noEmit` 0 错误、`next build` exit 0 |
| NM-ENG-004 | pytest-timeout 未装 | **已修复** | `requirements-dev.txt` 锁 2.4.0；`pytest.ini` timeout=30 + strict-config；conftest 按 marker 分级超时 |
| NM-ENG-001 | TimelineModelGateway 测试漂移 | **已修复** | HEAD 测试与 `model_gateway.py` 契约一致 |
| NM-ENG-005/009 | 工作树脏 / ahead 218 | **已收敛** | 全部落地为提交并推送；75MB dump 停止跟踪（`0b5ba30`，但仍在已推送历史中） |
| — | adversarial marker 未注册 | **已修复** | `dbe3cd9` |

## 2. 仍未解决的审计问题（对应 Phase 见 ROADMAP.md）

| 编号 | 核验结论 | 归入 |
|---|---|---|
| NM-ARCH-001..004 | 无 ADR 目录、无 S0-S6/Layer Registry 落地（全仓零命中）、level 字段仍混用 | Phase 23 |
| NM-GOV-001 | NU/NM 边界契约不存在 | Phase 23 |
| NM-GOV-003 | `indexing_service.py` 无 journal/幂等/reconcile（grep 零命中）；先删 DB 后删 Chroma 有残留窗口；`failed_count > 0` 仍置 novel `ready` | Phase 24 |
| NM-GOV-006 | Neo4j 防双写自动化约束缺失 | Phase 24 |
| NM-DATA-010 | `NarrativeRetrievalStrategy` 覆盖 chunks/units/hybrid 但 mode 由客户端传、无自动降级；NM 完全不在 router；Reader Chat 另有 `SOURCE_PRIORITY` | Phase 24 |
| NM-DATA-005 | `intake_kind`/`producer_kind` 代码零命中 | Phase 25 |
| NM-DATA-007 | judge schema 无 `short_title`；标题仍 rationale 截断（`build_machine_clue_title`） | Phase 25 |
| NM-DATA-008 | clue `cost_usd` 无赋值路径，结算恒 0；NM/timeline/reader_chat gateway 均已有真实结算 | Phase 25 |
| NM-API-001/002/003 | characters 空数组占位、`analyze/stream` 501、fanfiction 501 三项原样 | Phase 25 |
| NM-DATA-009 | 当前 DB `eval_datasets=0` / `eval_runs=0` | Phase 28 |
| NM-DATA-001..004 | 数据覆盖与语义差距（见 §4） | Phase 26/27 |
| NM-ENG-007/008 | codebase map 过期、快照标识缺失 | Phase 21 |

## 3. 审计之后新出现的问题（2026-07-18 ~ 07-26）

| 问题 | 严重度 | 证据 |
|---|---|---|
| master CI 连续多天全红 | **P0** | run 30189436828：Static（Ruff 风格违规）、Integration（1 failed：`test_real_qualification.py::test_release_entry_blocks_postgres_report_authority_mismatch`）、Browser smoke（webServer exit 127）、ci-gate 聚合脚本 SyntaxError、CodeQL Analyze 失败 |
| PR #11 带红 ci-gate 合入 master | **P0 治理** | mergedAt 2026-07-23T15:43:40Z，ci-gate FAILURE；required check 未强制或被绕过 |
| "phase21" 工作未走 GSD | P1 | 无 `.planning/phases/21-*`；STATE.md 曾写 20/20/100%；IMPLEMENTATION-STATUS.md 停在 2026-07-17（Alembic head 误记 `518675fa18f8`，实际 `18appsetting1`；测试计数误记 239，实际 1085 passed / 189 skipped） |
| `backend/app/api/README.md` / `api/__init__.py` 反向漂移 | P1 | 把已实现的 timeline/analysis 仍描述为 501 占位 |
| 75MB DB dump 在已推送 git 历史中 | P2 | `0aa4c21` 引入；彻底移除需历史重写（默认接受现状，需单独决策） |
| Vertex/Gemini 适配为无测试实验态 | P2 | `vertex_gemini.py`：无测试、无文档、`feat(exp)` |

## 4. phase21 分支实际交付（由 Phase 21 追认）

设置中心组件化重构（settings page 633→30 行；account/routing/models/usage section）；`GET/PUT /api/settings/routing` + `GET /api/usage/summary` + `app_settings` 表（迁移 `18appsetting1`，单 head 无分叉，迁移目录为 `backend/migrations/versions/`）；阅读器体验（system 主题、fontSize/lineHeight/contentWidth 偏好、章内进度恢复、immersive chrome、断点 1280）；`/writing` 诚实占位页（3D 翻书 + Planned 印章，不放假功能）；Timeline 服务全面重写（worker/model_gateway/promotion/query/reconcile/jobs/extraction/evidence/budget/overrides）；Reader Chat 服务重写（context/gateway/worker/budget/conversations/retrieval）；`ai_service.py` 接 Vertex 分支 + usage 落库；Vertex/Gemini 实验适配（gcloud token、代理解析、schema 收敛、thinkingBudget=0）；CI e2e job 补 docker compose + alembic + DATABASE_URL 注入；后端测试规模 1085 passed / 189 skipped。

## 5. 数据与产品差距（核心事实，规划输入）

- 样例 novel 91：515 章，NM chapter_state ~117/515（33 failed / 366 pending），**0 Arc、0 Global**，build partial；
- timeline ~1933 events，**causal edges = 0**；relationships ~41 条全 establish（**0 change/end**）；clues v24 32 条 **payoff_chapter = 0**（状态机已修但未重跑生产）；
- 当前 DB `eval_datasets = 0`、`eval_runs = 0`：基础 RAG 质量无当前证据；REQ-EVAL-02 MISSING / REQ-EVAL-03 PARTIAL；
- 同人文/编辑/导出 MISSING（fanfiction 501）；Phase 10 real Playwright residual 未关闭。

## 6. 总体判断

架构方向正确（原文唯一事实源、PostgreSQL 权威、candidate/active 分离、证据闭包），不需要重构、不应加新层。距离核心功能完成的路径：**恢复可信工程基线 → 收口架构契约 → 单书真实数据与质量闭环 → 生产消费切换 → 创作域**。对应规划：ROADMAP.md v1.1–v1.4（Phase 21–34）。
