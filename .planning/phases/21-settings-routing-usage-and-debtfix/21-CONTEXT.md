# Phase 21: phase21 追认与文档一致性恢复 — CONTEXT

- snapshot: master @ 9f01680（PR #13 合并后）
- date: 2026-07-26
- 性质: **追认阶段**。2026-07-18~07-23 的 "phase21" 分支工作（PR #11/#12）未走 GSD 流程，
  本阶段不重做代码，只把已交付事实纳入规划权威并消除文档漂移。

## 被追认的交付（git 证据）

| 交付 | 证据 |
|---|---|
| 设置中心组件化重构（settings page 633→~30 行；account/routing/models/usage section） | `10632b1` |
| `GET/PUT /api/settings/routing` + `GET /api/usage/summary` + `app_settings` 表 | `f4fbf06`；迁移 `backend/migrations/versions/18_app_settings.py`，Alembic head `18appsetting1` |
| 阅读器体验（system 主题、fontSize/lineHeight/contentWidth、章内进度恢复、immersive chrome、断点 1280） | `10632b1` |
| `/writing` 诚实占位页（3D 翻书 + Planned 印章，不放假功能） | `10632b1` |
| Timeline 服务重写（worker/model_gateway/promotion/query/reconcile/jobs/extraction/evidence/budget/overrides） | `9d0222a` |
| Reader Chat 服务重写（context/gateway/worker/budget/conversations/retrieval） | `9d0222a` |
| Vertex/Gemini 实验适配（gcloud token、代理、schema 收敛、thinkingBudget=0；无测试，`feat(exp)`） | `02f06d5`、`vertex_gemini.py` |
| Clue payoff 状态机修复 + 审计脚本 schema 对齐 | `2cf8562`（关闭 NM-DATA-006/NM-ENG-006） |
| CI e2e job 补 docker compose + alembic + DATABASE_URL 注入 | `0aa4c21` |
| 75MB DB dump 停止跟踪（仍在已推送历史中，彻底清除需历史重写，未授权） | `0b5ba30` |

验证基线（PR #12 声明 + 2026-07-26 本地复核）：后端 1085 passed / 189 skipped；前端 vitest 236；`next build` 通过。

## 本阶段消除的漂移

1. `IMPLEMENTATION-STATUS.md`：Alembic head 误记 `518675fa18f8`（实际 `18appsetting1`）、测试计数误记 239（实际 1085）、AI 路由条目未更新。
2. `backend/app/api/README.md` / `__init__.py`：把已实现的 timeline/analysis 仍标 501（反向漂移）。
3. `.planning/codebase/CONCERNS.md`：停留在 2026-07-05，声称 Narrative Unit 未实现等，已被 Phase 05–20 推翻。
4. `docs/路线图.md`：停留在 v0.3 时代的 Phase 3–7 规划，与实际实现路线（ECharts/Cytoscape、Phase 08/09）不符。

## 快照标识规范（NM-ENG-008 收口）

自本阶段起，状态类文档头部须含：`snapshot: <branch> @ <commit>`、`date:`、以及数据类断言的
DB 来源说明（fingerprint 或"未连接 DB"）。
