/**
 * Manifest/hash-backed backup evidence (Phase 43, plan 43-03, D-43-06,
 * T-43-03-01/T-43-03-03).
 *
 * A migration may only begin from a recoverable backup: every mutable file
 * under `data/` is copied into `<backups>/<txnId>/` and each entry is recorded
 * with its sha256 hash and size. `verifyBackup` recomputes the hashes before a
 * retry reuses the evidence, and `restoreBackup` copies the retained snapshot
 * back over `data/`. Retention is bounded (newest N kept) so repeated failed
 * attempts cannot grow without limit, and insufficient disk space fails
 * EXPLICITLY before any byte is written (T-43-03-03).
 */
import { createHash } from "node:crypto";
import {
  containPath,
  type AppDataPaths,
  type DataFs,
  type FileStat,
} from "./app-data-layout";
import type { VersionState } from "./version-state";

export const BACKUP_MANIFEST_FILENAME = "manifest.json";
export const BACKUP_MANIFEST_TMP_SUFFIX = ".tmp";
export const DEFAULT_BACKUP_RETENTION = 5;

export const BACKUP_ERROR_CODES = {
  INSUFFICIENT_SPACE: "INSUFFICIENT_SPACE",
  MANIFEST_INVALID: "MANIFEST_INVALID",
  HASH_MISMATCH: "HASH_MISMATCH",
  BACKUP_IO_FAILED: "BACKUP_IO_FAILED",
  RESTORE_IO_FAILED: "RESTORE_IO_FAILED",
} as const;
export type BackupErrorCode = (typeof BACKUP_ERROR_CODES)[keyof typeof BACKUP_ERROR_CODES];

export class BackupError extends Error {
  readonly code: BackupErrorCode;

  constructor(code: BackupErrorCode, message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "BackupError";
    this.code = code;
  }
}

export interface BackupEntry {
  /** Path relative to the app-data root (e.g. `data/novels/x.txt`). */
  relPath: string;
  /** sha256 hex of the file at backup time. */
  hash: string;
  size: number;
}

export interface BackupManifest {
  version: 1;
  txnId: string;
  createdAt: string;
  /** Committed state being backed up (pre-migration evidence). */
  sourceVersion: VersionState;
  entries: BackupEntry[];
  totalBytes: number;
}

export interface BackupResult {
  manifest: BackupManifest;
  /** Absolute path of the backup directory (inside app-data/backups). */
  dirPath: string;
  txnId: string;
}

/** sha256 hex of a byte buffer. */
export function sha256(data: Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

export async function hashFile(fs: DataFs, filePath: string): Promise<{ hash: string; size: number }> {
  const buffer = await fs.readBuffer(filePath);
  return { hash: sha256(buffer), size: buffer.length };
}

/** Recursively list `relPath`-relative file paths beneath `root` (sorted, deterministic). */
export async function walkTree(
  fs: DataFs,
  root: string,
  relPrefix: string,
): Promise<string[]> {
  const result: string[] = [];
  const stack: string[] = [""];
  while (stack.length > 0) {
    const rel = stack.pop() as string;
    const abs = rel === "" ? root : containPath(root, rel);
    const children = await fs.readdir(abs);
    for (const name of [...children].sort()) {
      const childRel = rel === "" ? name : `${rel}/${name}`;
      const childAbs = containPath(root, childRel);
      const stat = await fs.stat(childAbs);
      if (stat.isDirectory()) {
        stack.push(childRel);
      } else {
        result.push(relPrefix === "" ? childRel : `${relPrefix}/${childRel}`);
      }
    }
  }
  return result.sort();
}

export function backupDirectoryName(appData: AppDataPaths): string {
  return containPath(appData.backups, "."); // normalized backups root
}

export function readBackupManifest(
  fs: DataFs,
  manifestPath: string,
): Promise<BackupManifest | null> {
  return fs
    .readFile(manifestPath)
    .then((raw) => {
      try {
        const parsed: unknown = JSON.parse(raw);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          (parsed as { version?: unknown }).version !== 1 ||
          !Array.isArray((parsed as { entries?: unknown }).entries) ||
          typeof (parsed as { sourceVersion?: unknown }).sourceVersion !== "object"
        ) {
          return null;
        }
        return parsed as BackupManifest;
      } catch {
        return null;
      }
    })
    .catch(() => null);
}

/**
 * Create a hash-backed backup of `data/` inside `<backups>/<txnId>/`. Fails
 * explicitly with INSUFFICIENT_SPACE when the volume cannot hold the snapshot
 * before any byte is written. Prunes to `retention` after success.
 */
