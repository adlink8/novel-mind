---
phase: 18-frontend-motion-and-transition-system
status: researched
stack: [nextjs-16, react-19, tailwindcss-3, tw-animate-css, base-ui, vitest, playwright]
---

# Phase 18 Research

## Recommendation

Implement motion as a small CSS-first design-system layer, then consume it through existing primitives and domain components. The repository already has the required runtime capabilities: Tailwind transitions, `tw-animate-css`, Base UI open/closed state attributes, React controlled state, and Playwright desktop/mobile projects. A new animation library would add bundle size, competing lifecycle semantics, and reduced-motion work without solving a missing capability.

Use three semantic durations and two directional easing tokens:

| Token | Value | Intended use |
|---|---:|---|
| `--motion-duration-fast` | 150ms | hover/focus/press, progress fill |
| `--motion-duration-standard` | 200ms | tabs, cards, content-state fade |
| `--motion-duration-spatial` | 300ms | sidebar, sheet, chat/evidence panels |
| `--motion-ease-enter` | ease-out curve | element enters or expands |
| `--motion-ease-exit` | ease-in curve | element exits or collapses |

The exact cubic-bezier values should be centralized in `globals.css`; components should reference semantic utilities/data states, not copy curves. Transform/opacity are the default animation properties. Theme transitions are a separately gated color-only class because applying a global `transition: all` would animate layout, filters and custom backgrounds.

## Current Repository Facts

- `frontend/src/app/globals.css` already defines light/dark design tokens and a blanket `prefers-reduced-motion` fallback, but no semantic durations/easing or theme-transition boot gate.
- `AppThemeSync` applies persisted reader theme in `useEffect`, so the saved theme can arrive after the first paint. `layout.tsx` already uses `suppressHydrationWarning`, providing a place for a minimal pre-hydration theme boot strategy.
- `dialog.tsx`, `sheet.tsx`, `dropdown-menu.tsx` and `tooltip.tsx` use Base UI or data-state animations, but durations are currently split between 100ms, 150ms and 200ms with no shared enter/exit contract.
- `ChapterSidebar` animates on mobile but changes to `lg:hidden` when closed on desktop; custom reader panels and evidence drawers are conditionally removed immediately, so exit animations cannot complete consistently.
- `ReaderPreferences` and `ReaderChatPanel` each implement their own document `pointerdown` outside-click listener. `SearchPanel` and clue/relationship evidence panels use explicit full-screen backdrops. These paths should share topmost-layer and focus-return semantics without changing their controlled `open` APIs.
- The analysis workspace progressively replaces timeline data and has skeletons in timeline, relationship and clue branches. Timeline cards use hover translation; analysis list/state changes do not yet share a content transition or stable insertion policy.
- The project already runs Vitest and Playwright with `chromium-desktop` 1280×800 and touch-enabled `chromium-mobile-390` 390×844. Existing reader chat, timeline, relationship and clue specs can provide fixtures/helpers.

## Architecture

### 1. CSS-first motion contract

Add motion custom properties and a small set of semantic classes/data-state selectors in `globals.css`. Keep source order explicit:

1. no-transition boot state;
2. regular motion tokens and component transitions;
3. reduced-motion override last.

Avoid `transition-all` in newly touched code. Use explicit properties such as `transition-[opacity,transform,background-color,border-color,box-shadow,color]`. Retain element dimensions while animating state: overlays and panels can use data starting/ending styles; list rows reserve their final footprint before fading in.

### 2. Theme boot without flash

Persisted theme is local client state. A tiny inline boot step in the root layout should read only the established reader preference key, validate `light|dark|custom`, and set the root class plus `color-scheme` before the app paints. `AppThemeSync` remains the React-side reconciler. Add a one-frame/ready-class gate so theme color transitions run only after boot and only for explicit user changes. Invalid storage must fall back safely and never break hydration.

Do not animate the custom background image/value itself. Color transitions should apply to known surfaces/text/borders; the root background must remain stable. Test boot behavior with a fresh document and persisted dark/custom values.

### 3. Dismissable surface contract

Prefer the existing Base UI primitives for modal dialog/sheet behavior. For bespoke panels that must remain in their current DOM/layout, centralize a lightweight controlled dismissable-layer hook/component with:

- pointerdown outside using composed path/ref containment;
- Escape dismissal;
- topmost layer ordering so nested confirmations win;
- trigger registration and focus restoration;
- opt-out for modal/backdrop-owned surfaces;
- protection against the opening pointer event immediately closing the panel.

