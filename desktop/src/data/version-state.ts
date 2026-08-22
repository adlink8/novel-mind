/**
 * Versioned migration metadata (Phase 43, plan 43-03, D-43-05/D-43-06).
 *
 * `migration.json` records the committed layout/schema/runtime versions plus
 * the transaction id that produced them. It is written ATOMICALLY (tmp file +
 * rename) so a crash or fault mid-write never leaves a half-written version
 * file: the previous committed state stays authoritative until the new state
 * lands (T-43-03-01). Missing or unparseable state reads as "uninitialized"
 * (schemaVersion 0), never as a crash.
 */
import {
  APP_DATA_LAYOUT_VERSION,
  MIGRATION_META_TMP_SUFFIX,
  type DataFs,
} from "./app-data-layout";

export const DEFAULT_RUNTIME_VERSION = "0.0.0";
export const UNINITIALIZED_SCHEMA_VERSION = 0;

export interface VersionState {
  /** Directory/layout contract version (APP_DATA_LAYOUT_VERSION). */
  layoutVersion: number;
  /** Data schema version — the migration target/current value. */
  schemaVersion: number;
  /** Application runtime version string (e.g. electron app version). */
  runtimeVersion: string;
  /** ISO timestamp of the last atomic commit, or null when never committed. */
  committedAt: string | null;
  /** Migration transaction id that committed this state, or null. */
  txnId: string | null;
}

export function defaultVersionState(): VersionState {
  return {
    layoutVersion: APP_DATA_LAYOUT_VERSION,
    schemaVersion: UNINITIALIZED_SCHEMA_VERSION,
    runtimeVersion: DEFAULT_RUNTIME_VERSION,
    committedAt: null,
    txnId: null,
  };
}

function isValidVersionState(value: unknown): value is VersionState {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.layoutVersion === "number" &&
    typeof candidate.schemaVersion === "number" &&
    typeof candidate.runtimeVersion === "string"
  );
}

/** Returns the committed state, or null when uninitialized/unparseable. */
export async function readVersionState(
  fs: DataFs,
  filePath: string,
): Promise<VersionState | null> {
  let raw: string;
  try {
    raw = await fs.readFile(filePath);
  } catch {
    return null; // not yet initialized
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isValidVersionState(parsed)) return null;
    return {
      layoutVersion: parsed.layoutVersion,
      schemaVersion: parsed.schemaVersion,
      runtimeVersion: parsed.runtimeVersion,
      committedAt: parsed.committedAt ?? null,
      txnId: parsed.txnId ?? null,
    };
  } catch {
    return null;
  }
}

/**
 * Atomically commit version state: write `<filePath>.tmp`, then rename over the
 * target. On any failure the previous committed file is untouched.
 */
export async function writeVersionStateAtomic(
  fs: DataFs,
  filePath: string,
  state: VersionState,
): Promise<void> {
  const tmpPath = `${filePath}${MIGRATION_META_TMP_SUFFIX}`;
  await fs.writeFile(tmpPath, JSON.stringify(state, null, 2));
  await fs.rename(tmpPath, filePath);
}
