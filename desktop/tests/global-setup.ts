/**
 * Global setup for the shell smoke suite.
 *
 * 1. Compiles the desktop TypeScript sources to dist/ (tsc emit).
 * 2. Starts the existing Next standalone renderer on an OS-allocated loopback
 *    port using the Electron-embedded Node (ELECTRON_RUN_AS_NODE=1) — the
 *    mechanism proven in Phase 41 prerequisite #1
 *    (desktop/proof/bundled-node-evidence.json).
 * 3. Exposes NOVELMIND_SMOKE_RENDERER_URL to the spec and keeps the child
 *    handle for globalTeardown.
 */
import { execSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import { smokeServerState } from "./smoke-server";

const TESTS_DIR = __dirname;
const DESKTOP_DIR = path.resolve(TESTS_DIR, "..");
const REPO_ROOT = path.resolve(DESKTOP_DIR, "..");
const STANDALONE_SERVER = path.join(REPO_ROOT, "frontend", ".next", "standalone", "server.js");
const ELECTRON_EXE = path.join(DESKTOP_DIR, "node_modules", "electron", "dist", "electron.exe");
const TSC_BIN = path.join(DESKTOP_DIR, "node_modules", "typescript", "bin", "tsc");

function allocateLoopbackPort(): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (address === null || typeof address === "string") {
          reject(new Error("failed to allocate loopback port"));
        } else {
          resolve(address.port);
        }
      });
    });
  });
}

async function waitForHttpReady(url: string, timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(3_000) });
      if (response.status === 200) return;
    } catch {
      // Not ready yet — keep polling.
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`renderer server not ready at ${url} within ${timeoutMs}ms`);
}

export default async function globalSetup(): Promise<void> {
  if (!existsSync(STANDALONE_SERVER)) {
    throw new Error(
      `Next standalone server not found at ${STANDALONE_SERVER} — ` +
        `build it first with desktop/proof/scripts/build-next-standalone.ps1`,
    );
  }
  if (!existsSync(ELECTRON_EXE)) {
    throw new Error(
      `electron executable not found at ${ELECTRON_EXE} — run "npm install" in desktop/ first`,
    );
  }

  // 1. Compile desktop TS → dist/.
  execSync(`node "${TSC_BIN}" -p tsconfig.build.json`, { cwd: DESKTOP_DIR, stdio: "inherit" });

  // 2. Start the existing renderer via Electron-embedded Node on a dynamic loopback port.
  const port = await allocateLoopbackPort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const child = spawn(ELECTRON_EXE, [STANDALONE_SERVER], {
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: "1",
      PORT: String(port),
      HOSTNAME: "127.0.0.1",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: false,
  });
  child.stderr.on("data", (d: Buffer) => process.stderr.write(`[standalone:err] ${d}`));

  try {
    await waitForHttpReady(`${baseUrl}/`);
  } catch (err) {
    child.kill();
    throw err;
  }

  smokeServerState.handle = { child, baseUrl };
  process.env.NOVELMIND_SMOKE_RENDERER_URL = baseUrl;
  console.log(`[setup] renderer ready at ${baseUrl}`);
}
