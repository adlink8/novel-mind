/**
 * Shared Electron launch helper for the desktop e2e suites (Phase 45, plan
 * 45-03 UAT).
 *
 * The 42-03 suites launched the dev Electron binary with `args: ["."]` against
 * a renderer URL from globalSetup. The 45-03 qualification runs the SAME specs
 * against the SHIPPED artifact: the packaged `win-unpacked/NovelMind.exe`
 * (selected when NOVELMIND_PACKAGED_EXE is set). Everything else — renderer
 * URL from NOVELMIND_SMOKE_RENDERER_URL, isolated NOVELMIND_USER_DATA — is
 * passed through unchanged so the specs themselves stay identical.
 */
import { test as _test, _electron as electron } from "@playwright/test";
import type { ElectronApplication } from "@playwright/test";
import path from "node:path";
import { existsSync } from "node:fs";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");

/** Path to the packaged exe when NOVELMIND_PACKAGED_EXE is set and exists. */
export function packagedExePath(): string | null {
  const declared = process.env.NOVELMIND_PACKAGED_EXE;
  if (declared === undefined || declared === "") return null;
  if (!existsSync(declared)) {
    throw new Error(`NOVELMIND_PACKAGED_EXE points at a missing exe: ${declared}`);
  }
  return declared;
}

/** Launch the app shell: the packaged exe when selected, otherwise dev Electron. */
export async function launchShell(): Promise<ElectronApplication> {
  const exe = packagedExePath();
  // The specs consume NOVELMIND_SMOKE_RENDERER_URL (set by globalSetup); the
  // app shell reads NOVELMIND_RENDERER_URL. Bridge the two so specs stay
  // identical in dev and packaged runs.
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  const renderer = env.NOVELMIND_SMOKE_RENDERER_URL;
  if (
    (env.NOVELMIND_RENDERER_URL === undefined || env.NOVELMIND_RENDERER_URL === "") &&
    renderer !== undefined &&
    renderer !== ""
  ) {
    env.NOVELMIND_RENDERER_URL = renderer;
  }
  if (exe !== null) {
    return electron.launch({
      executablePath: exe,
      args: [],
      cwd: path.dirname(exe),
      env,
    });
  }
  return electron.launch({
    cwd: DESKTOP_DIR,
    args: ["."],
    env,
  });
}
