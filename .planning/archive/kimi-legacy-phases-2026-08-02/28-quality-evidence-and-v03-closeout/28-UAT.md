---
status: partial
phase: 28-quality-evidence-and-v03-closeout
source: 28-01-SUMMARY.md, 28-02-SUMMARY.md, 28-03-SUMMARY.md
started: 2026-07-29T13:06:37+08:00
updated: 2026-07-29T13:33:00+08:00
---

## Current Test

[testing paused — 5 items outstanding]

## Tests

### 1. 查看候选质量证据
expected: 质量报告显示 100/100、0 errors、Recall@5 1.0、quality_comparable=true，且无生产切换。
result: pending

### 2. Reader Chat 桌面端验收
expected: 在正式 PostgreSQL 数据上，Reader Chat 可创建/刷新会话，显示引用与高亮，不泄露剧透，键盘焦点和折叠行为正常。
result: pending

### 3. Reader Chat 390px 移动视口验收
expected: 390px 视口下布局不溢出，聊天、引用、高亮和会话操作仍可用。
result: pending

### 4. Reader Chat 768px 平板视口验收
expected: 768px 视口下响应式布局正常，聊天内容、引用和折叠交互不被裁切。
result: pending

### 5. Structure Workspace owner-scoped 验收
expected: 使用 novel 91 所有者会话打开 `/analysis?novel=91`，Structure Workspace 能显示 Arc/Global 候选结构、引用和诚实降级状态，不出现未授权登录门禁。
result: pending

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps

[none yet]
