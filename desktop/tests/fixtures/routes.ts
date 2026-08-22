/**
 * Frozen 13-route inventory for the Electron route-parity suite
 * (Phase 42, Plan 42-03, Task 2 / T-42-03-02).
 *
 * Loads the SAME frozen inventory the Phase 41 proof consumed
 * (`desktop/proof/tests/route-inventory.json`) so Electron parity compares
 * against the identical 13-route surface — no drift, no padding. A missing
 * inventory file fails the suite deterministically.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

export interface RouteFixture {
  path: string;
  params: Record<string, string>;
  markers: string[];
  testIds: string[];
}

export interface RouteGroup {
  id: string;
  label: string;
  routes: RouteFixture[];
}

interface RouteInventory {
  expectedRouteCount: number;
  fixture: { novelId: string; keySceneSetId: string };
  groups: RouteGroup[];
}

const INVENTORY_FILE = path.resolve(
  __dirname,
  "..",
  "..",
  "proof",
  "tests",
  "route-inventory.json",
);

let inventory: RouteInventory;
try {
  inventory = JSON.parse(readFileSync(INVENTORY_FILE, "utf8")) as RouteInventory;
} catch (err) {
  throw new Error(
    `frozen route inventory missing at ${INVENTORY_FILE} — the Electron parity suite cannot run without it`,
    { cause: err },
  );
}

export const EXPECTED_ROUTE_COUNT = inventory.expectedRouteCount;
export const FIXTURE = inventory.fixture;
export const ROUTE_GROUPS = inventory.groups;
export const ALL_ROUTES = inventory.groups.flatMap((group) => group.routes);

/** Renders an inventory path template with its fixture params (e.g. /novels/[id] -> /novels/1). */
export function concretePath(route: RouteFixture): string {
  let out = route.path;
  for (const [key, value] of Object.entries(route.params)) {
    out = out.replace(`[${key}]`, value);
  }
  return out;
}
