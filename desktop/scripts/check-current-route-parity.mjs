import { existsSync, readFileSync, readdirSync } from "node:fs";
import { createServer } from "node:net";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const appRoot = path.join(repoRoot, "frontend", "src", "app");
const standaloneRoot = path.join(repoRoot, "frontend", ".next", "standalone");
const inventoryPath = path.join(repoRoot, "desktop", "tests", "fixtures", "route-inventory.json");
const inventory = JSON.parse(readFileSync(inventoryPath, "utf8"));

function discoverRoutes(directory, prefix = "") {
  const routes = [];
  for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name),
  )) {
    if (entry.name.startsWith("_") || entry.name.startsWith(".")) continue;
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) routes.push(...discoverRoutes(fullPath, `${prefix}/${entry.name}`));
    if (entry.isFile() && (entry.name === "page.tsx" || entry.name === "page.ts")) {
      routes.push(prefix || "/");
    }
  }
  return routes;
}

function concretePath(route) {
  let result = route.path;
  for (const [key, value] of Object.entries(route.params)) {
    result = result.replace(`[${key}]`, value);
  }
  return result;
}

function allocatePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() =>
        typeof address === "object" && address ? resolve(address.port) : reject(new Error("No port")),
      );
    });
  });
}

async function waitForServer(baseUrl) {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(baseUrl, { signal: AbortSignal.timeout(3_000) });
      if (response.status === 200) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`Standalone server did not become ready at ${baseUrl}`);
}

const discovered = discoverRoutes(appRoot).sort();
const routes = inventory.groups.flatMap((group) => group.routes);
const frozen = routes.map((route) => route.path).sort();
if (inventory.expectedRouteCount !== routes.length || JSON.stringify(discovered) !== JSON.stringify(frozen)) {
  throw new Error(
    `Route inventory drift: discovered=${JSON.stringify(discovered)} inventory=${JSON.stringify(frozen)}`,
  );
}

const serverJs = path.join(standaloneRoot, "server.js");
if (!existsSync(serverJs)) throw new Error(`Standalone server missing: ${serverJs}`);

const port = await allocatePort();
const baseUrl = `http://127.0.0.1:${port}`;
const child = spawn(process.execPath, ["server.js"], {
  cwd: standaloneRoot,
  env: { ...process.env, HOSTNAME: "127.0.0.1", PORT: String(port) },
  stdio: "inherit",
});

try {
  await waitForServer(baseUrl);
  const targets = ["/icons/icon-192.png", "/sw.js", ...routes.map(concretePath)];
  for (const target of targets) {
    const response = await fetch(`${baseUrl}${target}`, { redirect: "manual" });
    if (response.status !== 200) throw new Error(`${target} returned HTTP ${response.status}`);
  }
  console.log(`Current standalone route parity PASS (${routes.length} routes + static assets)`);
} finally {
  child.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ]);
}