Do not infer dismissal from arbitrary global selectors. Keep controlled `open`/`onOpenChange` contracts and existing data fetching unchanged. On close, hold a short present/closing state long enough for the CSS exit, but mark closing content non-interactive and hidden from assistive technology as appropriate.

### 4. Page/content-state transitions

Do not intercept App Router navigation. Apply content transitions to stable regions:

- selected navigation indicator and page header/action feedback;
- analysis workspace tab body and selected evidence detail;
- loading/empty/error/ready swaps;
- progressive analysis list insertions and progress bar value changes;
- card hover/focus/selected states.

Use stable keys and reserve space. New list items may fade/translate by a few pixels once; existing items must not replay animation on each 2.5s poll. Charts from ECharts/Cytoscape retain their own rendering behavior; Phase 18 should animate their containing state, not run a second competing graph animation.

## Accessibility and Input Guidance

- `prefers-reduced-motion` replaces spatial movement/zoom/pulse with immediate state or a near-instant opacity change. Smooth scrolling, including directory `scrollIntoView`, must use `auto` under reduced motion.
- Focus-visible remains visible in both themes and is never delayed until animation completion.
- Triggers expose `aria-expanded`/`aria-controls`; panels have meaningful labels; loading has `aria-busy` or a status label independent of spinner movement.
- Outside click is additive to Escape and explicit close, not a replacement. Modal focus trapping stays with Base UI.
- Touch targets remain at least the existing component sizes; hover transforms are accompanied by focus/pressed color/border feedback and disabled on coarse/reduced-motion contexts when they add no value.

## Performance and Layout Stability

- Do not animate layout dimensions or apply large blurred layers to continuously moving elements.
- Restrict compositing hints (`will-change`) to an actively opening/closing panel and remove them at rest; permanent `will-change` wastes memory.
- Reserve skeleton/card/chart height and message composer space. Reader progress and chat panel must occupy separate fixed regions with verified bottom insets.
- Theme boot code must be synchronous, tiny, defensive and dependency-free. No network or React render is allowed before root theme selection.
- Progressive analysis data should only animate newly inserted identities, not every item whenever polling produces a new envelope object.

## Verification Strategy

### Static/unit

- Contract test or source scan confirms only semantic motion tokens are introduced in touched components and no linear/continuous decorative animations appear.
- Vitest covers persisted theme before React reconciliation, invalid storage fallback, reduced-motion behavior, outside click, Escape, nested/topmost dismissal, internal click, trigger toggle and focus return.
- Component tests cover `aria-expanded`, `aria-busy`, stable DOM footprint and closing-state non-interactivity.

### Browser

Create one focused `e2e/motion-and-transitions.spec.ts`, reusing existing login/fixture helpers. Run both configured projects and test:

- dark/light/custom first visible state and explicit switching;
- AppShell/navigation state without horizontal overflow;
- reader settings/chat/search/sidebar outside-click and Escape behavior;
- chat input remains above progress UI at desktop and 390px;
- relationship/clue evidence panel and nested confirmation topmost dismissal;
- analysis progress/list additions preserve container geometry and do not replay existing rows;
- reduced-motion emulation removes transform/animation while controls remain usable.

Use computed style and bounding boxes for deterministic assertions. Screenshots are supporting evidence, not the only oracle.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Conditional render prevents exit animation | Introduce a scoped presence state only for target panels; keep business `open` state authoritative. |
| Global outside listener closes a newly opened or nested panel | Track the opening event/topmost layer and test nested confirmations. |
| Theme transition flashes on hydration | Apply root theme pre-paint and enable transitions only after boot. |
| `transition-all` animates dimensions and causes CLS | Replace only in touched surfaces with explicit properties and geometry assertions. |
| Polling replays every card animation | Track stable item identity and animate insertion once. |
| Reduced-motion hides loading feedback | Pair every spinner/pulse with text/ARIA state and disable only non-essential movement. |
| Mobile bottom overlays collide | Assert real bounding boxes for composer, progress and mobile nav at 390×844. |

## Planning Conclusion

The phase should execute in three waves: establish tokens/primitives/theme boot; unify application and reader surfaces/dismissal; then apply state feedback to analysis/content and qualify in Playwright. This isolates foundational risk before broad component adoption and keeps all changes frontend-only.
