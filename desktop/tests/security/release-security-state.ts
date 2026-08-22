/**
 * Process-wide handle for the packaged release-security suite (Phase 45, plan
 * 45-04, Task 1). Mirrors `tests/clean-vm/qualification-state.ts`: the setup
 * owns the bundled renderer child and the isolated user-data dir, the teardown
 * releases them, and the spec launches the packaged exe via the shared
 * `launchShell` seam.
 */
import type { BundledServerHandle } from "../clean-vm/bundled-server";

export const releaseSecurityState: {
  server: BundledServerHandle | null;
  userDataDir: string;
} = {
  server: null,
  userDataDir: "",
};
