import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const standaloneRoot = path.join(frontendRoot, ".next", "standalone");

const copies = [
  [path.join(frontendRoot, "public"), path.join(standaloneRoot, "public")],
  [path.join(frontendRoot, ".next", "static"), path.join(standaloneRoot, ".next", "static")],
];

if (!existsSync(path.join(standaloneRoot, "server.js"))) {
  throw new Error("Next standalone server.js is missing; run this script after next build");
}

for (const [source, destination] of copies) {
  if (!existsSync(source)) {
    throw new Error(`Required standalone asset source is missing: ${source}`);
  }
  rmSync(destination, { recursive: true, force: true });
  mkdirSync(path.dirname(destination), { recursive: true });
  cpSync(source, destination, { recursive: true });
}

console.log("Prepared .next/standalone with public and .next/static assets");
