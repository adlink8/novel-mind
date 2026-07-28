import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "../..");

function readSrc(relativePath: string): string {
  return readFileSync(resolve(root, relativePath), "utf8");
}

const MOTION_LAYER = "app/globals.css";

const SHARED_PRIMITIVES = [
  "components/ui/dialog.tsx",
  "components/ui/sheet.tsx",
  "components/ui/dropdown-menu.tsx",
  "components/ui/tooltip.tsx",
  "components/ui/tabs.tsx",
  "components/ui/button.tsx",
] as const;

/** Forbidden patterns in the shared motion layer / touched primitives (Phase 18). */
const FORBIDDEN = [
  { name: "linear easing", pattern: /ease-linear|timing-function:\s*linear\b|ease:\s*linear\b/ },
  { name: "transition-all", pattern: /transition-all\b/ },
  { name: "arbitrary duration class", pattern: /duration-(?:75|100|500|700|1000)\b/ },
  { name: "decorative infinite animation", pattern: /animate-(?:pulse|bounce|spin)\b|animation:\s*[^;]*infinite/ },
] as const;

describe("Phase 18 motion contract", () => {
  it("defines semantic 150/200/300ms tokens and directional enter/exit easing", () => {
    const css = readSrc(MOTION_LAYER);
    expect(css).toMatch(/--motion-duration-fast:\s*150ms/);
    expect(css).toMatch(/--motion-duration-standard:\s*200ms/);
    expect(css).toMatch(/--motion-duration-spatial:\s*300ms/);
    expect(css).toMatch(/--motion-ease-enter:\s*cubic-bezier/);
    expect(css).toMatch(/--motion-ease-exit:\s*cubic-bezier/);
    expect(css).toContain("theme-transition-ready");
    expect(css).toContain("prefers-reduced-motion");
    // Reduced-motion block must appear after theme-transition-ready gate.
    const themeGate = css.indexOf("theme-transition-ready");
    const reduced = css.lastIndexOf("prefers-reduced-motion");
    expect(themeGate).toBeGreaterThan(-1);
    expect(reduced).toBeGreaterThan(themeGate);
  });

  it("exposes explicit-property semantic utilities without transition-all", () => {
    const css = readSrc(MOTION_LAYER);
    expect(css).toContain(".motion-transition-feedback");
    expect(css).toContain(".motion-transition-content");
    expect(css).toContain(".motion-transition-spatial");
    expect(css).toContain("transition-property: opacity, transform");
    expect(css).not.toMatch(/\.motion-[\w-]+\s*\{[^}]*transition:\s*all/);
    expect(css).not.toContain("transition-all");
  });

  it("rejects linear easing, arbitrary durations and decorative infinite animation in the motion layer", () => {
    const css = readSrc(MOTION_LAYER);
    for (const rule of FORBIDDEN) {
      expect(css, rule.name).not.toMatch(rule.pattern);
    }
    // Token definitions themselves must not use linear.
    expect(css).not.toMatch(/--motion-ease-\w+:\s*linear/);
  });

  it("maps shared primitives to semantic motion tokens only", () => {
    for (const file of SHARED_PRIMITIVES) {
      const source = readSrc(file);
      expect(source, file).toMatch(/motion-duration-(?:fast|standard|spatial)/);
      expect(source, file).not.toMatch(/duration-(?:100|150|200|300)\b/);
      expect(source, file).not.toContain("transition-all");
      expect(source, file).not.toMatch(/\bease-linear\b|\bease-in-out\b/);
      // Closed/ending states must not accept pointer input.
      if (file.includes("dialog") || file.includes("sheet") || file.includes("dropdown") || file.includes("tooltip")) {
        expect(source, file).toMatch(/data-\[closed\]:pointer-events-none|data-\[ending-style\]/);
      }
    }
  });

  it("uses enter ease on open and exit ease on closed for overlay primitives", () => {
    for (const file of [
      "components/ui/dialog.tsx",
      "components/ui/dropdown-menu.tsx",
      "components/ui/tooltip.tsx",
    ] as const) {
      const source = readSrc(file);
      // Tailwind v3 runtime: data-attribute variants require bracket syntax.
      expect(source, file).toContain("data-[open]:motion-ease-enter");
      expect(source, file).toContain("data-[closed]:motion-ease-exit");
    }
    const sheet = readSrc("components/ui/sheet.tsx");
    expect(sheet).toContain("motion-duration-spatial");
    expect(sheet).toContain("motion-ease-enter");
  });

  it("keeps tabs/buttons on standard/fast feedback with fixed geometry classes", () => {
    const tabs = readSrc("components/ui/tabs.tsx");
    const button = readSrc("components/ui/button.tsx");
    expect(tabs).toContain("motion-duration-standard");
    expect(button).toContain("motion-duration-fast");
    expect(tabs).not.toContain("transition-all");
    expect(button).not.toContain("transition-all");
    // No width/height animation on these controls.
    expect(tabs).not.toMatch(/transition-\[([^\]]*width|[^\]]*height)/);
    expect(button).not.toMatch(/transition-\[([^\]]*width|[^\]]*height)/);
  });
});
