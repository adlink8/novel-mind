import type { ChildProcess } from "node:child_process";
import type { BundledServerHandle } from "./bundled-server";

/**
 * Qualification run state (Phase 45, plan 45-03). Holds the bundled renderer
 * child handle owned by qualification-setup for clean teardown.
 */
export const qualificationState: {
  handle: BundledServerHandle | null;
  child: ChildProcess | null;
} = {
  handle: null,
  child: null,
};
