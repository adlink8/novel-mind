# Roadmap: NovelMind

## Active Milestone

v0.1 - 审计与启动修复

## Phases

- [x] Phase 1: 审计与启动修复 - 建立可信基线，修复启动级契约问题，让后续 GSD 自动执行从正确位置开始。

## Phase Details

### Phase 1: 审计与启动修复

**Goal**: 让仓库从“文档声称完成”转为“代码状态可信、基础链路可验证、占位功能不误导”。

**Depends on**: Nothing

**Requirements**: REQ-AUDIT-01, REQ-AUDIT-02, REQ-AUDIT-03, REQ-AUDIT-04, REQ-AUDIT-05

**Success Criteria**:
1. `IMPLEMENTATION-STATUS.md` 反映实际代码状态，并按 VERIFIED / PARTIAL / MISSING 分类。
2. 前端书架和 AI 设置不再因 API 契约错误而基础失败。
3. 未实现 API 明确返回未实现状态或真实空状态，不伪装成功生成结果。
4. 有一组本地命令能验证后端导入链路、前端类型/build 和关键契约。
5. 项目状态文档不再声明未经验证的完成状态。

**Plans**: 3 plans

Plans:
- [x] 01-01: 审计基线与状态校准
- [x] 01-02: 前后端契约启动修复
- [x] 01-03: 验证脚本与文档状态修正

## Progress

| Phase | Plans Complete | Status | Completed |
|---|---:|---|---|
| 1. 审计与启动修复 | 3/3 | Complete | 2026-06-06 |
