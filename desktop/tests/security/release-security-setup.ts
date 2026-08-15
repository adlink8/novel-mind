/**
 * Global setup for the packaged release-security suite (Phase 45, plan 45-04,
 * Task 1).
 *
 * 1. ARTIFACT GATE (checksum-bound, T-45-04-01): the shipped win-unpacked
 *    NovelMind.exe must exist and its SHA-256 must match the qualification
 *    manifest before any test runs.
 * 2. USER-DATA ISOLATION: NOVELMIND_USER_DATA points at a fresh per-run temp
 *    dir so the launched packaged app never touches the developer profile or a
 *    running NovelMind instance (mirrors the clean-VM provisioner).
 * 3. BUNDLED RENDERER: starts the BUNDLED next-standalone tree through the
 *    SHIPPED packaged exe's embedded Node (ELECTRON_RUN_AS_NODE) on a dynamic
 *    loopback port — the exact mechanism the packaged runtime adapter uses.
 *    The spec launches the packaged exe with that URL via `launchShell`.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { startBundledServer } from "../clean-vm/bundled-server";
import { releaseSecurityState } from "./release-security-state";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
const WIN_UNPACKED = path.join(DESKTOP_DIR, "dist", "win-unpacked");
const PACKAGED_EXE = path.join(WIN_UNPACKED, "NovelMind.exe");
const MANIFEST = path.join(
  DESKTOP_DIR,
  "tests",
  "fixtures",
  "qualification-manifest.json",
);

function sha256(file: string): string {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

export default async function globalSetup(): Promise<void> {
  if (!existsSync(PACKAGED_EXE)) {
    throw new Error(
      `packaged exe missing at ${PACKAGED_EXE} — run desktop/scripts/build-windows.ps1 first`,
    );
  }
  if (!existsSync(MANIFEST)) {
    throw new Error(`qualification manifest missing at ${MANIFEST}`);
  }
  const manifestData = JSON.parse(readFileSync(MANIFEST, "utf8")) as {
    artifact: { unpacked: { exe_sha256: string } };
  };
  const exeHash = sha256(PACKAGED_EXE);
  if (exeHash !== manifestData.artifact.unpacked.exe_sha256) {
    throw new Error(
      `packaged exe hash ${exeHash} != manifest ${manifestData.artifact.unpacked.exe_sha256} — artifact does not match the qualification manifest`,
    );
  }

  const userDataDir = mkdtempSync(
    path.join(tmpdir(), "novelmind-release-security-"),
  );
  process.env.NOVELMIND_USER_DATA = userDataDir;
  process.env.NOVELMIND_PACKAGED_EXE = PACKAGED_EXE;
  releaseSecurityState.userDataDir = userDataDir;

  const server = await startBundledServer(PACKAGED_EXE);
  releaseSecurityState.server = server;
  process.env.NOVELMIND_SMOKE_RENDERER_URL = server.baseUrl;
  console.log(`[release-security] bundled renderer ready at ${server.baseUrl}`);
}
