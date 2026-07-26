# Phase 21 SUMMARY — phase21 追认与文档一致性恢复

- status: COMPLETE
- date: 2026-07-26
- snapshot: master（PR #13 `9f01680` 之后的文档收口 PR）

## 21-01 追认与状态刷新

- 建立本目录（CONTEXT/SUMMARY）作为 phase21 分支工作的 GSD 追认记录（清单见 21-CONTEXT.md）。
- `IMPLEMENTATION-STATUS.md`：新增 2026-07-26 快照节（Alembic head `18appsetting1`、
  后端 1085 passed / 前端 236、AI 路由与用量条目、CI 恢复与分支保护事实）。
- `ROADMAP.md`/`STATE.md`：Phase 21 标记完成；Phase 22 状态更新（22-01/22-02 done，
  22-03 余 3-nightly 观察）。

## 21-02 漂移修正与入口文档重写

- `backend/app/api/README.md` + `__init__.py`：修正反向漂移（timeline/analysis 已实现；
  characters 标废弃双轨待 Phase 25；fanfiction 标 deferred 归 v1.4）。
- `.planning/codebase/CONCERNS.md`：重写为当前有效关切，指向
  `AUDIT-STATUS-REFRESH-2026-07-26.md` 为权威问题清单。
- `docs/路线图.md`：重写为指向 `.planning/ROADMAP.md` 的当前路线摘要，旧内容标记 superseded。
- 快照标识规范写入 21-CONTEXT.md 并在本次更新的文档执行。

## Verification

- 文档断言均带 git 证据（commit hash）或本地命令复核结果。
- 本 PR 经 ci-gate 全绿合入（分支保护 required check 生效后的常规路径）。

## 遗留

- Vertex/Gemini 适配仍为无测试实验态（补测试归 Phase 25 或使用方 Phase）。
- 75MB dump 的 git 历史清除需显式授权（默认接受现状）。
