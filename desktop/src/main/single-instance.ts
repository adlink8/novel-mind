/**
 * Single-instance enforcement (D-45-02, T-45-01-02).
 *
 * Acquires the Electron `requestSingleInstanceLock` BEFORE any runtime graph or
 * window exists. The lock is scoped to the app's userData directory (and app
 * name), so a second launch with the same `%APPDATA%/NovelMind` root is a
 * duplicate: it routes its command-line/URL intent to the existing window and
 * exits immediately — no second runtime graph is ever started (T-45-01-02).
 *
 * The pure focus/restore decision is exported as `focusMainWindow` so the
 * process-behavior suite can unit-test it without an Electron instance.
 */
import { app } from "electron";

export interface SingleInstanceWindow {
  isDestroyed(): boolean;
  isMinimized(): boolean;
  restore(): void;
  focus(): void;
}

export interface SingleInstanceOptions {
  /** Provides the current main window; may be null before the first window. */
  getMainWindow: () => SingleInstanceWindow | null;
  /**
   * Optional second-launch intent hook. Receives the argv and working directory
   * of the duplicate launch; the default behavior already focuses/restores the
   * existing window.
   */
  onSecondInstance?: (argv: string[], workingDirectory: string) => void;
}

export interface SingleInstanceResult {
  /**
   * True when THIS process holds the lock and must continue startup; false when
   * a duplicate instance already owns the lock (caller must exit).
   */
  isPrimary: boolean;
}

/**
 * Focus (and restore, if minimized) an existing main window, ignoring null and
 * destroyed windows. Pure — unit-testable with a fake window.
 */
export function focusMainWindow(win: SingleInstanceWindow | null): boolean {
  if (win === null || win.isDestroyed()) return false;
  if (win.isMinimized()) win.restore();
  win.focus();
  return true;
}

/**
 * Call ONCE at the very top of the main process, before any runtime or window
 * work. When this process is the duplicate, the caller should exit immediately
 * (index.ts calls `app.exit(0)`).
 */
export function enforceSingleInstance(options: SingleInstanceOptions): SingleInstanceResult {
  const gotLock = app.requestSingleInstanceLock();
  if (!gotLock) {
    return { isPrimary: false };
  }
  app.on("second-instance", (_event, argv, workingDirectory) => {
    options.onSecondInstance?.(argv, workingDirectory);
    focusMainWindow(options.getMainWindow());
  });
  return { isPrimary: true };
}
