---
session: 2026-07-26
duration: 完整一天
branch: master @ 52dee50（PR #22 合并后）
method: 主窗口协调 + 并行 agent（独立 worktree）× 4 轮
---

# 2026-07-26 工作汇报

## 交付总览

- **PR 合入**：12 个（#13–#22；#23 在途已修复推送）
- **Phase 完成**：21、22、23、24-03、25-01/02、25-03、25.1
- **Phase 接近完成**：24-01/02（#23 已修复重跑 CI 中）
- **Alembic head**：`25relintake02`（合入中 `24idxjournal1` 待合并）
- **测试总规模**：后端 ~1100 unit/contract + 27 integration；前端 248 vitest + 32/32 Playwright + tsc 0 errors
- **master CI**：自 PR #13 起持续绿（分支保护 `ci-gate` required + enforce_admins）

## 按里程碑的 Phase 状态

| Phase | 名称 | 状态 | PR |
|---|---|---|---|
| 21 | phase21 追认与文档一致性 | ✅ COMPLETE | #14 |
| 22 | CI 恢复绿与门禁生效 | ✅ COMPLETE | #13 |
| 23 | 层级注册表与叙事系统边界 | ✅ COMPLETE | #15（ADR）+ #19（契约测试） |
| 24-01/02 | 索引 journal + reconcile | ⏳ 在途 | #23（CI 进行中） |
| 24-03 | 检索统一 router | ✅ COMPLETE | #20 |
| 25-01 | clue short_title + cost_usd | ✅ COMPLETE | #18 |
| 25-02 | relationship intake provenance | ✅ COMPLETE | #18 |
| 25-03 | API 契约收口 | ✅ COMPLETE | #16 |
| 25.1 | 分析页对话工作台 | ✅ COMPLETE | #17（前端）+ #21（后端）+ #22（接线） |

## 每 PR 详情

### #13 fix(ci): restore green master CI
- 五类根因：Ruff 版本锁定、reconcile 故事序字典序 tie-break 生产 bug、Playwright webServer venv 回退、ci-gate 内嵌 Python 语法、CodeQL 私有仓库上传
- 后续：echarts 5→6.1、npm audit 生产树清零、pip-audit 豁免、分支保护 `ci-gate` required + enforce_admins
- 四轮迭代，先前 master 连续红（2026-07-22 起），合并后首次全绿

### #14 docs(planning): phase 21 recognition + 25.1
- Phase 21 追认目录（CONTEXT/SUMMARY）
- IMPLEMENTATION-STATUS.md 2026-07-26 快照节
- api README + __init__ 反向漂移修正
- codebase/CONCERNS.md 重写、docs/路线图.md 重写

### #15 docs(adr): layer registry + NU/NM boundary
- `docs/adr/0001-layer-registry.md`：S0-S6 + D*/R*/A* 正交命名空间
- `docs/adr/0002-narrative-unit-vs-narrative-memory.md`：消费顺序、NU/NM 边界
- 5 份 Phase 20 历史文档加 superseded 标注
- 与代码逐项核对：发现 6 处事实出入并如实记录

### #16 fix(api): API contract cleanup
- characters 三端点改 410 Gone + successor 指引
- 删除无调用方的 analyze/stream 桩
- fanfiction 三端点标记 deferred（v1.4）
- OpenAPI baseline 重冻 + diff fixtures 重放
- 27 unit + 5 contract 全绿

### #17 feat(analysis): default chat view
- `/analysis` 默认对话视图 + 双视图切换（CSS 隐藏，不卸载）
- 对话面板复用 reader chat 会话底座（同 API/同组件/零新后端）
- 剧透边界同语义、结构区间锚点收窄
- 后续 e2e 视图切换修复（#17 的第二个 commit）

### #18 feat: clue/relationship data honesty
- clue judge `short_title` + 诚实回落
- clue `cost_usd` 真实结算（对齐 timeline/NM gateway 模式）
- relationship `intake_kind` 双迁移（25relintake01→25relintake02，幂等守卫）
- 429 unit + 13 clue + 13 relationship 新增测试
- bug 修复：迁移与新库 ORM 元数据冲突（idempotency guards）

### #19 test(contract): facet read-only projection
- 纯 AST 扫描，4 tests，~1s
- 模型集从 AST 派生（防假绿）、7 类写模式检测、reader_chat 导入禁令
- 真实发现：timeline worker 写 novel.status（UI 字段，非叙事事实），精确豁免 + TODO

### #20 feat(search): retrieval router
- mode 变客户端意图，默认 auto，服务端决策
- units 不可用诚实降级 + resolved_mode/fallback_reason 回传
- citation 规则修复：无证据 unit 双层 fail-closed 过滤
- RETRIEVAL_LAYERS 注册表，NM 预留 disabled
- 250 unit 全绿

### #21 feat(reader-chat): chapter_range anchor
- MessageCreate 可选区间锚点（与 selection/chapter_id 互斥 422）
- 服务端剧透收窄、多章 context manifest（16k 硬上限）
- MessageView.anchor 回显
- 零迁移（锚点存 prompt_inputs JSON）
- 22 新 unit 全绿

### #22 feat(analysis): chapter_range wiring
- 发送原始结构区间，服务端收窄后 anchor 回显
- 消息上方显示"范围：第 X–Y 章"
- 起始章越界预判禁发 + 结构化 422 处理
- 移除单章降级路径
- 248 vitest 全绿

## 在途

### #23 feat(indexing): journal + reconcile
- 24-01：chunk_index_journal 表（迁移 24idxjournal1，幂等守卫）、删除窗口修复、fail-closed novel.status=partial
- 24-02：indexing_reconcile.py、run_index_reconcile.py CLI、manifest checksum 绑定
- 新库全链迁移验证通过、47 unit 全绿、ruff 干净
- CI 进行中

## v1.1 剩余项

| 项 | 状态 |
|---|---|
| 22-03 连续 3 个 nightly 全绿 | 等待中（day 1 已完成，还需 2 天） |
| 24-01/02 索引 journal + reconcile | #23 在途 CI |
| 24-03 Reader Chat 优先级并轨 | 未开始 |
| 25 setState 重构债（analysis/page.tsx） | 未开始 |
| 25 存量 clue 标题重建 | 未开始（随 Phase 27 生产重跑） |
| v1.1 里程碑审计 | 需等 #23 合入后执行 |

## 下一步

1. #23 合入 → v1.1 核心交付收官
2. Reader Chat 优先级并轨（24-03 剩余项）
3. v1.1 里程碑审计（三维度报告）
4. v1.2 启动：Phase 26 NM 整书构建（需你来确认预算上限）