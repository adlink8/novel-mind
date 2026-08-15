/**
 * Shared bundled-server launcher for the clean-VM qualification suites
 * (Phase 45, plan 45-03).
 *
 * Starts the BUNDLED next-standalone renderer through the SHIPPED packaged
 * exe's embedded Node (ELECTRON_RUN_AS_NODE=1) on an OS-allocated loopback
 * port — the exact mechanism the packaged runtime adapter uses. Returns the
 * child handle so the owner (global setup or a spec) can terminate it.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";

export interface BundledServerHandle {
  child: ChildProcess;
  baseUrl: string;
  /** Terminate the process tree the bundled server owns (clean shutdown). */
  stop(): Promise<void>;
}

function packagedExeFromEnv(): string {
  const declared = process.env.NOVELMIND_PACKAGED_EXE;
  if (declared === undefined || declared === "") {
    throw new Error("NOVELMIND_PACKAGED_EXE is not set — run via run-qualification.ps1");
  }
  if (!existsSync(declared)) {
    throw new Error(`NOVELMIND_PACKAGED_EXE points at a missing exe: ${declared}`);
  }
  return declared;
}

function bundledServerJs(exe: string): string {
  const candidate = path.join(path.dirname(exe), "resources", "next-standalone", "server.js");
  if (!existsSync(candidate)) {
    throw new Error(`bundled next-standalone/server.js missing at ${candidate}`);
  }
  return candidate;
}

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
  throw new Error(`bundled renderer not ready at ${url} within ${timeoutMs}ms`);
}

function killProcessTree(pid: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
      windowsHide: true,
    });
    killer.once("error", () => resolve());
    killer.once("exit", () => resolve());
  });
}

/** Start the bundled renderer server through the packaged exe's embedded Node. */
export async function startBundledServer(exeOverride?: string): Promise<BundledServerHandle> {
  const exe = exeOverride ?? packagedExeFromEnv();
  const serverJs = bundledServerJs(exe);
  const port = await allocateLoopbackPort();
  const baseUrl = `http://127.0.0.1:${port}`;

  const child = spawn(exe, [serverJs], {
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: "1",
      PORT: String(port),
      HOSTNAME: "127.0.0.1",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  child.stderr.on("data", (d: Buffer) => process.stderr.write(`[bundled-server:err] ${d}`));

  try {
    await waitForHttpReady(`${baseUrl}/`);
  } catch (err) {
    child.kill();
    throw err;
  }

  return {
    child,
    baseUrl,
    stop: async () => {
      if (child.pid === undefined || child.exitCode !== null) return;
      await killProcessTree(child.pid);
    },
  };
}
