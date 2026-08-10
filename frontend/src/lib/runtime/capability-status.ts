/**
 * Per-capability offline/provider status (Phase 44, plan 44-03, Task 2).
 *
 * D-44-06 / D-44-07 / D-44-08 + T-44-03-03: capability availability is NOT one
 * global online flag. It is derived from three renderer-safe signals:
 *   1. the runtime readiness/bootstrap from the main process,
 *   2. the REDACTED provider credential status from main (never a value),
 *   3. a single reachability probe — deliberately never treated as universal
 *      internet truth — plus the last provider request outcome.
 *
 * Local capabilities (reader / editor / library / data) stay `available`
 * whenever the local runtime is ready, regardless of network (D-44-06).
 * Provider capabilities (generation / embedding / image) are rendered
 * `unavailable` (no provider configured), `misconfigured` (credential state is
 * decrypt-failed / rotation-needed), or `blocked` (network absent or last
 * request failed) — with an explicit reason, and NEVER an empty-success or
 * fabricated artifact (D-44-07).
 *
 * This module is browser-safe: the only cross-boundary imports are the pure
 * `credential-status` type (erased at compile time) and the desktop capability
 * resolver. It never holds or serializes credentials.
 */
import type { CredentialState } from "../../../../desktop/src/shared/credential-status";
import { desktopCapabilities } from "../desktop/capabilities";

/** Capabilities that work offline because they never leave the local runtime. */
export const LOCAL_CAPABILITY_KINDS = ["reader", "editor", "library", "data"] as const;
export type LocalCapabilityKind = (typeof LOCAL_CAPABILITY_KINDS)[number];

/** Provider-backed capabilities that are honestly blocked when offline. */
export const PROVIDER_CAPABILITY_KINDS = ["generation", "embedding", "image"] as const;
export type ProviderCapabilityKind = (typeof PROVIDER_CAPABILITY_KINDS)[number];

export type ProviderAvailability =
  /** Ready and reachable: the operation may proceed. */
  | "available"
  /** No provider configured (redacted provider state is "unavailable"). */
  | "unavailable"
  /** Explicitly blocked: network absent or the last provider request failed. */
  | "blocked"
  /** Provider material exists but is unusable (decrypt_failed / rotation_needed). */
  | "misconfigured";

export interface ProviderCapabilityState {
  kind: ProviderCapabilityKind;
  availability: ProviderAvailability;
  /** Stable, renderer-safe reason shown to the user. Never a value. */
  reason: string;
  /** Last provider request outcome, if any (never a value). */
  lastRequest: "ok" | "failed" | null;
}

export interface LocalCapabilityState {
  kind: LocalCapabilityKind;
  availability: "available";
}

export interface CapabilityStatus {
  /** Single reachability probe result — not universal internet truth (D-44-07). */
  online: boolean;
  /** Local capabilities are independent of network (D-44-06). */
  local: Readonly<Record<LocalCapabilityKind, LocalCapabilityState>>;
  providers: Readonly<Record<ProviderCapabilityKind, ProviderCapabilityState>>;
}

/** Inputs for the pure derivation (all renderer-safe, never values). */
export interface CapabilityInputs {
  online: boolean;
  /** Redacted provider credential state from main (`bootstrap.credentials.provider`). */
  providerState: CredentialState;
  /** Per-kind last provider request outcome. */
  lastProviderRequest: Readonly<Record<ProviderCapabilityKind, "ok" | "failed" | null>>;
}

const PROVIDER_REASONS: Record<
  Exclude<ProviderAvailability, "available">,
  string
> = {
  unavailable: "未配置 AI 提供商",
  misconfigured: "AI 提供商凭据不可用，请检查凭据设置",
  blocked: "网络不可用，AI 提供商功能已阻断",
};

/**
 * Pure derivation for a single provider capability. Order of precedence:
 * credential state (unavailable / misconfigured) → network (blocked) →
 * last request (blocked) → available. A failure is never converted into an
 * empty success (D-44-07 / T-44-03-03).
 */
export function deriveProviderState(
  kind: ProviderCapabilityKind,
  inputs: Pick<CapabilityInputs, "online" | "providerState" | "lastProviderRequest">,
): ProviderCapabilityState {
  const lastRequest = inputs.lastProviderRequest[kind] ?? null;
  if (inputs.providerState === "unavailable") {
    return { kind, availability: "unavailable", reason: PROVIDER_REASONS.unavailable, lastRequest };
  }
  if (inputs.providerState === "decrypt_failed" || inputs.providerState === "rotation_needed") {
    return { kind, availability: "misconfigured", reason: PROVIDER_REASONS.misconfigured, lastRequest };
  }
  if (!inputs.online) {
    return { kind, availability: "blocked", reason: PROVIDER_REASONS.blocked, lastRequest };
  }
  if (lastRequest === "failed") {
    return {
      kind,
      availability: "blocked",
      reason: "上次 AI 提供商请求失败",
      lastRequest,
    };
  }
  return { kind, availability: "available", reason: "", lastRequest };
}

/** Pure assembly of the full typed capability snapshot from renderer-safe inputs. */
export function deriveCapabilityStatus(inputs: CapabilityInputs): CapabilityStatus {
  const local: Record<LocalCapabilityKind, LocalCapabilityState> = {
    reader: { kind: "reader", availability: "available" },
    editor: { kind: "editor", availability: "available" },
    library: { kind: "library", availability: "available" },
    data: { kind: "data", availability: "available" },
  };
  const providers: Record<ProviderCapabilityKind, ProviderCapabilityState> = {
    generation: deriveProviderState("generation", inputs),
    embedding: deriveProviderState("embedding", inputs),
    image: deriveProviderState("image", inputs),
  };
  return { online: inputs.online, local, providers };
}

/**
 * Per-kind last request outcomes. In-memory only, never persisted, never a
 * value. App code calls `recordProviderRequest` after each provider attempt so
 * a failed attempt is honestly reflected in the capability state.
 */
const providerRequestResults: Record<ProviderCapabilityKind, "ok" | "failed" | null> = {
  generation: null,
  embedding: null,
  image: null,
};

/** Record the outcome of a provider request (redacted signal, never a value). */
export function recordProviderRequest(
  kind: ProviderCapabilityKind,
  result: "ok" | "failed",
): void {
  providerRequestResults[kind] = result;
}

/**
 * Single reachability probe. `navigator.onLine` is a coarse browser hint, never
 * universal internet truth; provider states additionally incorporate credential
 * state and request results (D-44-07). SSR has no network context → assume
 * reachable and let route-time probes/requests refine the state.
 */
export function isOnlineProbe(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine;
}

/**
 * Read the redacted provider credential state from main. Browser mode has no
 * main-owned provider store → "unavailable" (the renderer never guesses about
 * provider configuration it cannot see; T-44-03-03).
 */
export async function readProviderCredentialState(): Promise<CredentialState> {
  const capability = await desktopCapabilities.getBootstrap();
  if (!capability.supported) return "unavailable";
  return capability.value.credentials.provider;
}

/**
 * Live, typed capability status for the current session. Combines the
 * reachability probe, the main-derived redacted provider state and the last
 * provider request outcomes. Never throws: any probe failure degrades to an
 * honest unavailable/blocked state rather than a fabricated success.
 */
export async function getCapabilityStatus(): Promise<CapabilityStatus> {
  const providerState = await readProviderCredentialState();
  return deriveCapabilityStatus({
    online: isOnlineProbe(),
    providerState,
    lastProviderRequest: { ...providerRequestResults },
  });
}
