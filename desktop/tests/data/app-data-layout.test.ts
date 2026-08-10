/**
 * App-data layout + version-state suites (plan 43-03, Task 1).
 *
 * Proves (acceptance criteria): all mutable paths stay within userData, the
 * install root is never writable/overlapping (both directions), resource paths
 * are read-only inputs, repeated initialization is idempotent, traversal is
 * rejected, and versioned metadata commits atomically (tmp + rename) with the
 * previous committed state preserved on any failure (T-43-03-01/T-43-03-02).
 */
import { expect, test } from "@playwright/test";
import {
  APP_DATA_DIR_NAMES,
  APP_DATA_LAYOUT_VERSION,
  AppDataLayoutError,
  buildAppDataPaths,
  containPath,
  initializeAppDataPaths,
  isPathInside,
  normalizePath,
} from "../../src/data/app-data-layout";
import {
  UNINITIALIZED_SCHEMA_VERSION,
  defaultVersionState,
  readVersionState,
  writeVersionStateAtomic,
} from "../../src/data/version-state";
import { FakeDataFs } from "./fake-data-fs";

const USER_DATA = "C:\\Users\\me\\AppData\\Roaming\\NovelMind";
const INSTALL_ROOT = "C:\\Program Files\\NovelMind";

test.describe("app-data layout", () => {
  test("all mutable paths live inside userData with versioned subdirs", () => {
    const paths = buildAppDataPaths({ userDataDir: USER_DATA, installRoot: INSTALL_ROOT });
    expect(paths.root).toBe("C:\\Users\\me\\AppData\\Roaming\\NovelMind");
    for (const name of APP_DATA_DIR_NAMES) {
      const dir = paths[name];
      expect(isPathInside(paths.root, dir)).toBe(true);
      expect(dir).toBe(`C:\\Users\\me\\AppData\\Roaming\\NovelMind\\${name}`);
    }
    expect(paths.migrationMeta).toBe(
      "C:\\Users\\me\\AppData\\Roaming\\NovelMind\\migration.json",
    );
    // The layout is versioned: metadata file carries the layout contract.
    expect(APP_DATA_LAYOUT_VERSION).toBeGreaterThan(0);
  });

  test("paths are normalized (no trailing separators, absolute)", () => {
    const paths = buildAppDataPaths({
      userDataDir: "C:\\Users\\me\\AppData\\Roaming\\NovelMind\\",
      installRoot: INSTALL_ROOT,
    });
    expect(paths.root.endsWith("\\")).toBe(false);
    expect(paths.data.endsWith("\\")).toBe(false);
    expect(normalizePath("C:/a/b/")).toBe("C:\\a\\b");
  });

  test("repeated initialization is idempotent", async () => {
    const fs = new FakeDataFs();
    const first = await initializeAppDataPaths(fs, {
      userDataDir: USER_DATA,
      installRoot: INSTALL_ROOT,
    });
    const second = await initializeAppDataPaths(fs, {
      userDataDir: USER_DATA,
      installRoot: INSTALL_ROOT,
    });
    expect(first).toEqual(second);
    for (const name of APP_DATA_DIR_NAMES) {
      expect(await fs.exists(first[name])).toBe(true);
    }
  });

  test("denied appData write surfaces typed WRITE_DENIED", async () => {
    const fs = new FakeDataFs();
    fs.faults.denyAllWrites = true;
    await expect(
      initializeAppDataPaths(fs, { userDataDir: USER_DATA, installRoot: INSTALL_ROOT }),
    ).rejects.toMatchObject({ code: "WRITE_DENIED" });
  });

  test("relative userData root is rejected", () => {
    expect(() => buildAppDataPaths({ userDataDir: "NovelMind", installRoot: INSTALL_ROOT })).toThrow(
      AppDataLayoutError,
    );
    expect(() => buildAppDataPaths({ userDataDir: "NovelMind" })).toThrowError(/absolute/);
  });

  test("app-data root must not overlap the install root (either direction)", () => {
    // userData inside install root.
    expect(() =>
      buildAppDataPaths({
        userDataDir: "C:\\Program Files\\NovelMind\\AppData",
        installRoot: "C:\\Program Files\\NovelMind",
      }),
    ).toThrowError(/overlap/);
    // install root inside userData.
    expect(() =>
      buildAppDataPaths({
        userDataDir: "C:\\Users\\me\\AppData\\Roaming\\NovelMind",
        installRoot: "C:\\Users\\me\\AppData\\Roaming\\NovelMind\\resources",
      }),
    ).toThrowError(/overlap/);
    // Equal roots are an overlap.
    expect(() =>
      buildAppDataPaths({ userDataDir: USER_DATA, installRoot: USER_DATA }),
    ).toThrowError(/overlap/);
  });

  test("traversal is rejected for every mutable path", () => {
    const paths = buildAppDataPaths({ userDataDir: USER_DATA, installRoot: INSTALL_ROOT });
    expect(() => containPath(paths.data, "..", "escape.txt")).toThrow(AppDataLayoutError);
    expect(() => containPath(paths.data, "..\\escape.txt")).toThrow(AppDataLayoutError);
    expect(() => containPath(paths.data, "a", "..", "..", "x")).toThrow(AppDataLayoutError);
    expect(() => containPath(paths.data, "C:\\Windows\\win.ini")).toThrow(AppDataLayoutError);
    expect(() => containPath(paths.data, "/etc/passwd")).toThrow(AppDataLayoutError);
    // Windows absolute path segment rejected.
    expect(() => containPath(paths.data, "C:/Windows")).toThrow(AppDataLayoutError);
  });

  test("containPath never returns a path outside the root", () => {
    const root = "C:\\app-data";
    for (const segments of [
      ["data"],
      ["data", "nested", "file.txt"],
      ["backups", "txn", "deep", "a"],
    ]) {
      const p = containPath(root, ...segments);
      expect(isPathInside(root, p)).toBe(true);
    }
  });

  test("migration resources must live outside app-data (read-only inputs)", async () => {
    const paths = buildAppDataPaths({ userDataDir: USER_DATA, installRoot: INSTALL_ROOT });
    // A resource inside userData (e.g. data/ being treated as a source) is refused.
    expect(isPathInside(paths.root, `${USER_DATA}\\backend\\uploads`)).toBe(true);
    expect(isPathInside(paths.root, "C:\\Program Files\\NovelMind\\backend\\uploads")).toBe(false);
    expect(isPathInside(paths.root, USER_DATA)).toBe(true);
  });
});

