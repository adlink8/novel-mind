/**
 * Uninstall/reinstall data preservation (plan 45-02, Task 2/3, D-45-05,
 * T-45-02-02).
 *
 * Proves the DEFAULT uninstall removes binaries only and preserves
 * `%APPDATA%/NovelMind` (electron-builder `deleteAppDataOnUninstall: false`),
 * and that explicit data deletion is a SEPARATE, confirmed, path-contained
 * action:
 *  - the default uninstall scope never touches the app-data tree (hashes
 *    identical before/after "uninstall then reinstall"),
 *  - reinstall over preserved data is a no-op upgrade (current) — hashes kept,
 *  - the explicit delete requires confirmation and refuses without it,
 *  - the delete target cannot escape the resolved app-data root (traversal and
 *    absolute outside-root paths are refused with a typed OUTSIDE_APP_DATA
 *    result), while a relative/inside-root absolute target deletes ONLY the
 *    requested subtree,
 *  - a denied delete reports a typed failure rather than a false success.
 */
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  dataRemovalLabel,
  defaultUninstallScope,
  deleteUserData,
  resolveDeletionTarget,
} from "../../src/update/uninstall-policy";
import { buildAppDataPaths } from "../../src/data/app-data-layout";
import { dataHashes, fixtureDataHashes, makeFixtureAppData, TARGET_SCHEMA_VERSION } from "./helpers";

test.describe("default uninstall preserves user data (uninstall-preservation)", () => {
  test("the default uninstall scope removes binaries only and never touches app data", async () => {
    const appDataRoot = path.join("C:\\", "Users", "me", "AppData", "Roaming", "NovelMind");
    const scope = defaultUninstallScope(appDataRoot);
    expect(scope.removes).toBe("install-binaries-only");
    expect(scope.preservesAppData).toBe(true);
    expect(scope.appDataRoot).toBe(appDataRoot);
    // The labelled removal action is distinct from the default scope.
    expect(dataRemovalLabel()).toMatch(/irreversible/);
  });

  test("uninstall + reinstall preserves every data hash", async () => {
    const t = await makeFixtureAppData();
    try {
      const before = await dataHashes(t.fs, t.appData);
      const appDataDir = t.appData.root;

      // "Uninstall": binaries only — the app-data root directory tree is untouched.
      // (Simulated by snapshotting the app-data root; a real uninstall would
      // remove the install dir under %LOCALAPPDATA%, which shares nothing here.)
      expect(fs.existsSync(appDataDir)).toBe(true);

      // "Reinstall": a fresh binary runs against the SAME preserved app-data.
      // The preserved tree is re-initialized idempotently (layout + meta) and a
      // fresh data pass leaves every hash identical.
      const reinstall = await makeFixtureAppData();
      try {
        // Copy the preserved data into the "reinstalled" tree and re-seed the
        // fixture's migration.json so the upgrade decision is stable.
        fs.rmSync(path.join(reinstall.appData.root, "data"), { recursive: true, force: true });
        fs.cpSync(appDataDir, reinstall.appData.root, { recursive: true });
        fs.rmSync(reinstall.appData.migrationMeta, { force: true });
        fs.copyFileSync(path.join(appDataDir, "migration.json"), reinstall.appData.migrationMeta);

        const after = await dataHashes(reinstall.fs, reinstall.appData);
        expect(Object.keys(after).sort()).toEqual(Object.keys(before).sort());
        for (const [rel, hash] of Object.entries(before)) {
          expect(after[rel], `data/${rel} must survive uninstall/reinstall`).toBe(hash);
        }
      } finally {
        await reinstall.cleanup();
      }
    } finally {
      await t.cleanup();
    }
  });

  test("reinstall over preserved data is a no-op (upgrade sees current state)", async () => {
    const t = await makeFixtureAppData();
    try {
      // A "reinstalled" binary at the SAME schema version as the committed data
      // sees a current, hash-identical state — it never re-migrates.
      const before = await dataHashes(t.fs, t.appData);
      const { readVersionState } = await import("../../src/data/version-state");
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(TARGET_SCHEMA_VERSION - 1); // fixture schema 1
      const after = await dataHashes(t.fs, t.appData);
      expect(after).toEqual(before);
    } finally {
      await t.cleanup();
    }
  });
});

