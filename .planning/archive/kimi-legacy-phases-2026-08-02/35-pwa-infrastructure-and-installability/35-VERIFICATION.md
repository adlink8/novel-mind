---
phase: 35-pwa-infrastructure-and-installability
status: human_needed
verified: 2026-07-29
verifier: codex
requirements: [REQ-MOBILE-01]
---

# Phase 35 Verification

## Automated checks

| Check | Result | Evidence |
|---|---|---|
| Production build | PASS | `frontend: npm run build`，Next 16 webpack + Serwist 构建成功 |
| Manifest route | PASS | `http://127.0.0.1:3010/manifest.webmanifest` → 200，含 standalone |
| Service Worker | PASS | `http://127.0.0.1:3010/sw.js` → 200，含 shell/API cache、skipWaiting、clientsClaim |
| Offline fallback | PASS | `http://127.0.0.1:3010/offline.html` → 200，品牌离线文案存在 |
| Vitest | PASS | 30 files / 251 tests passed |
| ESLint | PASS_WITH_WARNINGS | 0 errors；3 个既有 warnings |
| Backend scope | PASS | 本次改动 `git status -- backend` 无输出 |

## Must-have assessment

- Manifest、图标集、standalone、iOS 元数据：代码已满足。
- 生产 SW 注册、app-shell/API 缓存策略、非 GET 不缓存：代码与生成物已满足。
- 导航失败回退 `offline.html`：代码与 HTTP 资源已满足。
- Lighthouse installability、DevTools Cache Storage/offline refresh、真机添加到主屏幕、Playwright offline：尚未完成，需要人工浏览器验收。

## Gaps / human verification

Phase 35 不标记为 fully passed，直到 `35-HUMAN-UAT.md` 中的浏览器/真机项目完成。当前未发现代码级 gap；未完成项属于需要真实浏览器控制面的验收证据。
