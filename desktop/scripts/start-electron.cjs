"use strict";

const { spawn } = require("node:child_process");
const path = require("node:path");

const desktopDir = path.resolve(__dirname, "..");
const electronExecutable = require("electron");

const child = spawn(electronExecutable, ["."], {
  cwd: desktopDir,
  env: {
    ...process.env,
    NOVELMIND_RENDERER_DEV_CSP: "1",
  },
  stdio: "inherit",
  windowsHide: false,
});

child.once("error", (error) => {
  console.error(`[desktop] failed to launch Electron: ${error.message}`);
  process.exitCode = 1;
});

child.once("exit", (code) => {
  process.exitCode = code ?? 1;
});