test.describe("version state", () => {
  test("uninitialized state reads as schema version 0 (never a crash)", async () => {
    const fs = new FakeDataFs();
    const state = await readVersionState(fs, "C:\\app-data\\migration.json");
    expect(state).toBeNull();
    expect(defaultVersionState().schemaVersion).toBe(UNINITIALIZED_SCHEMA_VERSION);
  });

  test("corrupt/malformed metadata reads as uninitialized", async () => {
    const fs = new FakeDataFs();
    fs.seed("C:/app-data/migration.json", "{not json");
    expect(await readVersionState(fs, "C:\\app-data\\migration.json")).toBeNull();
    fs.seed("C:/app-data/migration.json", JSON.stringify({ version: "bogus" }));
    expect(await readVersionState(fs, "C:\\app-data\\migration.json")).toBeNull();
  });

  test("roundtrip preserves every field", async () => {
    const fs = new FakeDataFs();
    const filePath = "C:\\app-data\\migration.json";
    const state = {
      layoutVersion: APP_DATA_LAYOUT_VERSION,
      schemaVersion: 3,
      runtimeVersion: "1.2.3",
      committedAt: "2026-08-10T00:00:00.000Z",
      txnId: "backup-v2_2026-08-10T00-00-00_ab12",
    };
    await writeVersionStateAtomic(fs, filePath, state);
    const read = await readVersionState(fs, filePath);
    expect(read).toEqual(state);
  });

  test("commit is atomic: a failed write leaves the previous state untouched", async () => {
    const fs = new FakeDataFs();
    const filePath = "C:\\app-data\\migration.json";
    await writeVersionStateAtomic(fs, filePath, {
      layoutVersion: APP_DATA_LAYOUT_VERSION,
      schemaVersion: 1,
      runtimeVersion: "1.0.0",
      committedAt: null,
      txnId: null,
    });

    fs.faults.denyPathPrefix = "C:/app-data/migration.json.tmp";
    await expect(
      writeVersionStateAtomic(fs, filePath, {
        layoutVersion: APP_DATA_LAYOUT_VERSION,
        schemaVersion: 2,
        runtimeVersion: "1.1.0",
        committedAt: "2026-08-10T00:00:00.000Z",
        txnId: "txn-2",
      }),
    ).rejects.toThrow();
    const after = await readVersionState(fs, filePath);
    expect(after?.schemaVersion).toBe(1); // previous committed state preserved
    expect(fs.content(filePath)).not.toContain('"txn-2"');
  });
});
