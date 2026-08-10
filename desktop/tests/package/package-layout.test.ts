/**
 * Packaged-resource and mutable-path audit (plan 45-01, Task 2/4).
 *
 * Audits the electron-builder packaging contract and the hash-verified staged
 * runtime tree produced by `scripts/stage-runtime.ps1`:
 *
 *  - electron-builder.yml is the reproducible Windows packaging contract:
 *    appId/productName, win-unpacked + NSIS x64, asar, files whitelist, the
 *    `next-standalone` extraResources tree, unsigned local qualification and
 *    data-preserving uninstall (T-45-01-01 / D-45-05 / D-45-06).
 *  - The staged inventory matches the pinned Phase 41 proof manifest and every
 *    staged file reproduces its declared SHA-256 (T-45-01-01).
 *  - The staged tree is self-contained: no first-run download prerequisite and
 *    no fixed packaged port (the server binds the OS-allocated loopback PORT).
 *  - Mutable state stays under `%APPDATA%/NovelMind`, never inside install
 *    resources (D-45-03 / D-43-05): the app-data root is disjoint from the
 *    win-unpacked install root and no mutable-state directory ships in the
 *    staged resources.
 *  - When a build has run, the win-unpacked artifact contains the exe, app.asar
 *    and the exact staged server.js, and the exe is a GUI-subsystem binary
 *    (no console window, D-45-02).
 */
import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { buildAppDataPaths } from "../../src/data/app-data-layout";
import { peSubsystem } from "./pe-subsystem";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
const STAGED_ROOT = path.join(DESKTOP_DIR, "dist", "staged");
const STAGED_TREE = path.join(STAGED_ROOT, "next-standalone");
const STAGED_MANIFEST = path.join(STAGED_ROOT, "staged-manifest.json");
const PROOF_MANIFEST = path.join(DESKTOP_DIR, "proof", "runtime-manifest.json");
const YML = path.join(DESKTOP_DIR, "electron-builder.yml");
const UNPACKED_DIR = path.join(DESKTOP_DIR, "dist", "win-unpacked");

const MUTABLE_STATE_DIRS = [
  "pgdata",
  "data",
  "logs",
  "backups",
  "secrets",
  "uploads",
  "storage",
  "artifacts",
  "chroma",
] as const;

function sha256File(filePath: string): string {
  const hash = createHash("sha256");
  hash.update(readFileSync(filePath));
  return hash.digest("hex");
}

function walkFiles(root: string): string[] {
  const out: string[] = [];
  const visit = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        visit(full);
      } else {
        out.push(full);
      }
    }
  };
  visit(root);
  return out;
}

/** Relative staged paths with forward slashes, sorted — mirrors stage-runtime.ps1. */
function stagedRelPaths(): string[] {
  return walkFiles(STAGED_TREE)
    .map((f) => f.slice(STAGED_TREE.length + 1).replace(/\\/g, "/"))
    .sort();
}

test.describe("electron-builder.yml — reproducible Windows packaging contract", () => {
  test("appId, productName and unsigned local qualification are declared", () => {
    expect(existsSync(YML)).toBe(true);
    const yml = readFileSync(YML, "utf8");
    expect(yml).toContain("appId: com.novelmind.desktop");
    expect(yml).toContain("productName: NovelMind");
    // Unsigned local qualification — no winCodeSign download, no publication.
    expect(yml).toContain("signAndEditExecutable: false");
  });

  test("win-unpacked and NSIS x64 targets; data preserved on uninstall", () => {
    const yml = readFileSync(YML, "utf8");
    expect(yml).toMatch(/target:\s*dir/);
    expect(yml).toMatch(/target:\s*nsis/);
    expect(yml).toContain("- x64");
    expect(yml).toContain("asar: true");
    // D-45-05: uninstall keeps user data unless a labelled removal path is chosen.
    expect(yml).toContain("deleteAppDataOnUninstall: false");
  });

  test("no publish/auto-update section — no first-run download prerequisite", () => {
    const yml = readFileSync(YML, "utf8");
    expect(yml).not.toContain("publish:");
    expect(yml).not.toContain("electron-updater");
    expect(yml).toContain("next-standalone"); // staged tree is shipped as resources
  });

  test("staged next-standalone tree is carried as extraResources (never inside asar)", () => {
    const yml = readFileSync(YML, "utf8");
    expect(yml).toMatch(/extraResources:/);
    expect(yml).toContain("from: dist/staged/next-standalone");
    expect(yml).toContain("to: next-standalone");
  });
});

