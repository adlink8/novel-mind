/**
 * Uninstall / data-removal policy (Phase 45, plan 45-02, D-45-05, T-45-02-02).
 *
 * The DEFAULT uninstall removes application binaries only and preserves the
 * whole `%APPDATA%/NovelMind` tree (electron-builder `deleteAppDataOnUninstall:
 * false`, desktop/electron-builder.yml). Deleting user data is a SEPARATE,
 * explicitly labelled action that:
 *
 *   - is never part of the default uninstall path (`defaultUninstallScope`),
 *   - requires explicit confirmation (`confirm: true`),
 *   - resolves the requested target with `containPath` so it cannot escape the
 *     resolved app-data root — traversal, absolute segments and outside-root
 *     paths are refused (T-45-02-02),
 *   - reports a typed refusal with a recovery instruction when it cannot run.
 *
 * The module is Electron-free and unit-testable; the caller (installer/UI)
 * supplies the resolved app-data paths and the injected `DataFs`.
 */
import {
  AppDataLayoutError,
  APP_DATA_LAYOUT_ERROR_CODES,
  containPath,
  isPathInside,
  normalizePath,
  type AppDataPaths,
  type DataFs,
} from "../data/app-data-layout";
import path from "node:path";

export const DELETE_REFUSAL_CODES = {
  NOT_CONFIRMED: "NOT_CONFIRMED",
  OUTSIDE_APP_DATA: "OUTSIDE_APP_DATA",
  DELETE_FAILED: "DELETE_FAILED",
} as const;
export type DeleteRefusalCode = (typeof DELETE_REFUSAL_CODES)[keyof typeof DELETE_REFUSAL_CODES];

/** The default uninstall removes binaries only and never touches app data. */
export const DEFAULT_UNINSTALL_PRESERVES_APP_DATA = true as const;

/** The clearly labelled, separate data-removal action — never invoked by the default path. */
export function dataRemovalLabel(): string {
  return "Delete all NovelMind data (irreversible) — this removes every novel, chapter, " +
    "analysis, visual and backup under %APPDATA%\\NovelMind and cannot be undone.";
}

export interface UninstallScope {
  /** What the default uninstall removes. */
  removes: "install-binaries-only";
  /** D-45-05: app data is preserved unless the user picks the separate removal path. */
  preservesAppData: true;
  /** The app-data root that the default uninstall must NOT touch. */
  appDataRoot: string;
}

export function defaultUninstallScope(appDataRoot: string): UninstallScope {
  return {
    removes: "install-binaries-only",
    preservesAppData: DEFAULT_UNINSTALL_PRESERVES_APP_DATA,
    appDataRoot,
  };
}

export type DeleteUserDataResult =
  | { ok: true; deleted: string }
  | { ok: false; code: DeleteRefusalCode; recoveryInstruction: string };

export interface DeleteUserDataOptions {
  fs: DataFs;
  appData: AppDataPaths;
  /** The requested deletion path; must resolve strictly inside appData.root. */
  target: string;
  /** Explicit confirmation of the labelled removal path. Always required. */
  confirm: boolean;
  /** Human label shown with the confirmation; defaults to `dataRemovalLabel()`. */
  label?: string;
}

/**
 * Resolve a deletion target strictly inside the app-data root, or throw
 * `AppDataLayoutError` (traversal/outside-root) — the same containment authority
 * every mutable write in the data module uses. Relative targets are derived
 * under the root; absolute targets must resolve inside the root.
 */
export function resolveDeletionTarget(target: string, appData: AppDataPaths): string {
  if (!path.isAbsolute(target)) {
    return containPath(appData.root, target);
  }
  const normalized = normalizePath(target);
  if (!isPathInside(appData.root, normalized)) {
    throw new AppDataLayoutError(
      APP_DATA_LAYOUT_ERROR_CODES.TRAVERSAL,
      `deletion target outside app-data root: ${target}`,
    );
  }
  return normalized;
}

/**
 * Execute the explicit, labelled data-removal path. Refuses when not confirmed,
 * when the target escapes the resolved app-data root, or when the delete fails
 * (the refusal reports the failure — data may still be intact, so the caller
 * must show the recovery instruction rather than a success).
 */
export async function deleteUserData(
  options: DeleteUserDataOptions,
): Promise<DeleteUserDataResult> {
  if (options.confirm !== true) {
    return {
      ok: false,
      code: DELETE_REFUSAL_CODES.NOT_CONFIRMED,
      recoveryInstruction:
        `Confirm the labelled removal (${options.label ?? dataRemovalLabel()}) to continue. ` +
        "Your data was NOT modified.",
    };
  }
  let resolved: string;
  try {
    resolved = resolveDeletionTarget(options.target, options.appData);
  } catch (cause) {
    const reason =
      cause instanceof AppDataLayoutError ? cause.message : String(cause ?? "unknown");
    return {
      ok: false,
      code: DELETE_REFUSAL_CODES.OUTSIDE_APP_DATA,
      recoveryInstruction:
        `The requested deletion target is outside the resolved app-data root (${reason}). ` +
        "Your data was NOT modified.",
    };
  }
  try {
    await options.fs.rm(resolved, { recursive: true, force: false });
  } catch (cause) {
    return {
      ok: false,
      code: DELETE_REFUSAL_CODES.DELETE_FAILED,
      recoveryInstruction:
        "The data removal could not complete. Check permissions on the app-data directory " +
        `and retry (${cause instanceof Error ? cause.message : String(cause)}).`,
    };
  }
  return { ok: true, deleted: resolved };
}
