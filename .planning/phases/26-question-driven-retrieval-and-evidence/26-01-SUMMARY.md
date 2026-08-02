# 26-01 SUMMARY — QueryPlan 契约与确定性问题解析

**Status:** COMPLETE | **Date:** 2026-08-02 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, recorded in `.planning/STATE.md` + `config.json`)

## What Was Built

1. **`backend/app/services/queryplan/`** — QueryPlan 契约包：
   - `schemas.py`：严格 Pydantic 契约——intent、owner_id、novel_id、version_id、
     spoiler_cutoff、dimensions、fallback、answer_constraints、anchor、trace、availability，
     另有 EvidenceRef 与 BlockedResult；
   - `parser.py`：确定性 fail-closed 解析器（纯函数），拒绝未知/歧义 intent、越界 scope、
     future probing、矛盾约束；owner/novel/version/cutoff 必填，默认 reading-progress
     cutoff，whole-book 仅接受显式开关；校验失败只返回稳定 blocked/clarification reason，
     不创建 trace、不写数据库；
   - `repository.py`：owner/version-scoped append + 幂等 replay。
2. **`backend/app/models/queryplan.py`** — immutable durable `QueryPlanTrace`：
   canonical payload、trace id/idempotency key、owner/novel/version/cutoff、schema/parser
   version、source/dataset lineage、availability/fallback、created-at、blocked reason。
3. **Migration `20260801_2601_query_plan_trace.py`**：revision=`20260801_2601`、
   down_revision=`27approval01`（唯一单 head），upgrade/downgrade 成对可逆，仅新增
   `query_plan_traces` 表，不改既有 chat 表。
4. **Fixtures**：`backend/tests/fixtures/queryplan/questions_v1.json` — 16 个冻结 case。

## 验收

| 项 | 结果 |
|---|---|
| `cd backend && venv/Scripts/python.exe -m pytest tests/unit/queryplan -q` | ✅ **52 passed**（contracts 35 + parser 17） |
| `cd backend && venv/Scripts/python.exe -m pytest tests/integration/queryplan/test_trace_replay.py -q` | ✅ **9 passed**（restart replay 幂等） |
| `alembic heads` | ✅ `20260801_2601 (head)` 单 head |
| upgrade/downgrade 可逆 | ✅ SQLite 实测 + 真实 PG 全链矩阵 |
| `alembic check`（真实 PG） | ✅ 零 drift |
| 回归 | ✅ narrative_memory/reader_chat unit 237 passed；PG 迁移矩阵 21 passed |

## 设计决策（D-01..D-14 授权范围内）

- whole-book 信号集去除"主线/主题"等歧义词（有显式 chapter_range 锚点的分析问题不误判）；
- 中文数字章节（第十章）纳入 future-probing 检测；
- character_state/world_rules 在 plan 级声明 `unavailable`（Phase 27 前无生产 reader，D-05 非空成功）。

## 备注 / 偏离

- PLAN files_modified 未列的 5 个既有迁移测试文件被修改（head 引用 `27approval01` →
  `20260801_2601`）——head 推进的必然连带修复。
- `tests/integration/reader_chat/test_conversations_api.py` 挂起：既有环境问题（沙箱内
  后台 worker 调外部 AI provider 不可达），与本次改动无关。
