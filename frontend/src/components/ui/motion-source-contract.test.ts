import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Phase 18 source contract over every plan-declared files_modified path.
 * Rejects raw arbitrary durations, linear easing, transition-all,
 * unapproved infinite animation, and new animation-runtime imports.
 */

const root = resolve(__dirname, "../..");

const PHASE_18_FILES = [
  // 18-01
  "app/globals.css",
  "app/layout.tsx",
  "app/novels/[id]/page.tsx",
  "components/app-theme-sync.tsx",
  "components/ui/dialog.tsx",
  "components/ui/sheet.tsx",
  "components/ui/dropdown-menu.tsx",
  "components/ui/tooltip.tsx",
  "components/ui/tabs.tsx",
  "components/ui/button.tsx",
  // 18-02
  "lib/use-dismissable-layer.ts",
  "components/app-shell.tsx",
  "components/reader/chapter-sidebar.tsx",
  "components/reader/reader-preferences.tsx",
  "components/reader/reader-chat-panel.tsx",
  "components/reader/search-panel.tsx",
  "components/relationships/relationship-evidence-panel.tsx",
  "components/clues/clue-evidence-panel.tsx",
  // 18-03
  "components/page-header.tsx",
  "components/empty-state.tsx",
  "components/search/search-result-card.tsx",
  "components/reader/progress-bar.tsx",
  "components/timeline/timeline-chart.tsx",
  "components/timeline/timeline-status.tsx",
  "components/relationships/relationship-workspace.tsx",
  "components/clues/clue-workspace.tsx",
  "app/analysis/page.tsx",
] as const;

/** Token definition / documented loading-only exceptions (narrow allowlist). */
const ALLOWLIST_PATTERNS = [
  /--motion-duration-(?:fast|standard|spatial):\s*(?:150|200|300)ms/,
  // Spinner affordances paired with text/ARIA (LoaderCircle animate-spin).
  /animate-spin/,
  // Semantic motion utility class names (not raw durations).
  /motion-duration-(?:fast|standard|spatial)/,
  /motion-ease-(?:enter|exit)/,
  /motion-transition-(?:feedback|content|spatial)/,
] as const;

function stripAllowlisted(source: string): string {
  let next = source;
  // Strip token definitions block lines that legitimately contain 150/200/300ms.
  next = next.replace(
    /--motion-duration-(?:fast|standard|spatial):\s*(?:150|200|300)ms;?/g,
    ""
  );
  // Strip animate-spin usages (loading-only exception).
  next = next.replace(/animate-spin/g, "");
  // Strip semantic motion class tokens.
  next = next.replace(
    /motion-(?:duration-(?:fast|standard|spatial)|ease-(?:enter|exit)|transition-(?:feedback|content|spatial))/g,
    ""
  );
  return next;
}

describe("Phase 18 motion source contract", () => {
  it("covers every declared Phase 18 touched path", () => {
    for (const rel of PHASE_18_FILES) {
      const full = resolve(root, rel);
      expect(existsSync(full), rel).toBe(true);
    }
  });

  it("rejects arbitrary durations, linear easing, transition-all, infinite decorative animation and motion-runtime imports", () => {
    const violations: string[] = [];

    for (const rel of PHASE_18_FILES) {
      const full = resolve(root, rel);
      const raw = readFileSync(full, "utf8");
      const source = stripAllowlisted(raw);

      const checks: Array<{ name: string; pattern: RegExp }> = [
        {
          name: "arbitrary duration class",
          pattern: /duration-(?:75|100|150|200|300|500|700|1000)\b/,
        },
        {
          name: "raw ms duration in class/style",
          pattern: /(?:duration|transition(?:-duration)?)\s*:\s*\d+m?s\b|duration-\[\d+m?s\]/,
        },
        { name: "linear easing", pattern: /\bease-linear\b|timing-function:\s*linear\b/ },
        { name: "transition-all", pattern: /transition-all\b/ },
        {
          name: "unapproved infinite animation",
          pattern: /animate-(?:pulse|bounce)\b|animation:\s*[^;]*infinite/,
        },
        {
          name: "animation runtime import",
          pattern:
            /from\s+["']framer-motion["']|from\s+["']@react-spring|from\s+["']motion["']|require\(["']framer-motion["']\)/,
        },
      ];

      for (const check of checks) {
        if (check.pattern.test(source)) {
          violations.push(`${rel}: ${check.name}`);
        }
      }
    }

    expect(violations, violations.join("\n")).toEqual([]);
  });

  it("keeps semantic token definitions and loading spinner exception documented", () => {
    const css = readFileSync(resolve(root, "app/globals.css"), "utf8");
    expect(css).toMatch(/--motion-duration-fast:\s*150ms/);
    expect(css).toMatch(/--motion-duration-standard:\s*200ms/);
    expect(css).toMatch(/--motion-duration-spatial:\s*300ms/);
    // Allowlist constants must stay intentional.
    expect(ALLOWLIST_PATTERNS.length).toBeGreaterThan(0);
  });
});