export async function createBackup(
  fs: DataFs,
  appData: AppDataPaths,
  options: { sourceVersion: VersionState; retention?: number },
): Promise<BackupResult> {
  const retention = options.retention ?? DEFAULT_BACKUP_RETENTION;
  const dataRoot = appData.data;
  const backupsRoot = backupDirectoryName(appData);

  let relFiles: string[];
  try {
    relFiles = await walkTree(fs, dataRoot, "data");
  } catch (cause) {
    throw new BackupError(
      BACKUP_ERROR_CODES.BACKUP_IO_FAILED,
      `could not enumerate mutable data for backup: ${cause instanceof Error ? cause.message : String(cause)}`,
      { cause },
    );
  }

  const entries: BackupEntry[] = [];
  let totalBytes = 0;
  try {
    for (const rel of relFiles) {
      const abs = containPath(dataRoot, rel.slice("data/".length));
      const { hash, size } = await hashFile(fs, abs);
      entries.push({ relPath: rel, hash, size });
      totalBytes += size;
    }
  } catch (cause) {
    throw new BackupError(
      BACKUP_ERROR_CODES.BACKUP_IO_FAILED,
      `could not hash data for backup: ${cause instanceof Error ? cause.message : String(cause)}`,
      { cause },
    );
  }

  // Explicit insufficient-space gate BEFORE any write (T-43-03-03).
  const freeBytes = await fs.statFreeBytes(backupsRoot);
  if (Number.isFinite(freeBytes) && freeBytes < totalBytes) {
    throw new BackupError(
      BACKUP_ERROR_CODES.INSUFFICIENT_SPACE,
      `insufficient disk space for backup: need ${totalBytes} bytes, have ${freeBytes}`,
    );
  }

  const txnId = `backup-v${options.sourceVersion.schemaVersion}_${new Date()
    .toISOString()
    .replace(/[:.]/g, "-")}_${Math.random().toString(36).slice(2, 6)}`;
  const dirPath = containPath(appData.backups, txnId);

  try {
    await fs.mkdir(dirPath, { recursive: true });
    for (const entry of entries) {
      const srcAbs = containPath(dataRoot, entry.relPath.slice("data/".length));
      const destAbs = containPath(dirPath, entry.relPath);
      await fs.mkdir(containPath(dirPath, pathDir(entry.relPath)), { recursive: true });
      await fs.copyFile(srcAbs, destAbs);
    }
    const manifest: BackupManifest = {
      version: 1,
      txnId,
      createdAt: new Date().toISOString(),
      sourceVersion: options.sourceVersion,
      entries,
      totalBytes,
    };
    const manifestPath = containPath(dirPath, BACKUP_MANIFEST_FILENAME);
    const tmpPath = `${manifestPath}${BACKUP_MANIFEST_TMP_SUFFIX}`;
    await fs.writeFile(tmpPath, JSON.stringify(manifest, null, 2));
    await fs.rename(tmpPath, manifestPath);

    await pruneBackups(fs, appData, retention, txnId);
    return { manifest, dirPath, txnId };
  } catch (cause) {
    if (cause instanceof BackupError) throw cause;
    throw new BackupError(
      BACKUP_ERROR_CODES.BACKUP_IO_FAILED,
      `backup write failed: ${cause instanceof Error ? cause.message : String(cause)}`,
      { cause },
    );
  }
}

/**
 * Recompute hashes of every backed-up entry; throws HASH_MISMATCH on the first
 * drift. This is the integrity gate before a retry reuses backup evidence.
 */
export async function verifyBackup(fs: DataFs, manifest: BackupManifest, dirPath: string): Promise<void> {
  for (const entry of manifest.entries) {
    const abs = containPath(dirPath, entry.relPath);
    let actual: { hash: string; size: number };
    try {
      actual = await hashFile(fs, abs);
    } catch (cause) {
      throw new BackupError(
        BACKUP_ERROR_CODES.HASH_MISMATCH,
        `backup entry missing: ${entry.relPath}`,
        { cause },
      );
    }
    if (actual.hash !== entry.hash || actual.size !== entry.size) {
      throw new BackupError(
        BACKUP_ERROR_CODES.HASH_MISMATCH,
        `backup entry corrupt: ${entry.relPath}`,
      );
    }
  }
}

/** Copy a verified backup back over `data/`, restoring the pre-migration state. */
export async function restoreBackup(
  fs: DataFs,
  manifest: BackupManifest,
  dirPath: string,
  appData: AppDataPaths,
): Promise<void> {
  try {
    for (const entry of manifest.entries) {
      const srcAbs = containPath(dirPath, entry.relPath);
      const destRel = entry.relPath.slice("data/".length);
      const destAbs = containPath(appData.data, destRel);
      await fs.mkdir(containPath(appData.data, pathDir(destRel)), { recursive: true });
      await fs.copyFile(srcAbs, destAbs);
    }
  } catch (cause) {
    throw new BackupError(
      BACKUP_ERROR_CODES.RESTORE_IO_FAILED,
      `backup restore failed: ${cause instanceof Error ? cause.message : String(cause)}`,
      { cause },
    );
  }
}

/** Absolute backup directory names (txn dirs), oldest first. */
export async function listBackups(fs: DataFs, appData: AppDataPaths): Promise<string[]> {
  const backupsRoot = backupDirectoryName(appData);
  let names: string[];
  try {
    names = await fs.readdir(backupsRoot);
  } catch {
    return [];
  }
  const result: string[] = [];
  for (const name of [...names].sort()) {
    const abs = containPath(backupsRoot, name);
    const stat: FileStat = await fs.stat(abs);
    if (!stat.isDirectory()) continue;
    if (await fs.exists(containPath(abs, BACKUP_MANIFEST_FILENAME))) result.push(abs);
  }
  return result;
}

/**
 * Bounded retention (T-43-03-03): keep the newest `retention` backups, delete
 * the rest. `keepTxnId` (when given) is always preserved regardless of age.
 */
export async function pruneBackups(
  fs: DataFs,
  appData: AppDataPaths,
  retention: number,
  keepTxnId?: string,
): Promise<void> {
  const backups = await listBackups(fs, appData);
  // Newest first: txn names are ISO-timestamp prefixed so descending sort
  // approximates newest-first; guard with mtime for correctness.
  const withMtime = await Promise.all(
    backups.map(async (dir) => ({ dir, mtime: (await fs.stat(dir)).mtimeMs })),
  );
  withMtime.sort((a, b) => b.mtime - a.mtime);
  const keep = Math.max(0, retention);
  const toDelete = withMtime.slice(keep).filter(
    ({ dir }) => keepTxnId === undefined || !dir.endsWith(keepTxnId),
  );
  for (const { dir } of toDelete) {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

function pathDir(relPath: string): string {
  const idx = relPath.lastIndexOf("/");
  return idx <= 0 ? "." : relPath.slice(0, idx);
}
