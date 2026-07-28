"use client";

import { useEffect } from "react";

import {
  applyReaderTheme,
  loadReaderPreferences,
} from "@/components/reader/reader-preferences";

/**
 * React reconciler for the pre-paint theme bootstrap in layout.tsx.
 * Applies the same validated storage contract, then enables color-only
 * theme transitions after the first paint (never during boot).
 */
export function AppThemeSync() {
  useEffect(() => {
    const preferences = loadReaderPreferences();
    applyReaderTheme(preferences.theme, {
      customBackground: preferences.customBackground,
      enableTransition: false,
    });

    // 「跟随系统」主题：系统明暗切换时重新套用（仅当用户选择 system 才响应）
    let media: MediaQueryList | null = null;
    const onSystemThemeChange = () => {
      const current = loadReaderPreferences();
      if (current.theme !== "system") return;
      applyReaderTheme(current.theme, {
        customBackground: current.customBackground,
        enableTransition: true,
      });
    };
    if (typeof window.matchMedia === "function") {
      media = window.matchMedia("(prefers-color-scheme: dark)");
      media.addEventListener("change", onSystemThemeChange);
    }

    // Gate color transitions only after initial synchronization + one frame.
    const frame = requestAnimationFrame(() => {
      document.documentElement.classList.add("theme-transition-ready");
    });

    return () => {
      cancelAnimationFrame(frame);
      media?.removeEventListener("change", onSystemThemeChange);
    };
  }, []);

  return null;
}
