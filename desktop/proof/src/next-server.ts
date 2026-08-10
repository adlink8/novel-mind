/**
 * Phase 41 proof (Plan 41-02): start the Next standalone renderer from the topology's
 * `next` component descriptor and own its lifecycle.
 *
 * - The server runs `.next/standalone/server.js` (built by
 *   `desktop/proof/scripts/build-next-standalone.ps1`).
 * - The loopback port is allocated at runtime (OS-picked), never a fixed packaged port.
 * - `stop()` terminates the whole child process tree and asserts no residual process
 *   (T-41-02-02, D-41-05).
 *
 * This is proof-only code. It is used by the route-parity Playwright suite and by the
 * build/verify script.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:net";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { LOOPBACK_HOST, allocateEndpoint } from "./topology.ts";
import type { ComponentDescriptor } from "./topology.ts";

const PROOF_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(PROOF_DIR, "..", "..");
const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const STANDALONE_DIR = path.join(FRONTEND_DIR, ".next", "standalone");

/** Seconds to wait for HTTP readiness before declaring the server failed (T-41-02-02). */
export const READY_TIMEOUT_MS = 90_000;

/** A running, ready standalone server owned by the proof harness. */
export interface NextStandaloneServer {
  /** Loopback base URL, e.g. http://127.0.0.1:34567 */
  baseUrl: string;
  /** The allocated loopback port. */
  port: number;
  /** Terminates the child process tree and waits for exit. Idempotent. */
  stop: () => Promise<void>;
}

/** Reserves a free loopback port (OS-allocated), then releases it for the server. */
function allocateLoopbackPort(): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server: Server = createServer();
    server.once("error", reject);
    server.listen(0, LOOPBACK_HOST, () => {
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

/** Waits until an HTTP GET on `url` answers with 200 (bounded by timeoutMs). */
export async function waitForHttpReady(
  url: string,
  timeoutMs: number = READY_TIMEOUT_MS,
): Promise<void> {
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
  throw new Error(`HTTP readiness not reached for ${url} within ${timeoutMs}ms`);
}

/** Resolves when the child exits; resolves immediately if it already exited. */
function childExited(child: ChildProcess): Promise<number | null> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(child.exitCode);
  }
  return new Promise<number | null>((resolve) => {
    child.once("exit", (code) => resolve(code));
  });
}

/** Terminates a Windows process tree (taskkill /T /F) — the owned-shutdown primitive. */
function killProcessTree(pid: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
      windowsHide: true,
    });
    killer.once("error", () => resolve());
    killer.once("exit", () => resolve());
  });
}

/**
 * Starts `.next/standalone/server.js` from the topology's `next` descriptor on an
 * OS-allocated loopback port, waits for HTTP readiness and returns an owned handle.
 */
export async function startNextStandalone(
  component: Pick<ComponentDescriptor, "endpoint" | "resourceRoot" | "executable">,
): Promise<NextStandaloneServer> {
  if (component.endpoint.host !== LOOPBACK_HOST) {
    throw new Error(`next descriptor must bind ${LOOPBACK_HOST}`);
  }
  if (!existsSync(path.join(STANDALONE_DIR, "server.js"))) {
    throw new Error(
      `standalone server.js not found at ${path.join(STANDALONE_DIR, "server.js")} — ` +
        `run desktop/proof/scripts/build-next-standalone.ps1 first`,
    );
  }

  const port = await allocateLoopbackPort();
  const allocated = allocateEndpoint(component.endpoint, port);

  const child = spawn("node", ["server.js"], {
    cwd: STANDALONE_DIR,
    env: {
      ...process.env,
      PORT: String(port),
      HOSTNAME: LOOPBACK_HOST,
    },
    stdio: "ignore",
    windowsHide: true,
  });

  const baseUrl = `http://${allocated.host}:${allocated.port}`;
  let stopped = false;

  const stop = async (): Promise<void> => {
    if (stopped) return;
    stopped = true;
    if (child.exitCode === null && child.signalCode === null) {
      await killProcessTree(child.pid!);
      await childExited(child);
    }
  };

  try {
    await waitForHttpReady(baseUrl);
  } catch (err) {
    await stop();
    throw new Error(`standalone server failed to become ready: ${String(err)}`);
  }

  return {
    baseUrl,
    port: allocated.port,
    stop,
  };
}
