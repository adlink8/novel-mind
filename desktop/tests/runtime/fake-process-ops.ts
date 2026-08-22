/**
 * Fake process operations for the adapter-contract and state-machine suites.
 *
 * Injects deterministic spawn/exit/kill/readiness behavior so the contract suite
 * can assert stable error codes (missing executable, spawn failure, early exit,
 * readiness timeout, kill failure) without touching real OS processes.
 */
import type { ProcessOperations, SpawnedProcess, SpawnOptions } from "../../src/runtime/process-operations";
import type { RuntimeComponent } from "../../src/runtime/types";

/**
 * Stable markers identifying each component's spawn in the dev adapter's
 * launch config (command + args joined). `spawnedProcess(marker)` searches for
 * the marker substring; these are the canonical markers for the five
 * components as launched by DevelopmentProcessAdapter.
 */
export const DEV_ADAPTER_SPAWN_MARKERS: Record<RuntimeComponent, string> = {
  postgres_pgvector: "pgvector/pgvector",
  vector_store: "chromadb/chroma",
  fastapi: "uvicorn",
  agent_service: "start.mjs",
  next: "next",
};

export interface SpawnRecord {
  command: string;
  args: readonly string[];
  options?: SpawnOptions;
  process: FakeProcess;
}

export class FakeProcess implements SpawnedProcess {
  readonly pid: number;
  exitCode: number | null = null;
  /** Invoked by kill(); lets the owning FakeOps simulate graceful drain. */
  onKill: (() => void) | null = null;
  private readonly listeners = new Set<(code: number | null) => void>();
  private exited = false;

  constructor(pid: number) {
    this.pid = pid;
  }

  onExit(listener: (code: number | null) => void): void {
    if (this.exited) {
      queueMicrotask(() => listener(this.exitCode));
      return;
    }
    this.listeners.add(listener);
  }

  kill(): void {
    this.onKill?.();
  }

  emitExit(code: number | null): void {
    if (this.exited) return;
    this.exited = true;
    this.exitCode = code;
    for (const listener of [...this.listeners]) listener(code);
  }
}

export class FakeOps implements ProcessOperations {
  spawned: SpawnRecord[] = [];
  /** Number of killTree invocations (records force-kill events). */
  killTreeCalls = 0;

  existsResult: boolean | ((file: string) => boolean) = true;
  spawnThrows = false;
  /** When set, spawned processes are born already-exited with this code. */
  earlyExitCode: number | null = null;
  probeResult: boolean | ((host: string, port: number, path?: string) => boolean) = true;
  /** true => graceful drain (kill) makes the process exit immediately. */
  drainSucceeds = true;
  /** true => killTree force-kills the process tree. */
  killTreeSucceeds = true;
  sleepMs = 5;

  private nextPid = 1000;
  private readonly pids = new Map<number, FakeProcess>();

  exists(file: string): boolean {
    return typeof this.existsResult === "function" ? this.existsResult(file) : this.existsResult;
  }

  spawn(command: string, args: readonly string[], options?: SpawnOptions): SpawnedProcess {
    if (this.spawnThrows) throw new Error("spawn failed (injected)");
    const process = new FakeProcess(this.nextPid++);
    this.pids.set(process.pid, process);
    process.onKill = () => {
      if (this.drainSucceeds) process.emitExit(0);
    };
    this.spawned.push({ command, args, options, process });
    if (this.earlyExitCode !== null) {
      // Synchronously mark the process exited so waitReady observes it; listener
      // notifications are deferred via queueMicrotask in FakeProcess.
      process.emitExit(this.earlyExitCode);
    }
    return process;
  }

  async waitForExit(process: SpawnedProcess, _timeoutMs: number): Promise<number | null> {
    await this.sleep(this.sleepMs);
    return this.fakeOf(process).exitCode;
  }

  async killTree(pid: number): Promise<void> {
    this.killTreeCalls += 1;
    await this.sleep(this.sleepMs);
    if (this.killTreeSucceeds) {
      this.pids.get(pid)?.emitExit(0);
    }
  }

  async probeHttp(host: string, port: number, path: string, _timeoutMs: number): Promise<boolean> {
    await this.sleep(this.sleepMs);
    return typeof this.probeResult === "function"
      ? this.probeResult(host, port, path)
      : this.probeResult;
  }

  async probeTcp(host: string, port: number, _timeoutMs: number): Promise<boolean> {
    await this.sleep(this.sleepMs);
    return typeof this.probeResult === "function"
      ? this.probeResult(host, port)
      : this.probeResult;
  }

  async allocateLoopbackPort(): Promise<number> {
    return 41000 + this.spawned.length;
  }

  async sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  fakeOf(process: SpawnedProcess): FakeProcess {
    const fake = this.pids.get(process.pid);
    if (fake === undefined) throw new Error(`unknown fake process pid ${process.pid}`);
    return fake;
  }

  /** Last spawned process whose command+args contain the marker (e.g. "next"). */
  spawnedProcess(marker: string): FakeProcess {
    for (let i = this.spawned.length - 1; i >= 0; i -= 1) {
      const record = this.spawned[i];
      if (record === undefined) continue;
      const joined = `${record.command} ${record.args.join(" ")}`;
      if (joined.includes(marker)) return record.process;
    }
    throw new Error(`no spawned process matches marker "${marker}"`);
  }
}

export function createFakeOps(): FakeOps {
  return new FakeOps();
}
