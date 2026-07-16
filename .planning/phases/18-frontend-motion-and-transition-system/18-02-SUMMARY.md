---
phase: 18-frontend-motion-and-transition-system
plan: "02"
subsystem: ui
tags: [dismissable-layer, focus-return, spatial-motion, reader-panels, evidence]

requires:
  - phase: 18-01
    provides: motion tokens, reduced-motion contract, theme boot
provides:
  - useDismissableLayer controlled topmost outside/Escape/presence/focus contract
  - Reader settings/search/chat and evidence panels on spatial motion + shared dismissal
affects:
  - 18-03 Playwright dual-viewport motion qualification

tech-stack:
  added: []
  patterns:
    - "Controlled open is authority; presence only holds exit CSS"
    - "Module stack for topmost nested dismissal"
    - "Backdrop-owned surfaces set closeOnOutside=false; Escape still shared"

key-files:
  created:
    - frontend/src/lib/use-dismissable-layer.ts
    - frontend/src/lib/use-dismissable-layer.test.tsx
  modified:
    - frontend/src/components/app-shell.tsx
    - frontend/src/components/reader/chapter-sidebar.tsx
    - frontend/src/components/reader/reader-preferences.tsx
    - frontend/src/components/reader/reader-preferences.test.tsx
    - frontend/src/components/reader/search-panel.tsx
    - frontend/src/components/reader/reader-chat-panel.tsx
    - frontend/src/components/reader/reader-chat-panel.test.tsx
    - frontend/src/components/relationships/relationship-evidence-panel.tsx
    - frontend/src/components/clues/clue-evidence-panel.tsx

key-decisions:
  - "DISMISSABLE_PRESENCE_MS = 300 matches --motion-duration-spatial"
  - "Opening suppressOutside clears on next rAF (not a timed window)"
  - "Clue NestedConfirmLayer registers above parent so Escape/outside hits confirm first"
  - "Chat ignores [data-reader-chat-toggle]; mobile chip is not dismissable surface"

patterns-established:
  - "layerRef + optional triggerRef + ignoreSelectors"
  - "closing ⇒ pointer-events-none + aria-hidden during exit"
  - "scrollIntoView({ behavior: auto }) under prefers-reduced-motion"

requirements-completed: [UI-MOTION-02, UI-MOTION-03, UI-MOTION-05, UI-MOTION-06]

duration: 55min
completed: 2026-07-16
---

# Phase 18 Plan 02: Dismissable Surfaces Summary

**Shared dismissable-layer stack unifies outside click, Escape, focus return and exit presence for reader/settings/search/chat/evidence without changing business open APIs.**

## Layer Ordering and Focus Protocol

1. Layers register on a module stack only while `open` (not during exit-only presence).
2. Outside pointer and Escape only act on the topmost entry.
3. Opening frame sets `suppressOutside` until the next animation frame (trigger-race safe).
4. Internal clicks and trigger hits never dismiss; `ignoreSelectors` covers chat toggle.
5. After presence unmounts, focus returns to `triggerRef` or the pre-open active element.
6. Closing content is non-interactive (`pointer-events-none`, `aria-hidden`).

## Component Adoption Matrix

| Surface | Outside | Escape | Presence | Motion |
|---|---|---|---|---|
| AppShell nav | n/a | n/a | n/a | standard/fast color feedback |
| ChapterSidebar | mobile backdrop | n/a | transform | spatial; reduced-motion scroll auto |
| ReaderPreferences | shared layer | shared | 300ms | spatial popover |
| SearchPanel | backdrop | shared | 300ms | spatial sheet from right |
| ReaderChatPanel | shared (+ toggle ignore) | shared | 300ms | spatial; mobile bottom-14 clearance |
| Relationship evidence | backdrop | shared | 300ms | spatial right drawer |
| Clue evidence | backdrop | shared | 300ms | spatial right drawer |
| Clue nested confirm | topmost layer | topmost | brief | dismiss before parent |

## Collision Strategy

- Mobile chat shell stays `fixed … bottom-14` (above mobile nav).
- Desktop chat is a reserved column (no permanent text cover).
- Progress bar remains in-flow under the reading column (not stacked above composer).
- Evidence drawers use full-height right panels with independent backdrops.

## Verification Evidence

```
npm test -- --run src/lib/use-dismissable-layer.test.tsx \
  src/components/reader/reader-preferences.test.tsx \
  src/components/reader/progress-bar.test.tsx \
  src/components/reader/reader-chat-panel.test.tsx \
  src/components/clues/clue-workspace.test.tsx \
  src/app/analysis/relationships.test.tsx
→ 6 files, 46 tests passed

npm run lint → 0 errors
npm run build → production build succeeded
```

## Task Commits

1. **Tasks 1–3: dismissable hook + panel adoption** - `2035b54` (feat)

## Residual

- Dual-viewport geometry/touch qualification deferred to 18-03 Playwright matrix.
- Desktop chapter sidebar still uses `lg:hidden` when closed (matches prior business layout; mobile uses transform exit).
