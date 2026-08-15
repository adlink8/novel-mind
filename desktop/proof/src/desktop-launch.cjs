/**
 * Proof-only Electron desktop launcher (Phase 41, D-41-04/D-41-05).
 *
 * Spawns the Next standalone server with the Electron-embedded Node
 * (ELECTRON_RUN_AS_NODE=1), then opens a BrowserWindow that loads it on a
 * loopback port. This is the disposable window smoke that proves the
 * existing renderer runs inside Electron without a browser or user runtime.
 *
 * Disposable: no product UI, no domain/database API, no preload hardening
 * (that is Phase 42 scope).
 */
"use strict";

const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const SERVER_JS = path.join(
  REPO_ROOT,
  "frontend",
  ".next",
  "standalone",
  "server.js"
);
const ELECTRON_EXE = process.execPath; // this Electron binary, re-spawned as embedded Node
const PORT = process.env.NOVELMIND_PROOF_PORT
  ? Number(process.env.NOVELMIND_PROOF_PORT)
  : 39885;

let serverProc = null;
let win = null;

function log(msg) {
  console.log(`[desktop-launch] ${new Date().toISOString()} ${msg}`);
}

function httpReady(url, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve(res.statusCode);
      });
      req.on("error", () => {
        if (Date.now() - started > timeoutMs) reject(new Error("timeout"));
        else setTimeout(tick, 300);
      });
    };
    tick();
  });
}

function spawnStandalone() {
  return new Promise((resolve, reject) => {
    log(`spawning standalone via embedded Node on 127.0.0.1:${PORT}`);
    serverProc = spawn(
      ELECTRON_EXE,
      [SERVER_JS],
      {
        env: {
          ...process.env,
          ELECTRON_RUN_AS_NODE: "1",
          PORT: String(PORT),
          HOSTNAME: "127.0.0.1",
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: false,
      }
    );
    serverProc.stdout.on("data", (d) => log(`[server] ${d.toString().trim()}`));
    serverProc.stderr.on("data", (d) => log(`[server:err] ${d.toString().trim()}`));
    serverProc.on("error", reject);
    serverProc.on("exit", (code) => {
      log(`standalone exited code=${code}`);
      serverProc = null;
    });
    httpReady(`http://127.0.0.1:${PORT}/`, 20000)
      .then((code) => resolve(code))
      .catch(reject);
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "NovelMind Desktop (Phase 41 proof)",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.webContents.on("did-finish-load", () => {
    log(`window loaded ${win.webContents.getURL()}`);
  });
  win.webContents.on("did-fail-load", (_e, code, desc) => {
    log(`window load failed code=${code} desc=${desc}`);
  });
  win.on("closed", () => {
    win = null;
    if (serverProc) {
      log("window closed, killing standalone server");
      serverProc.kill("SIGKILL");
      serverProc = null;
    }
    app.quit();
  });
  win.loadURL(`http://127.0.0.1:${PORT}/`);
}

app.whenReady().then(async () => {
  try {
    const code = await spawnStandalone();
    log(`standalone ready (HTTP ${code}); opening window`);
    createWindow();
  } catch (err) {
    log(`FAILED to start standalone: ${err.message}`);
    app.quit();
    process.exit(1);
  }
});

app.on("window-all-closed", () => app.quit());
