# 34-04 SUMMARY — Markdown/HTML/EPUB Export and UAT

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 34 override)

## What Was Built

1. **`backend/app/services/export/manifest.py`** — `ExportManifestService.freeze` +
   `NovelExportManifest`/`FrozenExport` 契约（含 cutoff_chapter spoiler 来源）。
2. **`backend/app/services/export/markdown.py`**、`html.py`、`epub.py` — 三格式适配器，仅
   消费同一 frozen manifest；EPUB3 固定 stdlib zip/XML 布局，EPUB 章节复用
   `html.render_chapter_xhtml` 实现字节级 parity。
3. **`backend/app/api/export.py`** — `GET /{id}/export/manifest` + `GET /{id}/export?format=…`
   （`X-Export-Manifest-Hash`）。
4. **`backend/app/main.py`**（修改）— 注册 export_router。
5. **前端**：`export.ts` + `export.test.tsx`（4p）、`e2e/export.spec.ts`（环境受限）。
6. **测试**：`test_parity.py` 9 + `test_adapters.py` 15。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/export tests/integration/export -q` | ✅ **24 passed** |
| `pytest tests/integration/export/test_parity.py -q`（两次） | ✅ 9 passed / 9 passed |
| `pytest tests/unit/illustration_anchors tests/integration/illustration_anchors -q` | ✅ **91 passed**（回归） |
| `pytest tests/unit -q`（全量） | ✅ **1002 passed** |
| `npx vitest run export` | ✅ **4 passed** |
| `npx vitest run`（全量） | ✅ **386 passed / 44 files** |
| `from app.main import app` | ✅ OK |

## 关键设计

- manifest 可重放、排序确定、approved-only、hash/owner/version scope；
- HTML/EPUB3 同 frozen manifest 字节级 parity；EPUB3 ZIP/XML/resources 固定布局；
- 缺失资产显式报告；graceful missing asset。

## 备注 / 偏差

- 为满足 PLAN verify 的 `tests/unit/export` 路径，额外创建 `tests/unit/export/test_adapters.py`
  （纯 DB-free 单测）。
- 前端 `export.ts` 最小辅助（URL/文件名/状态标签契约）为必要支撑。
- 全量 `pytest tests` 存在既有环境问题（重复 basename 收集冲突、openapi 导出子进程挂起、
  test_agent_tools facade KeyError——33-05/34-05 新工具未入静态字典），非本切片引入。
