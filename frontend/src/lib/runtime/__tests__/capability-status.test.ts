/**
 * capability-status unit tests (Phase 44, plan 44-03, Task 2).
 *
 * Covers the typed per-capability derivation (T-44-03-03):
 * - local capabilities stay available regardless of network (D-44-06),
 * - provider capabilities derive from provider credential state, reachability
 *   probe and last request result — never one global online flag (D-44-07),
 * - provider failures are never converted into empty-success (D-44-07).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  deriveCapabilityStatus,
  deriveProviderState,
  isOnlineProbe,
} from "../capability-status";

afterEach(() => {
  vi.restoreAllMocks();
});

function baseInputs(overrides: Partial<Parameters<typeof deriveCapabilityStatus>[0]> = {}) {
  return {
    online: true,
    providerState: "available" as const,
    lastProviderRequest: { generation: null, embedding: null, image: null },
    ...overrides,
  };
}

describe("deriveCapabilityStatus", () => {
  it("keeps local capabilities available even when offline (D-44-06)", () => {
    const status = deriveCapabilityStatus(baseInputs({ online: false }));
    expect(status.local.reader.availability).toBe("available");
    expect(status.local.editor.availability).toBe("available");
    expect(status.local.library.availability).toBe("available");
    expect(status.local.data.availability).toBe("available");
  });

  it("derives provider states from the redacted provider credential state", () => {
    const offline = deriveCapabilityStatus(
      baseInputs({ online: false, providerState: "available" }),
    );
    expect(offline.providers.generation).toMatchObject({
      availability: "blocked",
      kind: "generation",
    });
    expect(offline.providers.embedding.availability).toBe("blocked");
    expect(offline.providers.image.availability).toBe("blocked");

    const unconfigured = deriveCapabilityStatus(
      baseInputs({ online: true, providerState: "unavailable" }),
    );
    expect(unconfigured.providers.generation.availability).toBe("unavailable");
    expect(unconfigured.providers.generation.reason).toContain("未配置");

    const misconfigured = deriveCapabilityStatus(
      baseInputs({ online: true, providerState: "decrypt_failed" }),
    );
    expect(misconfigured.providers.image.availability).toBe("misconfigured");
  });

  it("never converts a failed provider request into available (D-44-07)", () => {
    const status = deriveCapabilityStatus(
      baseInputs({
        online: true,
        providerState: "available",
        lastProviderRequest: { generation: "failed", embedding: null, image: "ok" },
      }),
    );
    expect(status.providers.generation.availability).toBe("blocked");
    expect(status.providers.generation.reason).toContain("上次");
    // A successful request keeps the capability available.
    expect(status.providers.image.availability).toBe("available");
  });

  it("flags the reachability probe, not universal internet truth", () => {
    const status = deriveCapabilityStatus(baseInputs({ online: false }));
    expect(status.online).toBe(false);
    expect(status.providers.generation.availability).toBe("blocked");
  });
});

describe("deriveProviderState precedence", () => {
  it("credential unavailability precedes network state", () => {
    const state = deriveProviderState("generation", {
      online: false,
      providerState: "unavailable",
      lastProviderRequest: { generation: "failed", embedding: null, image: null },
    });
    expect(state.availability).toBe("unavailable");
  });

  it("credential misconfiguration precedes network state", () => {
    const state = deriveProviderState("image", {
      online: false,
      providerState: "rotation_needed",
      lastProviderRequest: { generation: null, embedding: null, image: null },
    });
    expect(state.availability).toBe("misconfigured");
  });
});

describe("isOnlineProbe", () => {
  it("assumes reachable when no navigator context exists (SSR)", () => {
    expect(isOnlineProbe()).toBe(true);
  });

  it("mirrors navigator.onLine when present", () => {
    Object.defineProperty(window.navigator, "onLine", {
      value: false,
      configurable: true,
    });
    expect(isOnlineProbe()).toBe(false);
    Object.defineProperty(window.navigator, "onLine", {
      value: true,
      configurable: true,
    });
    expect(isOnlineProbe()).toBe(true);
  });
});
