# 34-02 SUMMARY — Responsive Reader Illustration Presentation

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 34 override)

## What Was Built

1. **`frontend/src/lib/illustration-anchor.ts`** — AnchorView/AnchorStatus 镜像 + owner-scoped
   API + `verifyAnchorAgainstChapter`（读侧哈希/区间重验证）+ `illustrationAssetBytesUrl`。
2. **`frontend/src/components/reader/illustration-block.tsx`** — flow-layout `<figure>`，
   hash-verified 已批准资产、accessible caption/alt、IntersectionObserver lazy 加载、
   missing/stale/invalid 显式占位。
3. **`frontend/src/components/reader/reader-content.tsx`**（修改）— `anchors` prop；
   `buildPageBlocks` 按精确 source offset 在段落后插入插图（不从 DOM index 推断）；
   highlight 重构到段落级；选区映射保持精确。
4. **`frontend/src/lib/reader-selection.ts`**（修改）— `rangeToChapterUtf16` 跳过
   `[data-reader-illustration]` 子树（选区坐标不被插图 caption 污染）。
5. **`frontend/src/app/novels/[id]/page.tsx`**（修改）— 拉取 published anchors 传入
   ReaderContent（失败降级为无 anchors）。
6. **`frontend/e2e/illustration-anchors.spec.ts`** — route-mock 桌面/390px 浏览器证据（12
   tests，环境受限）。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `npx vitest run illustration` | ✅ **34 passed**（illustration 15 + illustrations 19） |
| `npx vitest run`（全量） | ✅ **382 passed / 43 files** |
| `npx tsc --noEmit` | ✅ 本阶段 7 文件 0 错误（全量 39 错误均为既有遗留） |
| `npx playwright test --list e2e/illustration-anchors.spec.ts` | ✅ **12 tests**（desktop/mobile-390/tablet-768） |
| `from app.main import app` | ✅ OK |

## 关键设计

- hash 验证 fail-closed（绝不把 stale anchor 当 valid 渲染）；显式 missing/stale/invalid
  占位；
- caption/alt 无 dangerouslySetInnerHTML；flow layout 不遮挡 progress/nav；paged/scroll/
  长页均覆盖；owner-scoped 只读已批准 anchors；
- spoiler cutoff 由后端 owner-scoped anchors API 服务端收窄，前端不自行推导。

## 备注 / 偏差

- 新增 reader-selection.ts、page.tsx 两个非 PLAN 文件改动（选区坐标保持 + 页面接线——
  reader 集成的必需前置）。
- e2e 断言未执行（Next canary 编译失败环境限制）；spec 保留为可执行证据。
- 无新 migration（schema 由 34-01/05 提供）。