test.describe("staged runtime inventory — hash-pinned and self-contained", () => {
  test("staged manifest exists and pins match the Phase 41 proof manifest", () => {
    expect(existsSync(STAGED_MANIFEST), "run scripts/stage-runtime.ps1 first").toBe(true);
    expect(existsSync(PROOF_MANIFEST)).toBe(true);
    const staged = JSON.parse(readFileSync(STAGED_MANIFEST, "utf8")) as {
      pins: { electron: string; embeddedNode: string; serverJsHash: string };
    };
    const proof = JSON.parse(readFileSync(PROOF_MANIFEST, "utf8")) as {
      environment: { electron: { packageVersion: string; embeddedNodeDeclared: string } };
      components: { runtimeArtifact: { hash: string } }[];
    };
    expect(staged.pins.electron).toBe(proof.environment.electron.packageVersion);
    expect(staged.pins.embeddedNode).toBe(proof.environment.electron.embeddedNodeDeclared);
    expect(staged.pins.serverJsHash).toBe(proof.components[0]!.runtimeArtifact.hash);
  });

  test("staged server.js reproduces the pinned hash", () => {
    const staged = JSON.parse(readFileSync(STAGED_MANIFEST, "utf8")) as {
      pins: { serverJsHash: string };
    };
    const serverJs = path.join(STAGED_TREE, "server.js");
    expect(existsSync(serverJs)).toBe(true);
    expect(sha256File(serverJs)).toBe(staged.pins.serverJsHash);
  });

  test("every staged file reproduces its declared SHA-256 inventory", () => {
    const staged = JSON.parse(readFileSync(STAGED_MANIFEST, "utf8")) as {
      fileCount: number;
      files: { path: string; sha256: string }[];
    };
    const declared = new Map(staged.files.map((f) => [f.path, f.sha256]));
    const actual = stagedRelPaths();
    expect(actual).toHaveLength(staged.fileCount);
    for (const rel of actual) {
      const hash = sha256File(path.join(STAGED_TREE, rel));
      expect(declared.get(rel), `undeclared staged file ${rel}`).toBe(hash);
    }
  });

  test("staged tree is self-contained (no first-run download prerequisite)", () => {
    const staged = JSON.parse(readFileSync(STAGED_MANIFEST, "utf8")) as {
      pins: { next: string; react: string };
    };
    expect(existsSync(path.join(STAGED_TREE, "node_modules"))).toBe(true);
    expect(existsSync(path.join(STAGED_TREE, "server.js"))).toBe(true);
    expect(existsSync(path.join(STAGED_TREE, "package.json"))).toBe(true);
    const staticDir = path.join(STAGED_TREE, ".next", "static");
    const publicDir = path.join(STAGED_TREE, "public");
    expect(existsSync(staticDir)).toBe(true);
    expect(existsSync(publicDir)).toBe(true);
    expect(readdirSync(staticDir).length).toBeGreaterThan(0);
    expect(readdirSync(publicDir).length).toBeGreaterThan(0);
    const pkg = JSON.parse(readFileSync(path.join(STAGED_TREE, "package.json"), "utf8")) as {
      dependencies: { next: string; react: string };
    };
    expect(pkg.dependencies.next).toBe(staged.pins.next);
    expect(pkg.dependencies.react).toBe(staged.pins.react);
  });

  test("no source maps and no secrets ship in the staged resources", () => {
    const rels = stagedRelPaths();
    expect(rels.some((r) => r.endsWith(".map"))).toBe(false);
    expect(rels.some((r) => r.toLowerCase().endsWith(".env"))).toBe(false);
    expect(rels.some((r) => r.toLowerCase().endsWith(".pem") || r.toLowerCase().endsWith(".key"))).toBe(false);
  });

  test("no fixed packaged port — the standalone server binds process.env.PORT", () => {
    const serverJs = readFileSync(path.join(STAGED_TREE, "server.js"), "utf8");
    expect(serverJs).toContain("process.env.PORT");
    // The server itself must never `listen(<literal>)` on a fixed port.
    expect(serverJs.match(/\.listen\(\s*\d/)).toBeNull();
  });
});

test.describe("mutable path audit — data under %APPDATA%, never install resources", () => {
  test("app-data root is isolated from the win-unpacked install root", () => {
    const appData = path.join(
      process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming"),
      "NovelMind",
    );
    // buildAppDataPaths rejects app-data/install-root overlap (D-43-05).
    let error: unknown = null;
    try {
      buildAppDataPaths({ userDataDir: appData, installRoot: UNPACKED_DIR });
    } catch (cause) {
      error = cause;
    }
    expect(error).toBeNull();
  });

  test("staged install resources contain no mutable-state directories", () => {
    expect(existsSync(STAGED_TREE)).toBe(true);
    for (const dir of MUTABLE_STATE_DIRS) {
      expect(
        existsSync(path.join(STAGED_TREE, dir)),
        `mutable state dir "${dir}" must not ship inside install resources`,
      ).toBe(false);
    }
  });
});

test.describe("win-unpacked artifact audit (runs after a real build)", () => {
  test("unpacked app contains exe, app.asar and the exact staged next-standalone tree", () => {
    test.skip(!existsSync(UNPACKED_DIR), "run scripts/build-windows.ps1 first");
    const exe = ["NovelMind.exe", "electron.exe"]
      .map((n) => path.join(UNPACKED_DIR, n))
      .find(existsSync);
    expect(exe, "win-unpacked must contain the app exe").toBeDefined();
    expect(existsSync(path.join(UNPACKED_DIR, "resources", "app.asar"))).toBe(true);
    const serverJs = path.join(UNPACKED_DIR, "resources", "next-standalone", "server.js");
    expect(existsSync(serverJs)).toBe(true);
    expect(existsSync(path.join(UNPACKED_DIR, "resources", "next-standalone", "public"))).toBe(true);
    expect(
      existsSync(path.join(UNPACKED_DIR, "resources", "next-standalone", ".next", "static")),
    ).toBe(true);
    const staged = JSON.parse(readFileSync(STAGED_MANIFEST, "utf8")) as {
      pins: { serverJsHash: string };
    };
    expect(sha256File(serverJs)).toBe(staged.pins.serverJsHash);
  });

  test("packaged exe is a GUI-subsystem binary (no console window)", () => {
    test.skip(!existsSync(UNPACKED_DIR), "run scripts/build-windows.ps1 first");
    const exe = ["NovelMind.exe", "electron.exe"]
      .map((n) => path.join(UNPACKED_DIR, n))
      .find(existsSync);
    expect(exe, "win-unpacked must contain the app exe").toBeDefined();
    expect(peSubsystem(exe!)).toBe("gui");
  });
});
