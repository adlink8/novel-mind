/**
 * Global teardown for the packaged release-security suite (Phase 45, plan
 * 45-04, Task 1). Stops the bundled renderer child it owns and best-effort
 * removes the isolated per-run user-data dir.
 */
import { rmSync } from "node:fs";
import { releaseSecurityState } from "./release-security-state";

export default async function globalTeardown(): Promise<void> {
  if (releaseSecurityState.server !== null) {
    await releaseSecurityState.server.stop();
    releaseSecurityState.server = null;
  }
  const dir = releaseSecurityState.userDataDir;
  if (dir !== "") {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // Best effort — a just-closed Electron child can briefly hold the dir.
    }
  }
}
