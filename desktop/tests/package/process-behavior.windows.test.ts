/**
 * Single-instance + clean process behavior (plan 45-01, Task 3/4, D-45-02).
 *
 * Part A (all platforms) unit-tests the pure `focusMainWindow` decision logic of
 * `desktop/src/main/single-instance.ts`.
 *
 * Part B (Windows only) launches the REAL app twice — against the packaged
 * win-unpacked exe when a build exists, otherwise the dev Electron binary with a
 * compiled shell — and asserts the T-45-01-02 contract:
 *   - the primary instance creates exactly one window and stays alive;
 *   - a second launch sharing the same userData root exits immediately (exit 0)
 *     WITHOUT creating a second window or a second runtime graph;
 *   - after a clean primary exit the lock is released (a later launch with the
 *     same userData becomes primary).
 *
 * Both instances share the NOVELMIND_USER_DATA override (a per-run temp dir) so
 * the single-instance lock collides deterministically and never touches the real
 * `%APPDATA%/NovelMind`. NOVELMIND_RENDERER_URL points both at a local loopback
 * smoke server so no runtime graph is started in this suite.
 */
import { test, expect, _electron as electron } from "@playwright/test";
import type { ElectronApplication } from "@playwright/test";
import { spawn, execSync, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { focusMainWindow } from "../../src/main/single-instance";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
const UNPACKED_DIR = path.join(DESKTOP_DIR, "dist", "win-unpacked");
const IS_WINDOWS = process.platform === "win32";

interface LaunchConfig {
  exePath: string;
  args: string[];
  cwd: string;
}

let smokeServer: Server | null = null;
let baseUrl = "";
let userDataDir = "";
let launch: LaunchConfig = { exePath: "", args: [], cwd: "" };
let primaryApp: ElectronApplication | null = null;

function packagedExe(): string | null {
  for (const name of ["NovelMind.exe", "electron.exe"]) {
    const p = path.join(UNPACKED_DIR, name);
    if (existsSync(p)) return p;
  }
  return null;
}

function sharedEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  env.NOVELMIND_RENDERER_URL = baseUrl;
  env.NOVELMIND_USER_DATA = userDataDir;
  return env;
}

function waitForExit(child: ChildProcess, timeoutMs: number): Promise<number | null> {
  return new Promise<number | null>((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        resolve(null);
      }
    }, timeoutMs);
    child.once("exit", (code) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(code);
      }
    });
    child.once("error", () => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolve(null);
      }
    });
  });
}

function killTree(pid: number): void {
  spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
}

test.beforeAll(async () => {
  // Tiny loopback smoke server: a second NovelMind instance is not allowed to
  // resolve anything outside the approved origin.
  const server = createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end("<!doctype html><title>NovelMind</title><h1>single-instance smoke</h1>");
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  smokeServer = server;
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("smoke server did not bind a loopback port");
  }
  baseUrl = `http://127.0.0.1:${address.port}`;
  userDataDir = mkdtempSync(path.join(os.tmpdir(), "novelmind-si-"));

  const packaged = packagedExe();
  if (packaged !== null) {
    launch = { exePath: packaged, args: [], cwd: UNPACKED_DIR };
  } else {
    // Dev fallback: always recompile the shell so dist/ is never stale relative
    // to the current src (the single-instance wiring in src/main/index.ts is
    // load-bearing for this suite).
    execSync(
      `node "${path.join(DESKTOP_DIR, "node_modules", "typescript", "bin", "tsc")}" -p tsconfig.build.json`,
      { cwd: DESKTOP_DIR, stdio: "inherit" },
    );
    const distMain = path.join(DESKTOP_DIR, "dist", "main", "index.js");
    if (!existsSync(distMain)) {
      throw new Error("tsc build produced no dist/main/index.js");
    }
    launch = {
      exePath: path.join(DESKTOP_DIR, "node_modules", "electron", "dist", "electron.exe"),
      args: ["."],
      cwd: DESKTOP_DIR,
    };
  }

  if (IS_WINDOWS) {
    primaryApp = await electron.launch({
      executablePath: launch.exePath,
      args: launch.args,
      cwd: launch.cwd,
      env: sharedEnv(),
    });
  }
});

