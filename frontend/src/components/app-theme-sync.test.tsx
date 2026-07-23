import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppThemeSync } from "@/components/app-theme-sync";
import {
  applyReaderTheme,
  DEFAULT_READER_PREFERENCES,
  deriveCustomForeground,
  loadReaderPreferences,
  parseCustomBackground,
  parseReaderTheme,
  READER_PREFERENCES_KEY,
  resolveReaderTheme,
  saveReaderPreferences,
  THEME_BOOT_SCRIPT,
} from "@/components/reader/reader-preferences";

function resetRoot() {
  const root = document.documentElement;
  root.classList.remove("dark", "theme-transition-ready");
  delete root.dataset.readerTheme;
  root.style.colorScheme = "";
  root.style.removeProperty("--reader-custom-background");
  root.style.removeProperty("--reader-custom-foreground");
}

beforeEach(() => {
  window.localStorage.clear();
  resetRoot();
});

afterEach(() => {
  cleanup();
  resetRoot();
  vi.restoreAllMocks();
});

describe("reader theme parsing contract", () => {
  it("accepts only light|dark|custom|system", () => {
    expect(parseReaderTheme("dark")).toBe("dark");
    expect(parseReaderTheme("custom")).toBe("custom");
    expect(parseReaderTheme("light")).toBe("light");
    expect(parseReaderTheme("system")).toBe("system");
    expect(parseReaderTheme("sepia")).toBe("light");
    expect(parseReaderTheme(null)).toBe("light");
  });

  it("resolves system theme via prefers-color-scheme with a safe fallback", () => {
    // jsdom 无 matchMedia → 安全回退 light
    expect(resolveReaderTheme("system")).toBe("light");
    expect(resolveReaderTheme("dark")).toBe("dark");
    expect(resolveReaderTheme("custom")).toBe("custom");
  });

  it("accepts only six-digit hex custom backgrounds", () => {
    expect(parseCustomBackground("#112233")).toBe("#112233");
    expect(parseCustomBackground("#efe4d1")).toBe("#efe4d1");
    expect(parseCustomBackground("red")).toBe(
      DEFAULT_READER_PREFERENCES.customBackground
    );
    expect(parseCustomBackground("#fff")).toBe(
      DEFAULT_READER_PREFERENCES.customBackground
    );
    expect(parseCustomBackground("url(javascript:alert(1))")).toBe(
      DEFAULT_READER_PREFERENCES.customBackground
    );
  });

  it("derives readable foreground for light and dark custom backgrounds", () => {
    expect(deriveCustomForeground("#efe4d1")).toBe("28 20% 13%");
    expect(deriveCustomForeground("#111111")).toBe("42 35% 96%");
  });
});

