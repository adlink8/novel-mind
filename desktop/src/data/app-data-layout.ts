/**
 * Versioned app-data layout (Phase 43, plan 43-03, D-43-05).
 *
 * All mutable state — data, logs, backups, runtime bookkeeping and secrets —
 * lives beneath a single versioned root corresponding to Electron
 * `app.getPath('userData')` (`%APPDATA%/NovelMind` on Windows). Installed
 * application resources stay immutable and are never written.
 *
 * This module is the path authority for the whole `desktop/src/data` module:
 * every write target is derived through `containPath` (which rejects traversal),
 * the root must not overlap the install root, and repeated initialization is
 * idempotent (D-43-06). It also defines the injected `DataFs` seam — the data
 * module never touches `node:fs` directly, so the fault-injection suites can
 * simulate denied writes, low disk space and corrupt files deterministically.
 *
 * Layout (all children of `root` = `app.getPath('userData')`):
 *   data/       — mutable user data (migrated content, uploads, storage)
 *   logs/       — bounded, rotated diagnostics (ownership: runtime, plan 43-02)
 *   backups/    — hash-backed migration/restore evidence (bounded retention)
 *   runtime/    — runtime bookkeeping (migration journal, snapshots)
 *   secrets/    — credentials/keys; never migrated, never backed up by this layer
 *   migration.json — versioned metadata: layout/schema/runtime version + txnId
 *
 * Electron integration: the main process passes `app.getPath('userData')` as
 * `userDataDir`. This module stays Electron-free so it is unit-testable; in
 * non-Electron contexts `NOVELMIND_USER_DATA` provides the root.
 */
import fs from "node:fs/promises";
import path from "node:path";

export const APP_DATA_DIR_NAMES = ["data", "logs", "backups", "runtime", "secrets"] as const;
export type AppDataDirName = (typeof APP_DATA_DIR_NAMES)[number];

/** Bumped only when the directory/layout contract itself changes. */
export const APP_DATA_LAYOUT_VERSION = 1;

/** Versioned metadata file written atomically by version-state.ts. */
export const MIGRATION_META_FILENAME = "migration.json";
export const MIGRATION_META_TMP_SUFFIX = ".tmp";

/** Runtime bookkeeping (migration journal). */
export const MIGRATION_JOURNAL_FILENAME = "migration-journal.json";

export interface AppDataPaths {
  /** Canonical, normalized, absolute userData root. */
  root: string;
  data: string;
  logs: string;
  backups: string;
  runtime: string;
  secrets: string;
  /** Absolute path of the versioned metadata file (migration.json). */
  migrationMeta: string;
}

export interface AppDataLayoutOptions {
  /** Electron `app.getPath('userData')`; must be an absolute path. */
  userDataDir: string;
  /**
   * Absolute path of the installed (immutable) application resources. When
   * provided, the app-data root must not overlap it in either direction —
   * writes into installation resources are rejected by construction.
   */
  installRoot?: string;
}

export const APP_DATA_LAYOUT_ERROR_CODES = {
  INVALID_ROOT: "INVALID_ROOT",
  TRAVERSAL: "TRAVERSAL",
  INSTALL_ROOT_OVERLAP: "INSTALL_ROOT_OVERLAP",
  WRITE_DENIED: "WRITE_DENIED",
} as const;
export type AppDataLayoutErrorCode =
  (typeof APP_DATA_LAYOUT_ERROR_CODES)[keyof typeof APP_DATA_LAYOUT_ERROR_CODES];

export class AppDataLayoutError extends Error {
  readonly code: AppDataLayoutErrorCode;

  constructor(code: AppDataLayoutErrorCode, message: string) {
    super(message);
    this.name = "AppDataLayoutError";
    this.code = code;
  }
}

/**
 * Injected filesystem seam. The data module never calls `node:fs` directly;
 * real operations come from `nodeDataFs()`, and tests inject deterministic
 * fakes (denied writes, low disk space, corrupt data).
 */
export interface FileStat {
  isDirectory(): boolean;
  size: number;
  mtimeMs: number;
}

export interface DataFs {
  mkdir(p: string, opts?: { recursive?: boolean }): Promise<void>;
  writeFile(p: string, data: string): Promise<void>;
  readFile(p: string): Promise<string>;
  readBuffer(p: string): Promise<Buffer>;
  rename(oldPath: string, newPath: string): Promise<void>;
  copyFile(src: string, dest: string): Promise<void>;
  readdir(p: string): Promise<string[]>;
  stat(p: string): Promise<FileStat>;
  exists(p: string): Promise<boolean>;
  rm(p: string, opts: { recursive?: boolean; force?: boolean }): Promise<void>;
  /** Free bytes on the volume containing `p`; implementations may return Infinity when undeterminable. */
  statFreeBytes(p: string): Promise<number>;
}

/** Real filesystem implementation of `DataFs` (node:fs/promises + statfs). */
export function nodeDataFs(): DataFs {
  return {
    mkdir: async (p, opts) => {
      await fs.mkdir(p, opts);
    },
    writeFile: (p, data) => fs.writeFile(p, data, "utf8"),
    readFile: (p) => fs.readFile(p, "utf8"),
    readBuffer: (p) => fs.readFile(p),
    rename: (oldPath, newPath) => fs.rename(oldPath, newPath),
    copyFile: (src, dest) => fs.copyFile(src, dest),
    readdir: (p) => fs.readdir(p),
    stat: async (p) => {
      const s = await fs.stat(p);
      return { isDirectory: () => s.isDirectory(), size: s.size, mtimeMs: s.mtimeMs };
    },
    exists: async (p) => {
      try {
        await fs.access(p);
        return true;
      } catch {
        return false;
      }
    },
    rm: (p, opts) => fs.rm(p, opts),
    statFreeBytes: async (p) => {
      try {
        const s = await fs.statfs(p);
        return s.bavail * s.bsize;
      } catch {
        // Volume cannot be inspected (unsupported/not mounted): behave as if
        // space is available so the layout still works; the backup step remains
        // the explicit insufficient-space gate on platforms that support it.
        return Number.MAX_SAFE_INTEGER;
      }
    },
  };
}

