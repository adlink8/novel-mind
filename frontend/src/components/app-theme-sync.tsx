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

    // Gate color transitions only after initial synchronization + one frame.
    const frame = requestAnimationFrame(() => {
      document.documentElement.classList.add("theme-transition-ready");
    });

    return () => cancelAnimationFrame(frame);
  }, []);

  return null;
}
