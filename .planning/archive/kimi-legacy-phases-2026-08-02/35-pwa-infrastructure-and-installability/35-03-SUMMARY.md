---
phase: 35-pwa-infrastructure-and-installability
plan: 03
subsystem: infra
tags: [offline, service-worker, fallback, smoke-test]
requires:
  - phase: 35-pwa-infrastructure-and-installability
    provides: "生产 Service Worker 与预缓存策略"
provides:
  - "离线导航 fallback 页面"
  - "生产 manifest、SW、offline.html HTTP 冒烟证据"
affects: [36-offline-reading, 39-pwa-verification]
tech-stack:
  added: []
  patterns: ["导航失败回退本地 offline.html"]
key-files:
  created: [frontend/public/offline.html]
  modified: []
key-decisions:
  - "使用内联品牌样式的静态离线页，确保回退本身不依赖 CSS/JS 网络请求。"
requirements-completed: [REQ-MOBILE-01]
duration: 15min
completed: 2026-07-29
---

# Phase 35-03 Summary

**生产 PWA 已具备离线导航回退外壳，并通过本地生产服务器的关键资源冒烟。**

## Accomplishments

- 新增品牌化 `offline.html`，包含离线说明、缓存内容提示和重新加载按钮。
- Serwist fallback 仅在导航策略失败且预缓存未命中时返回该页面。
- 生产服务器 `3010` 实测 `/manifest.webmanifest`、`/sw.js`、`/offline.html` 均返回 200，关键内容校验通过。

## Task Commit

- `7399829` — `feat(phase35): add offline fallback shell`

## Verification

- 生产 HTTP 冒烟：`PWA_HTTP_SMOKE_PASS`。
- Lighthouse、DevTools Application 面板、Playwright 离线导航和真机安装仍需人工/浏览器验收，记录在 `35-HUMAN-UAT.md`。
