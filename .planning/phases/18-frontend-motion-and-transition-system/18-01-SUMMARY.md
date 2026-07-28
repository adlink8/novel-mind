---
phase: 18-frontend-motion-and-transition-system
plan: "01"
subsystem: ui
tags: [motion, css-tokens, theme-boot, reduced-motion, base-ui, vitest, nextjs]

requires: []
provides:
  - Semantic motion tokens 150/200/300ms with enter ease-out and exit ease-in
  - Pre-paint theme bootstrap + AppThemeSync reconciler (no FOUC)
  - Shared primitive duration/easing/reduced-motion contract tests
affects:
  - 18-02 dismissable panels and spatial surfaces
  - 18-03 content feedback and Playwright qualification

tech-stack:
  added: []
  patterns:
    - "CSS-first motion contract in globals.css; no animation runtime deps"
    - "theme-transition-ready gate after first paint; color-only transitions"
    - "Semantic utilities motion-duration-*/motion-ease-*/motion-transition-*"

key-files:
  created:
    - frontend/src/components/app-theme-sync.tsx
    - frontend/src/components/app-theme-sync.test.tsx
    - frontend/src/components/ui/motion-contract.test.tsx
    - frontend/src/components/reader/reader-preferences.tsx
    - frontend/src/components/reader/reader-preferences.test.tsx
  modified:
    - frontend/src/app/globals.css
    - frontend/src/app/layout.tsx
    - frontend/src/app/novels/[id]/page.tsx
    - frontend/src/components/ui/dialog.tsx
    - frontend/src/components/ui/sheet.tsx
    - frontend/src/components/ui/dropdown-menu.tsx
    - frontend/src/components/ui/tooltip.tsx
    - frontend/src/components/ui/tabs.tsx
    - frontend/src/components/ui/button.tsx
    - frontend/tailwind.config.ts

key-decisions:
  - "Three duration tokens only: fast 150ms, standard 200ms, spatial 300ms"
  - "Enter cubic-bezier(0,0,0.2,1); exit cubic-bezier(0.4,0,1,1)"
  - "THEME_BOOT_SCRIPT shared string inlined in layout; never evaluates custom as markup"
  - "Custom background: six-digit hex only → --reader-custom-background + derived foreground"
  - "Reader surface uses data-reader-surface CSS vars (no post-hydration style flash)"

patterns-established:
  - "Primitives use motion-duration-* + motion-ease-enter/exit; data-closed:pointer-events-none"
  - "No transition-all in touched primitives; explicit property lists only"
  - "prefers-reduced-motion block is last authority (0.01ms, no transform, auto scroll)"

requirements-completed: [UI-MOTION-01, UI-MOTION-04, UI-MOTION-05]

duration: 45min
completed: 2026-07-16
---

# Phase 18 Plan 01: Motion Foundation and Theme Boot Summary

**CSS-first motion contract (150/200/300ms) plus pre-paint theme bootstrap eliminate FOUC and unify shared Base UI primitive transitions without new runtime dependencies.**

## Performance

- **Duration:** ~45 min
- **Tasks:** 3 (human-action authorized; tokens; theme boot; primitives)
- **Files modified:** 15

## Token Values

| Token | Value | Use |
|---|---:|---|
| `--motion-duration-fast` | 150ms | hover/focus/press, dialog/dropdown/tooltip |
| `--motion-duration-standard` | 200ms | tabs, content state |
| `--motion-duration-spatial` | 300ms | sheet/sidebar panels |
| `--motion-ease-enter` | `cubic-bezier(0, 0, 0.2, 1)` | open/expand |
| `--motion-ease-exit` | `cubic-bezier(0.4, 0, 1, 1)` | close/collapse |

Semantic utilities: `.motion-duration-*`, `.motion-ease-enter|exit`, `.motion-transition-feedback|content|spatial`.

## Theme Boot Protocol

1. Inline `THEME_BOOT_SCRIPT` in root layout `<head>` runs before first paint.
2. Reads `novelmind:reader-preferences:v1`; accepts only `light|dark|custom`.
3. Sets `html.dark`, `data-reader-theme`, `color-scheme`, and for custom: validated `#RRGGBB` → `--reader-custom-background` / `--reader-custom-foreground`.
4. Invalid JSON / storage / hex → safe defaults; no throw; no markup evaluation.
5. `AppThemeSync` reconciles on mount with `enableTransition: false`, then adds `theme-transition-ready` after rAF (color-only transitions).
6. Reader scroll surface uses `data-reader-surface` to consume pre-paint CSS vars.

## Primitive Mapping

| Primitive | Duration | Easing | Notes |
|---|---|---|---|
| dialog overlay/content | fast | enter open / exit closed | opacity+scale; closed pointer-events none |
| sheet overlay/content | spatial | enter (+ exit on ending) | side-aware transform; explicit opacity/transform |
| dropdown / tooltip | fast | enter / exit | closed pointer-events none |
| tabs trigger | standard | enter | color/bg/border/shadow only |
| button | fast | enter | color/bg/border/shadow/opacity/transform; fixed geometry |

## Reduced-Motion Behavior

- Final `@media (prefers-reduced-motion: reduce)` block sets animation/transition to 0.01ms, iteration 1, delay 0, `scroll-behavior: auto`.
- Motion utilities force `transform: none`.
- Theme color transitions also collapsed under reduced motion.
- Visible state changes remain immediate and operable.

## Verification Evidence

```
npm test -- --run src/components/ui/motion-contract.test.tsx \
  src/components/app-theme-sync.test.tsx \
  src/components/reader/reader-preferences.test.tsx
→ 3 files, 26 tests passed

npm run lint → 0 errors
npm run build → Next.js production build succeeded
```

## Task Commits

1. **Tasks 1–3: motion tokens + theme boot + primitive normalize** - `f6280cf` (feat)

## Residual / Notes

- Unrelated WIP outside staged paths was left untouched.
- `reader-preferences.tsx` was previously untracked WIP; committed as the canonical theme preference module for Phase 18 boot.
- Domain panels (chat/settings/evidence) deferred to 18-02.
