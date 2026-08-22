import { defineTool } from "@earendil-works/pi-coding-agent";
import { fastapiConnectorToolCall } from "./fastapi-client.js";
import type { ConnectorRuntimeManifest } from "../skills/loader.js";

export type { ConnectorRuntimeManifest } from "../skills/loader.js";

/** Build only the run-accepted connector tools; no URL or credentials enter Pi. */
export function buildConnectorTools(
  connectors: readonly ConnectorRuntimeManifest[],
  auth: string,
  runNovelId: number,
) {
  return connectors.map((connector) =>
    defineTool({
      name: connector.tool_name,
      label: connector.tool_name,
      description: `受限 HTTPS connector ${connector.tool_name}；URL、版本和 owner 由 FastAPI run proxy 冻结。`,
      parameters: connector.request_schema as never,
      execute: (_toolCallId, params, signal) =>
        fastapiConnectorToolCall(connector.tool_name, params, signal, auth, runNovelId),
    }),
  );
}
