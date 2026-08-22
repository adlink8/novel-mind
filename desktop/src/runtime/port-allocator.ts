/**
 * Loopback port allocation (Phase 43, plan 43-02).
 *
 * The managed graph never assumes a fixed port: every component endpoint is
 * allocated from the OS at runtime (D-43-03, topology.ts contract — a nonzero
 * fixed port is rejected by construction). This module is the single owner of
 * that allocation policy:
 *
 * - `allocateLoopbackPort()` asks the OS for a free loopback port (listen(0)).
 * - `PortPool` allocates a batch of mutually distinct ports for one runtime
 *   instance, so "dynamic non-conflicting endpoints" is guaranteed within the
 *   instance even under sequential allocate/close races.
 * - `assertDynamicPort()`/`isDynamicPort()` reject fixed (pre-allocated,
 *   nonzero) ports before they can reach a spawn.
 *
 * The real `nodeProcessOperations` delegates its `allocateLoopbackPort` to this
 * module so the adapters and the graph share one allocation source.
 */
import { createServer } from "node:net";
import { LOOPBACK_HOST } from "./types";

/** A port value that asks the operating system to choose a free one. */
export const DYNAMIC_PORT = 0 as const;

export class PortAllocationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PortAllocationError";
  }
}

/** Whether a value is an integer in the valid port range (0..65535). */
export function isPortNumber(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= 65535
  );
}

/**
 * Whether the port is a dynamic allocation request (0 = OS picks). A fixed
 * nonzero port is a "fixed packaged port" and is rejected by graph/adapter
 * launch paths (topology.ts contract).
 */
export function isDynamicPort(port: number): boolean {
  return port === DYNAMIC_PORT;
}

/** Throws unless the port is a dynamic (0) allocation request. */
export function assertDynamicPort(port: number, context = "port"): void {
  if (!isPortNumber(port)) {
    throw new PortAllocationError(`${context}: invalid port value ${String(port)}`);
  }
  if (!isDynamicPort(port)) {
    throw new PortAllocationError(
      `${context}: fixed port ${port} is rejected; a loopback port must be OS-allocated at runtime`,
    );
  }
}

/** Throws unless the port is a valid loopback port (0 or an allocated value). */
export function assertLoopbackPort(port: number, context = "port"): void {
  if (!isPortNumber(port)) {
    throw new PortAllocationError(`${context}: invalid port value ${String(port)}`);
  }
}

/** Single OS allocation of a free loopback port (port 0 semantics). */
export function allocateLoopbackPort(): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(DYNAMIC_PORT, LOOPBACK_HOST, () => {
      const address = server.address();
      server.close(() => {
        if (address === null || typeof address === "string") {
          reject(new PortAllocationError("failed to allocate a loopback port"));
        } else {
          resolve(address.port);
        }
      });
    });
  });
}

export interface PortAllocation {
  /** The OS-allocated loopback port. Always > 0 after allocation. */
  port: number;
}

/**
 * Batch allocator that guarantees mutually distinct ports for one runtime
 * instance. Sequential allocate/close can race with other processes; the pool
 * re-allocates until it holds `count` unique values.
 */
export class PortPool {
  private readonly allocated = new Set<number>();

  /** Allocate one distinct loopback port (retried on collision). */
  async allocate(): Promise<number> {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const port = await allocateLoopbackPort();
      if (!this.allocated.has(port)) {
        this.allocated.add(port);
        return port;
      }
    }
    throw new PortAllocationError("could not allocate a distinct loopback port");
  }

  /** Allocate `count` mutually distinct loopback ports. */
  async allocateMany(count: number): Promise<readonly number[]> {
    if (!Number.isInteger(count) || count < 0) {
      throw new PortAllocationError(`invalid allocation count ${String(count)}`);
    }
    const ports: number[] = [];
    for (let i = 0; i < count; i += 1) {
      ports.push(await this.allocate());
    }
    return ports;
  }

  /** Whether the pool allocated this exact port (instance-bound ownership). */
  owns(port: number): boolean {
    return this.allocated.has(port);
  }

  /** Release a port back to the pool. Idempotent. */
  release(port: number): void {
    this.allocated.delete(port);
  }

  get size(): number {
    return this.allocated.size;
  }
}