describe("applyReaderTheme", () => {
  it("applies dark class and color-scheme for dark theme", () => {
    applyReaderTheme("dark");
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.dataset.readerTheme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("applies light theme without dark class", () => {
    applyReaderTheme("dark");
    applyReaderTheme("light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(document.documentElement.dataset.readerTheme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("sets validated custom background CSS variables", () => {
    applyReaderTheme("custom", { customBackground: "#1a2b3c" });
    expect(document.documentElement.dataset.readerTheme).toBe("custom");
    expect(
      document.documentElement.style.getPropertyValue("--reader-custom-background")
    ).toBe("#1a2b3c");
    expect(
      document.documentElement.style.getPropertyValue("--reader-custom-foreground")
    ).toBe(deriveCustomForeground("#1a2b3c"));
  });

  it("falls back safely for invalid custom background", () => {
    applyReaderTheme("custom", {
      customBackground: "expression(alert(1))",
    });
    expect(
      document.documentElement.style.getPropertyValue("--reader-custom-background")
    ).toBe(DEFAULT_READER_PREFERENCES.customBackground);
  });

  it("gates theme-transition-ready only when enableTransition is true", () => {
    applyReaderTheme("dark", { enableTransition: false });
    expect(document.documentElement).not.toHaveClass("theme-transition-ready");
    applyReaderTheme("light", { enableTransition: true });
    expect(document.documentElement).toHaveClass("theme-transition-ready");
  });
});

describe("THEME_BOOT_SCRIPT pre-paint protocol", () => {
  it("embeds the same storage key and never evaluates custom as markup", () => {
    expect(THEME_BOOT_SCRIPT).toContain(READER_PREFERENCES_KEY);
    expect(THEME_BOOT_SCRIPT).toContain("data-reader-theme");
    expect(THEME_BOOT_SCRIPT).toContain("colorScheme");
    expect(THEME_BOOT_SCRIPT).toContain("--reader-custom-background");
    expect(THEME_BOOT_SCRIPT).not.toMatch(/innerHTML|document\.write|eval\(/);
  });

  it("restores dark theme when storage holds dark", () => {
    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify({ theme: "dark" })
    );
    new Function(THEME_BOOT_SCRIPT)();
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.getAttribute("data-reader-theme")).toBe(
      "dark"
    );
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("handles system theme safely when matchMedia is unavailable", () => {
    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify({ theme: "system" })
    );
    expect(() => {
      new Function(THEME_BOOT_SCRIPT)();
    }).not.toThrow();
    expect(document.documentElement.getAttribute("data-reader-theme")).toBe(
      "system"
    );
    // jsdom 无 matchMedia → 回退浅色，不加 dark
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("restores valid custom background variables before React", () => {
    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify({ theme: "custom", customBackground: "#abcdef" })
    );
    new Function(THEME_BOOT_SCRIPT)();
    expect(document.documentElement.getAttribute("data-reader-theme")).toBe(
      "custom"
    );
    expect(
      document.documentElement.style.getPropertyValue("--reader-custom-background")
    ).toBe("#abcdef");
  });

  it("ignores malformed JSON and invalid theme without throwing", () => {
    window.localStorage.setItem(READER_PREFERENCES_KEY, "{not-json");
    expect(() => {
      new Function(THEME_BOOT_SCRIPT)();
    }).not.toThrow();
    expect(document.documentElement).not.toHaveClass("dark");

    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify({ theme: "neon", customBackground: "nope" })
    );
    new Function(THEME_BOOT_SCRIPT)();
    expect(document.documentElement.getAttribute("data-reader-theme")).toBe(
      "light"
    );
  });

  it("survives unavailable localStorage", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => {
      new Function(THEME_BOOT_SCRIPT)();
    }).not.toThrow();
    spy.mockRestore();
  });
});

describe("AppThemeSync reconciliation", () => {
  it("reconciles persisted dark theme and enables transition after rAF", async () => {
    window.localStorage.setItem(
      READER_PREFERENCES_KEY,
      JSON.stringify({
        ...DEFAULT_READER_PREFERENCES,
        theme: "dark",
      })
    );

    let rafCb: FrameRequestCallback | null = null;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCb = cb;
      return 1;
    });

    render(<AppThemeSync />);

    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.dataset.readerTheme).toBe("dark");
    expect(document.documentElement).not.toHaveClass("theme-transition-ready");

    expect(rafCb).not.toBeNull();
    // rafCb 在 mock 闭包内赋值，CFA 仍收窄为 null；先转回声明的联合类型再调用
    (rafCb as FrameRequestCallback | null)?.(0);
    expect(document.documentElement).toHaveClass("theme-transition-ready");
  });

  it("reconciles without changing class when storage empty (default light)", () => {
    render(<AppThemeSync />);
    expect(document.documentElement).not.toHaveClass("dark");
    expect(document.documentElement.dataset.readerTheme).toBe("light");
  });

  it("user save enables transition and applies theme without hydration mismatch tokens", () => {
    saveReaderPreferences({
      ...DEFAULT_READER_PREFERENCES,
      theme: "custom",
      customBackground: "#c0ffee",
    });
    expect(loadReaderPreferences().theme).toBe("custom");
    expect(document.documentElement.dataset.readerTheme).toBe("custom");
    expect(document.documentElement).toHaveClass("theme-transition-ready");
    expect(
      document.documentElement.style.getPropertyValue("--reader-custom-background")
    ).toBe("#c0ffee");
  });
});
