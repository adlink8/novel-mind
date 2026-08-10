/**
 * Bounded, serializable request/response schemas for every DesktopBridge
 * capability (D-42-05 / T-42-02-03).
 *
 * `desktop/src/shared/bridge-contract.ts` is the typed surface the renderer
 * sees; this module is the enforceable runtime shape for the main process.
 * Every schema is a flat structure validator with no nested wildcards and an
 * explicit maximum length, so a compromised renderer cannot smuggle oversized
 * or structurally-unexpected payloads into handler logic (T-42-02-03 DoS
 * mitigation).
 */

/** Maximum serialized payload length accepted over the bridge (DoS bound). */
export const MAX_IPC_PAYLOAD_BYTES = 4_096;

/** Maximum length of any single string field inside a bridge payload. */
export const MAX_IPC_FIELD_CHARS = 2_048;

/** Schema definition for a single request argument. */
export interface IpcArgSchema {
  name: string;
  kind: "string" | "number" | "boolean";
  required: boolean;
  maxLength?: number;
}

export interface IpcRequestSchema {
  name: string;
  args: readonly IpcArgSchema[];
}

export interface IpcResponseSchema {
  /** Expected JSON shape of a successful response, for documentation/tests. */
  shape: string;
}

/**
 * Validate a decoded request argument against its schema. Never throws on
 * shape mismatch — returns a redacted rejection reason so callers produce a
 * stable error code without leaking payload details.
 */
export function validateArg(
  arg: IpcArgSchema,
  value: unknown,
): { ok: true; value: unknown } | { ok: false; reason: string } {
  if (value === undefined || value === null) {
    return arg.required
      ? { ok: false, reason: `missing required arg "${arg.name}"` }
      : { ok: true, value };
  }
  if (arg.kind === "string") {
    if (typeof value !== "string") {
      return { ok: false, reason: `arg "${arg.name}" is not a string` };
    }
    const max = arg.maxLength ?? MAX_IPC_FIELD_CHARS;
    if (value.length > max) {
      return { ok: false, reason: `arg "${arg.name}" exceeds max length` };
    }
    return { ok: true, value };
  }
  if (typeof value !== arg.kind) {
    return { ok: false, reason: `arg "${arg.name}" is not a ${arg.kind}` };
  }
  return { ok: true, value };
}

/**
 * The bounded schema set. A capability with no request arguments still lists an
 * empty `args` array and every payload is capped at MAX_IPC_PAYLOAD_BYTES.
 */
export const BRIDGE_IPC_SCHEMAS: Record<string, IpcRequestSchema> = {
  "bridge:getRuntimeStatus": {
    name: "getRuntimeStatus",
    args: [],
  },
  "bridge:requestRuntimeRestart": {
    name: "requestRuntimeRestart",
    args: [],
  },
  "bridge:getBootstrap": {
    name: "getBootstrap",
    args: [],
  },
  "bridge:openExternalLink": {
    name: "openExternalLink",
    args: [
      {
        name: "url",
        kind: "string",
        required: true,
        maxLength: MAX_IPC_FIELD_CHARS,
      },
    ],
  },
  "bridge:getLocalAuthToken": {
    name: "getLocalAuthToken",
    args: [
      {
        name: "target",
        kind: "string",
        required: true,
        maxLength: 32,
      },
    ],
  },
};

export const BRIDGE_IPC_RESPONSES: Record<string, IpcResponseSchema> = {
  "bridge:getRuntimeStatus": {
    shape: "DesktopRuntimeStatus (ready/appVersion/electronVersion/security)",
  },
  "bridge:requestRuntimeRestart": {
    shape: "RestartRequestResult ({ok:true} | {ok:false,reason})",
  },
  "bridge:getBootstrap": {
    shape: "DesktopBootstrap (appVersion/bridgeVersion/features/runtime: RuntimeBootstrap|null)",
  },
  "bridge:openExternalLink": {
    shape: "OpenExternalLinkResult ({ok:true} | {ok:false,code,reason})",
  },
  "bridge:getLocalAuthToken": {
    shape: "short-lived audience-bound local session token string | null (never the HMAC secret)",
  },
};
