/**
 * PackagedProcessAdapter (D-43-02, T-43-01-01).
 *
 * Launches ONLY the Phase 41 approved bundled path: the Next standalone tree
 * run through the Electron-embedded Node (ELECTRON_RUN_AS_NODE=1) — proven in
 * `desktop/proof/bundled-node-evidence.json`.
 *
 * Every other component — bundled Python FastAPI, bundled PostgreSQL, bundled
 * vector store — is part of the Phase 41 NO-GO scope (only Next standalone +
 * Electron-embedded Node are proven) and FAILS CLOSED here with
 * UNSUPPORTED_IN_PACKAGED. This adapter never falls back to PATH executables or
 * Docker, never reads spawn arguments from unvalidated input (bounded static
 * args only), and never writes outside the configured paths.
 *
 * The full packaged graph cannot reach `ready` until the unproven bundled
 * runtimes land in a later plan; that boundary is recorded honestly in the
 * 43-01 SUMMARY alongside the Phase 41 NO-GO record.
 */
import path from "node:path";
import {
  LOOPBACK_HOST,
  RUNTIME_ERROR_CODES,
  RuntimeError,
  type AdapterBudgets,
  type ComponentLaunch,
  type RuntimeComponent,
} from "./types";
import { BaseProcessAdapter } from "./base-process-adapter";
import type { ProcessOperations } from "./process-operations";

export interface PackagedPaths {
  /** Absolute Electron executable used as the embedded Node runtime. */
  electronExe: string;
  /** Absolute path to the bundled Next standalone server.js. */
  nextStandaloneServerJs: string;
}

/** The only component with a Phase 41 approved bundled launch path. */
const PACKAGED_LAUNCHABLE: readonly RuntimeComponent[] = ["next"];

export class PackagedProcessAdapter extends BaseProcessAdapter {
  readonly mode = "packaged" as const;
  readonly launchable: readonly RuntimeComponent[] = PACKAGED_LAUNCHABLE;

  private readonly paths: PackagedPaths;

  constructor(
    ops: ProcessOperations,
    budgets?: Partial<AdapterBudgets>,
    paths?: Partial<PackagedPaths>,
  ) {
    super(ops, budgets);
    this.paths = {
      electronExe: paths?.electronExe ?? process.execPath,
      nextStandaloneServerJs: paths?.nextStandaloneServerJs ?? "",
    };
  }

  protected launchConfig(component: RuntimeComponent): ComponentLaunch {
    if (component !== "next") {
      // Defensive: base.start already rejects non-launchable components via
      // `launchable`; this keeps the fail-closed contract explicit.
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.UNSUPPORTED_IN_PACKAGED,
        `no approved bundled path for ${component}`,
        component,
      );
    }
    const serverJs = this.paths.nextStandaloneServerJs;
    if (serverJs === "") {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.EXECUTABLE_NOT_FOUND,
        "next standalone server.js not configured",
        "next",
      );
    }
    return {
      command: this.paths.electronExe,
      args: [serverJs],
      cwd: path.dirname(serverJs),
      env: { ELECTRON_RUN_AS_NODE: "1", HOSTNAME: LOOPBACK_HOST },
      portVia: { kind: "env", name: "PORT" },
      probe: { transport: "http", path: "/" },
    };
  }
}
