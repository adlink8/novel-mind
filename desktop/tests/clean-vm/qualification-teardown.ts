import { qualificationState } from "./qualification-state";

/**
 * Qualification teardown (Phase 45, plan 45-03): terminates the bundled
 * renderer process tree started by qualification-setup (owned shutdown).
 */
export default async function globalTeardown(): Promise<void> {
  const handle = qualificationState.handle;
  qualificationState.handle = null;
  qualificationState.child = null;
  if (handle === null) return;
  await handle.stop();
}