test.afterAll(async () => {
  if (primaryApp !== null) {
    await primaryApp.close().catch(() => undefined);
    primaryApp = null;
  }
  if (smokeServer !== null) {
    smokeServer.close();
    smokeServer = null;
  }
  if (userDataDir !== "") {
    // The just-closed Electron child may still hold the userData dir briefly;
    // retry a few times before giving up (best-effort temp cleanup).
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        rmSync(userDataDir, { recursive: true, force: true });
        break;
      } catch {
        await new Promise((r) => setTimeout(r, 500));
      }
    }
    userDataDir = "";
  }
});

test.describe("focusMainWindow — pure decision logic (all platforms)", () => {
  interface FakeWin {
    destroyed: boolean;
    minimized: boolean;
    restoreCalls: number;
    focusCalls: number;
  }

  function makeFakeWin(opts: Partial<FakeWin> = {}): FakeWin {
    return { destroyed: false, minimized: false, restoreCalls: 0, focusCalls: 0, ...opts };
  }

  function adapter(win: FakeWin) {
    return {
      isDestroyed: () => win.destroyed,
      isMinimized: () => win.minimized,
      restore: () => {
        win.restoreCalls += 1;
      },
      focus: () => {
        win.focusCalls += 1;
      },
    };
  }

  test("null window is a no-op", () => {
    expect(focusMainWindow(null)).toBe(false);
  });

  test("destroyed window is a no-op", () => {
    const win = makeFakeWin({ destroyed: true });
    expect(focusMainWindow(adapter(win))).toBe(false);
    expect(win.restoreCalls).toBe(0);
    expect(win.focusCalls).toBe(0);
  });

  test("minimized window is restored then focused", () => {
    const win = makeFakeWin({ minimized: true });
    expect(focusMainWindow(adapter(win))).toBe(true);
    expect(win.restoreCalls).toBe(1);
    expect(win.focusCalls).toBe(1);
  });

  test("normal window is focused without restore", () => {
    const win = makeFakeWin();
    expect(focusMainWindow(adapter(win))).toBe(true);
    expect(win.restoreCalls).toBe(0);
    expect(win.focusCalls).toBe(1);
  });
});

test.describe("single-instance process behavior (Windows)", () => {
  test("primary instance creates exactly one window and stays alive", async () => {
    test.skip(!IS_WINDOWS, "Windows-only packaged process behavior");
    expect(primaryApp, "primary app must be running").not.toBeNull();
    const page = await primaryApp!.firstWindow();
    await page.waitForLoadState("domcontentloaded");
    await expect.poll(() => page.title()).toContain("NovelMind");
    const winCount = await primaryApp!.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().length,
    );
    expect(winCount).toBe(1);
  });

  test("second launch with the same userData exits without a second window", async () => {
    test.skip(!IS_WINDOWS, "Windows-only packaged process behavior");
    expect(primaryApp, "primary app must be running").not.toBeNull();
    const child = spawn(launch.exePath, launch.args, {
      cwd: launch.cwd,
      env: sharedEnv(),
      stdio: "ignore",
    });
    try {
      // The duplicate must exit immediately (app.exit(0)) — it never reaches
      // whenReady, so no runtime graph and no second window.
      const code = await waitForExit(child, 20_000);
      expect(code).toBe(0);
    } finally {
      if (child.exitCode === null && child.pid !== undefined) killTree(child.pid);
    }
    const winCount = await primaryApp!.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().length,
    );
    expect(winCount).toBe(1);
  });

  test("clean primary exit releases the lock (a later launch becomes primary)", async () => {
    test.skip(!IS_WINDOWS, "Windows-only packaged process behavior");
    expect(primaryApp, "primary app must be running").not.toBeNull();
    await primaryApp!.close();
    primaryApp = null;

    const child = spawn(launch.exePath, launch.args, {
      cwd: launch.cwd,
      env: sharedEnv(),
      stdio: "ignore",
    });
    try {
      // As the new primary it must stay alive — waitForExit returns null on timeout.
      const code = await waitForExit(child, 5_000);
      expect(code).toBeNull();
    } finally {
      if (child.pid !== undefined) killTree(child.pid);
    }
  });
});
