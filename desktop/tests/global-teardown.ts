/**
 * Global teardown for the shell smoke suite: terminates the standalone renderer
 * process tree started by globalSetup (owned shutdown).
 */
import { spawn } from "node:child_process";
import { smokeServerState } from "./smoke-server";

function killProcessTree(pid: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
      windowsHide: true,
    });
    killer.once("error", () => resolve());
    killer.once("exit", () => resolve());
  });
}

export default async function globalTeardown(): Promise<void> {
  const handle = smokeServerState.handle;
  smokeServerState.handle = null;
  if (handle === null || handle.child.pid === undefined) return;
  if (handle.child.exitCode === null && handle.child.signalCode === null) {
    await killProcessTree(handle.child.pid);
  }
}