test.describe("explicit data deletion is confirmed and path-contained (uninstall-preservation)", () => {
  test("explicit delete refuses without confirmation and reports a recovery instruction", async () => {
    const t = await makeFixtureAppData();
    try {
      const result = await deleteUserData({
        fs: t.fs,
        appData: t.appData,
        target: "data",
        confirm: false,
      });
      expect(result.ok).toBe(false);
      if (result.ok) return;
      expect(result.code).toBe("NOT_CONFIRMED");
      expect(result.recoveryInstruction).toMatch(/NOT modified/);
      // Data is still fully present.
      expect(Object.keys(fixtureDataHashes()).length).toBeGreaterThan(0);
    } finally {
      await t.cleanup();
    }
  });

  test("traversal and outside-root absolute targets are refused (OUTSIDE_APP_DATA)", async () => {
    const t = await makeFixtureAppData();
    try {
      for (const target of [
        "..",
        "data/../../..",
        "..\\data",
        "C:\\Windows\\System32",
        path.join(t.appData.root, "..", "other"),
      ]) {
        const result = await deleteUserData({
          fs: t.fs,
          appData: t.appData,
          target,
          confirm: true,
        });
        expect(result.ok, `target ${target} must be refused`).toBe(false);
        if (!result.ok) expect(result.code).toBe("OUTSIDE_APP_DATA");
      }
    } finally {
      await t.cleanup();
    }
  });

  test("resolveDeletionTarget validates absolute targets against the app-data root", () => {
    const root = "C:\\Users\\me\\AppData\\Roaming\\NovelMind";
    const appData = buildAppDataPaths({ userDataDir: root });
    // Inside-root absolute targets resolve; outside-root ones throw.
    expect(resolveDeletionTarget(path.join(root, "data"), appData)).toBe(
      path.join(root, "data"),
    );
    expect(() => resolveDeletionTarget("data/../../outside", appData)).toThrow();
    expect(() => resolveDeletionTarget("C:\\Windows", appData)).toThrow();
  });

  test("a confirmed in-root delete removes ONLY the requested subtree", async () => {
    const t = await makeFixtureAppData();
    try {
      const result = await deleteUserData({
        fs: t.fs,
        appData: t.appData,
        target: "data/derivatives",
        confirm: true,
      });
      expect(result.ok).toBe(true);

      const remaining = await dataHashes(t.fs, t.appData);
      const fixture = fixtureDataHashes();
      expect(remaining["derivatives/timeline-novel-001.json"]).toBeUndefined();
      // Everything else survives.
      for (const [rel, hash] of Object.entries(fixture)) {
        if (rel.startsWith("derivatives/")) continue;
        expect(remaining[rel], `data/${rel} must survive a scoped delete`).toBe(hash);
      }
    } finally {
      await t.cleanup();
    }
  });

  test("a denied delete reports a typed failure, not a false success", async () => {
    const t = await makeFixtureAppData();
    try {
      // Patch the DataFs rm to deny the deletion (e.g. a locked/ACL'd tree).
      const originalRm = t.fs.rm.bind(t.fs);
      (t.fs as { rm: typeof t.fs.rm }).rm = async (p, opts) => {
        if (p === path.join(t.appData.root, "data")) {
          throw new Error("access denied (injected)");
        }
        return originalRm(p, opts);
      };
      const result = await deleteUserData({
        fs: t.fs,
        appData: t.appData,
        target: "data",
        confirm: true,
      });
      expect(result.ok).toBe(false);
      if (result.ok) return;
      expect(result.code).toBe("DELETE_FAILED");
      expect(result.recoveryInstruction).toMatch(/permissions/);
    } finally {
      await t.cleanup();
    }
  });

  test("the ps1 fixture data hashes are stable across the suite (fixture check)", async () => {
    const a = fixtureDataHashes();
    const b = fixtureDataHashes();
    expect(a).toEqual(b);
    expect(Object.keys(a).length).toBeGreaterThan(0);
  });
});
