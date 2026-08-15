import type { ChildProcess } from "node:child_process";

/**
 * Process-wide handle for the standalone renderer server owned by
 * globalSetup/globalTeardown (they run in the same Playwright runner process).
 */
export interface SmokeServerHandle {
  child: ChildProcess;
  baseUrl: string;
}

export const smokeServerState: { handle: SmokeServerHandle | null } = { handle: null };
