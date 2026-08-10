/**
 * Clean-VM qualification global setup (Phase 45, plan 45-03).
 *
 * Starts the BUNDLED renderer server through the SHIPPED packaged exe's
 * embedded Node (ELECTRON_RUN_AS_NODE=1) — the exact mechanism the packaged
 * runtime adapter uses — on an OS-allocated loopback port, then exposes:
 *  - NOVELMIND_SMOKE_RENDERER_URL (renderer base URL for the e2e specs),
 *  - NOVELMIND_PACKAGED_EXE (the packaged exe the specs must launch).
 *
 * The child handle is kept for globalTeardown so the qualification run owns and
 * terminates the process tree it created (clean shutdown, D-45-02).
 */
import { startBundledServer } from "./bundled-server";
import { qualificationState } from "./qualification-state";

export default async function globalSetup(): Promise<void> {
  const handle = await startBundledServer();
  qualificationState.handle = handle;
  process.env.NOVELMIND_SMOKE_RENDERER_URL = handle.baseUrl;
  console.log(
    `[qualification-setup] bundled renderer ready at ${handle.baseUrl} ` +
      `(exe=${process.env.NOVELMIND_PACKAGED_EXE})`,
  );
}