/**
 * Canonicalize a path: resolve to an absolute path and strip trailing
 * separators so containment comparisons are exact on every platform.
 */
export function normalizePath(p: string): string {
  let normalized = path.resolve(p);
  while (
    normalized.length > 1 &&
    (normalized.endsWith(path.sep) || normalized.endsWith("/") || normalized.endsWith("\\"))
  ) {
    normalized = normalized.slice(0, -1);
  }
  return normalized;
}

function caseFold(p: string): string {
  // Windows paths are case-insensitive; every containment decision folds case.
  return process.platform === "win32" ? p.toLowerCase() : p;
}

/** True when `candidate` equals `parent` or resolves strictly beneath it. */
export function isPathInside(parent: string, candidate: string): boolean {
  const rel = path.relative(normalizePath(parent), normalizePath(candidate));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/**
 * Resolve `segments` beneath `root`, rejecting any traversal that escapes the
 * root and any absolute segment. Every mutable write target in this module is
 * derived through this guard (T-43-03-02).
 */
export function containPath(root: string, ...segments: readonly string[]): string {
  const base = normalizePath(root);
  if (base === "" || !path.isAbsolute(base)) {
    throw new AppDataLayoutError(APP_DATA_LAYOUT_ERROR_CODES.INVALID_ROOT, "app-data root must be an absolute path");
  }
  for (const segment of segments) {
    if (path.isAbsolute(segment)) {
      throw new AppDataLayoutError(
        APP_DATA_LAYOUT_ERROR_CODES.TRAVERSAL,
        `absolute path segment rejected: ${segment}`,
      );
    }
  }
  const joined = path.resolve(base, ...segments);
  if (!isPathInside(base, joined)) {
    throw new AppDataLayoutError(
      APP_DATA_LAYOUT_ERROR_CODES.TRAVERSAL,
      `path escapes app-data root: ${joined}`,
    );
  }
  return joined;
}

/**
 * The app-data root and the install root must be disjoint (in either
 * direction). If they overlap, writes under app-data could reach installed
 * resources — prohibited (D-43-05, T-43-03-02).
 */
export function assertLayoutIsolated(appDataRoot: string, installRoot: string): void {
  const app = normalizePath(appDataRoot);
  const inst = normalizePath(installRoot);
  if (caseFold(app) === caseFold(inst) || isPathInside(app, inst) || isPathInside(inst, app)) {
    throw new AppDataLayoutError(
      APP_DATA_LAYOUT_ERROR_CODES.INSTALL_ROOT_OVERLAP,
      `app-data root overlaps the install root (${appDataRoot} vs ${installRoot})`,
    );
  }
}

/**
 * A read-only migration/resource input must live OUTSIDE the app-data root.
 * This guards the migration direction: install resources are inputs, app-data
 * is the only writable output (D-43-05).
 */
export function assertResourceInputOutsideAppData(appDataRoot: string, resourcePath: string): void {
  if (isPathInside(appDataRoot, resourcePath)) {
    throw new AppDataLayoutError(
      APP_DATA_LAYOUT_ERROR_CODES.INSTALL_ROOT_OVERLAP,
      `migration resource must not live inside app-data: ${resourcePath}`,
    );
  }
}

/**
 * Build the versioned layout paths. Pure (no filesystem access) except for the
 * install-root isolation check; throws on malformed/overlapping roots.
 */
export function buildAppDataPaths(options: AppDataLayoutOptions): AppDataPaths {
  if (!path.isAbsolute(options.userDataDir)) {
    throw new AppDataLayoutError(
      APP_DATA_LAYOUT_ERROR_CODES.INVALID_ROOT,
      `userDataDir must be an absolute path (got "${options.userDataDir}")`,
    );
  }
  const root = normalizePath(options.userDataDir);
  if (options.installRoot !== undefined) {
    assertLayoutIsolated(root, options.installRoot);
  }
  const dirs = {
    data: containPath(root, "data"),
    logs: containPath(root, "logs"),
    backups: containPath(root, "backups"),
    runtime: containPath(root, "runtime"),
    secrets: containPath(root, "secrets"),
  };
  return {
    root,
    ...dirs,
    migrationMeta: containPath(root, MIGRATION_META_FILENAME),
  };
}

/**
 * Idempotently create the versioned layout directories. Safe to call on every
 * startup (D-43-06 first-run and compatible upgrade are idempotent).
 */
export async function initializeAppDataPaths(
  fs: DataFs,
  options: AppDataLayoutOptions,
): Promise<AppDataPaths> {
  const paths = buildAppDataPaths(options);
  try {
    for (const dir of [paths.data, paths.logs, paths.backups, paths.runtime, paths.secrets]) {
      await fs.mkdir(dir, { recursive: true });
    }
  } catch (cause) {
    throw new AppDataLayoutError(
      APP_DATA_LAYOUT_ERROR_CODES.WRITE_DENIED,
      `could not create app-data layout: ${cause instanceof Error ? cause.message : String(cause)}`,
    );
  }
  return paths;
}

/** Non-Electron default root; Electron callers pass `app.getPath('userData')`. */
export function defaultAppDataRoot(): string | null {
  const value = process.env.NOVELMIND_USER_DATA;
  return value === undefined || value === "" ? null : value;
}
