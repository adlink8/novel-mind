---
phase: 35-pwa-infrastructure-and-installability
plan: 02
subsystem: infra
tags: [serwist, service-worker, cache-first, network-first, next-webpack]
requires:
  - phase: 35-pwa-infrastructure-and-installability
    provides: "manifest 与本地图标"
provides:
  - "生产环境 Serwist Service Worker 构建与自动注册"
  - "app-shell/static cache-first 与 API network-first 缓存策略"
affects: [35-03, 36-offline-reading, 39-pwa-verification]
tech-stack:
  added: ["@serwist/next@9.5.12", "serwist@9.5.12", "@testing-library/dom"]
  patterns: ["开发环境禁用 SW，生产 webpack 构建注入 SW"]
key-files:
  created: [frontend/src/app/sw.ts, frontend/src/components/eval-quality-panels.tsx]
  modified: [.gitignore, frontend/next.config.mjs, frontend/package.json, frontend/package-lock.json, frontend/src/lib/reader-selection.ts, frontend/src/app/eval/page.tsx, frontend/src/app/eval/page.test.tsx]
key-decisions:
  - "显式预缓存图标与离线页并计算内容 hash，避免 Windows glob 产生反斜杠 URL。"
  - "Next 16 canary 使用 webpack 构建 Serwist；客户端 Web Crypto 优先，Node fallback 改为 webpack 可解析入口。"
requirements-completed: [REQ-MOBILE-01]
duration: 45min
completed: 2026-07-29
---

# Phase 35-02 Summary

**生产构建已集成 Serwist，app-shell 静态资源可缓存，API 请求按 network-first 回退缓存。**

## Accomplishments

- 新增 `sw.ts`：`novelmind-shell-v1` 使用 CacheFirst/导航 NetworkFirst，`novelmind-api-v1` 使用约 3 秒 NetworkFirst。
- 开启 `skipWaiting`、`clientsClaim`、缓存条目/时效上限；非 GET 请求未注册缓存路由。
- `@serwist/next` 仅生产注入与注册，开发环境禁用，且生成的 `public/sw.js` 不纳入源码提交。
- 为 Next 16 webpack 生产构建补齐兼容处理，并将评测页面测试组件移出 App Router page 导出边界。

## Task Commit

- `65f3d1a` — `feat(phase35): integrate Serwist shell caching`

## Verification

- `npm run build` 成功，生成 `frontend/public/sw.js`。
- 生成物校验确认含 `novelmind-shell-v1`、`novelmind-api-v1`、`skipWaiting`、`clientsClaim`，且图标 URL 使用正斜杠并带 revision。
- `npm run test`：30 个测试文件、251 个测试全部通过。
- `npm run lint`：0 error，保留 3 个既有 warning。
