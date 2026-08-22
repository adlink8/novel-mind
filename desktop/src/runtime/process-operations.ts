/**
 * Injected process operations (Phase 43, plan 43-01).
 *
 * The adapters never touch `node:child_process` directly: every spawn/exit/kill
 * and readiness probe flows through this seam so the adapter-contract suite can
 * inject deterministic fakes (spawn failure, early exit, never-ready, kill
 * failure) without spawning real OS processes. The real implementation is
 * `nodeProcessOperations`; a fake is provided by the tests.
 *
 * Readiness is defined as "the component's owned readiness probe succeeds", NOT
 * "a port happens to be open". Port-open alone is never sufficient evidence
 * (D-43-03); protocol-level probing lands in plan 43-02.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createConnection } from "node:net";
import { allocateLoopbackPort } from "./port-allocator";

export interface SpawnedProcess {
  readonly pid: number;
  exitCode: number | null;
  onExit(listener: (code: number | null) => void): void;
  kill(): void;
}

export interface SpawnOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  windowsHide?: boolean;
}

export interface ProcessOperations {
  exists(filePath: string): boolean;
  spawn(command: string, args: readonly string[], options?: SpawnOptions): SpawnedProcess;
  /** Resolves with the exit code, or null when the process has not exited within timeoutMs. */
  waitForExit(process: SpawnedProcess, timeoutMs: number): Promise<number | null>;
  /** Force-kills the whole Windows process tree (taskkill /T /F). */
  killTree(pid: number): Promise<void>;
  probeHttp(host: string, port: number, path: string, timeoutMs: number): Promise<boolean>;
  probeTcp(host: string, port: number, timeoutMs: number): Promise<boolean>;
  allocateLoopbackPort(): Promise<number>;
  sleep(ms: number): Promise<void>;
}

function childExited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null;
}

class NodeSpawnedProcess implements SpawnedProcess {
  constructor(private readonly child: ChildProcess) {}

  get pid(): number {
    return this.child.pid ?? 0;
  }

  get exitCode(): number | null {
    return this.child.exitCode;
  }

  onExit(listener: (code: number | null) => void): void {
    if (childExited(this.child)) {
      queueMicrotask(() => listener(this.child.exitCode));
      return;
    }
    this.child.once("exit", (code) => listener(code));
  }

  kill(): void {
    if (!childExited(this.child)) this.child.kill();
  }
}

export function nodeProcessOperations(): ProcessOperations {
  return {
    exists: (filePath) => existsSync(filePath),

    spawn: (command, args, options) => {
      const child = spawn(command, args, options);
      return new NodeSpawnedProcess(child);
    },

    waitForExit: (process, timeoutMs) =>
      new Promise<number | null>((resolve) => {
        if (process.exitCode !== null) {
          resolve(process.exitCode);
          return;
        }
        let settled = false;
        const timer = setTimeout(() => {
          if (!settled) {
            settled = true;
            resolve(null);
          }
        }, timeoutMs);
        process.onExit((code) => {
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve(code);
          }
        });
      }),

    killTree: (pid) =>
      new Promise<void>((resolve) => {
        const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
          windowsHide: true,
        });
        killer.once("error", () => resolve());
        killer.once("exit", () => resolve());
      }),

    probeHttp: async (host, port, path, timeoutMs) => {
      try {
        const response = await fetch(`http://${host}:${port}${path}`, {
          signal: AbortSignal.timeout(timeoutMs),
        });
        return response.ok;
      } catch {
        return false;
      }
    },

    probeTcp: (host, port, timeoutMs) =>
      new Promise<boolean>((resolve) => {
        const socket = createConnection({ host, port });
        const timer = setTimeout(() => {
          socket.destroy();
          resolve(false);
        }, timeoutMs);
        socket.once("connect", () => {
          clearTimeout(timer);
          socket.destroy();
          resolve(true);
        });
        socket.once("error", () => {
          clearTimeout(timer);
          socket.destroy();
          resolve(false);
        });
      }),

    // Delegate to the canonical port allocator (plan 43-02): OS-allocated
    // loopback port, fixed ports rejected by construction.
    allocateLoopbackPort: () => allocateLoopbackPort(),

    sleep: (ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
  };
}
