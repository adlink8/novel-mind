/**
 * Phase 39-03 derivative export API client (D-39-03 / REQ-FORK-05 / REQ-CRE-07).
 *
 * Mirrors the owner-scoped envelopes the export panel consumes:
 *
 * - the approved `approve_export` ApprovalRequest (via the agent runtime
 *   `/agent/approval-requests` read surface) carries the `payload_summary`
 *   binding artifact_id / artifact_revision_id / project_id / manifest_hash —
 *   the browser never guesses or assembles these;
 * - the ExportPreparationArtifact read envelope
 *   (`/agent/novels/{novelId}/artifacts/{artifactId}` + revisions) carries the
 *   frozen `preparation` payload whose `content_hash` is the manifest checksum;
 * - the three-dimension audit (`/novels/{novelId}/derivative-projects/{projectId}/export/audit`)
 *   is the ONLY source of quality status — a completed download is never a
 *   quality pass;
 * - export is requested ONLY through the deterministic `agent/materialize`
 *   route of an approved artifact; the browser never assembles a manifest or
 *   selects a live revision;
 * - the read-only `download` bytes carry `X-Export-Manifest-Hash` /
 *   `X-Export-Snapshot-Hash` so the browser can verify the file it received
 *   replays the approved artifact's frozen manifest checksum.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (backend read envelopes; scope always comes from the novel path)
// ---------------------------------------------------------------------------

export type DerivativeExportFormat = "markdown" | "epub";

export type DerivativeAuditStatus = "verified" | "partial" | "blocked";

export type DerivativeAuditDimensionKind =
  | "implementation_readiness"
  | "sample_data_coverage"
  | "quality_qualification";

export interface DerivativeApprovalPayloadSummary {
  project_id?: number;
  project_key?: string;
  fork_id?: number;
  fork?: string | null;
  branch?: string | null;
  artifact_id?: number;
  artifact_revision_id?: number;
  snapshot_hash?: string;
  manifest_hash?: string;
  approval_note?: string | null;
}

/** One `approve_export` ApprovalRequest read row (agent runtime surface). */
export interface DerivativeApprovalRequestView {
  id: number;
  owner_id: number;
  action: string;
  payload_summary: DerivativeApprovalPayloadSummary;
  status: string;
  created_at: string;
  decided_at: string | null;
  expires_at: string | null;
}

export interface AgentArtifactView {
  id: number;
  owner_id: number;
  novel_id: number;
  type: string;
  schema_version: string;
  status: "candidate" | "validated" | "approved" | "published" | "rejected";
  branch: string | null;
  input_hash: string;
  current_revision_id: number | null;
}

export interface AgentArtifactRevisionView {
  id: number;
  artifact_id: number;
  owner_id: number;
  novel_id: number;
  revision_no: number;
  content: Record<string, unknown>;
}

/** Frozen `ExportPreparationPayload` (mirror of the backend wire contract). */
export interface ExportPreparationPayload {
  schema_version: string;
  artifact_kind: "export_preparation";
  authority_space: "derivative";
  fork: string;
  project_id: number;
  project_key: string;
  source_snapshot: {
    source_snapshot_id: string;
    source_snapshot_hash: string;
    source_manifest_hash: string;
    cutoff_chapter: number;
  };
  base_revision: {
    project_manifest_hash: string;
    scope_hash: string;
    cutoff_snapshot_hash: string;
    text_version_hash: string;
  };
  /** The frozen manifest/snapshot checksum the artifact is bound to. */
  content_hash: string;
  evidence_refs: string[];
  generator_lineage: Record<string, unknown>;
  validator_report: Record<string, unknown>;
  review_state: "candidate";
  approval_request_id: number | null;
  materialize_lineage: Record<string, unknown>;
}

export interface DerivativeExportAuditEvidence {
  kind: string;
  location: string;
  detail: string;
}

export interface DerivativeExportAuditDimension {
  dimension: DerivativeAuditDimensionKind;
  status: DerivativeAuditStatus;
  blocked_reasons: string[];
  evidence: DerivativeExportAuditEvidence[];
}

/** Three-dimension audit report (the ONLY quality source for the panel). */
export interface DerivativeExportAuditReport {
  schema_version: string;
  audit_version: string;
  owner_id: number;
  novel_id: number;
  project_id: number;
  snapshot_hash: string;
  dimensions: DerivativeExportAuditDimension[];
  verdict: "qualified_candidate" | "blocked";
  blocked_reasons: string[];
  report_hash: string;
  phase22: {
    green_observed: number;
    green_required: number;
    source: string;
    source_hash: string;
  };
}

/** Read-only deterministic agent/prepare envelope (counts + preparation_hash). */
export interface DerivativeExportAgentPrepareResponse {
  preparation: Record<string, unknown>;
  preparation_hash: string;
  snapshot_hash: string;
  manifest_hash: string;
  schema_version: string;
  export_version: string;
  project_id: number;
  fork_id: number;
  chapter_count: number;
  asset_count: number;
  revision_count: number;
  citation_count: number;
  candidate_only: boolean;
}

export interface DerivativeExportMaterializeBody {
  branch?: string | null;
  fork?: string | null;
  /** Approved ExportPreparationArtifact refs; the server re-verifies them. */
  artifact_id: number;
  artifact_revision_id: number;
  /** The approved `approve_export` ApprovalRequest id. */
  approval_id: number;
  /** Frozen preparation hash; a forged/stale value fails closed. */
  preparation_hash: string;
  reason?: string | null;
}

export interface DerivativeExportMaterializeResponse {
  owner_id: number;
  novel_id: number;
  project_id: number;
  fork_id: number;
  artifact_id: number;
  artifact_revision_id: number;
  approval_request_id: number;
  approval_action: string;
  approval_status: string;
  preparation_hash: string;
  snapshot_hash: string;
  manifest_hash: string;
  package_hash: string;
  package_schema_version: string;
  bundle_size: number;
  bundle_formats: string[];
  status: string;
  candidate_only: boolean;
  materialized: boolean;
}

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

export const derivativeExportApi = {
  /** Approved/pending `approve_export` approval rows (owner-scoped). */
  listApprovalRequests: () =>
    api.get<{ items: DerivativeApprovalRequestView[]; total: number; skip: number; limit: number }>(
      "/agent/approval-requests",
      { params: { skip: 0, limit: 100 } }
    ),

  /** One ExportPreparationArtifact read row (404-hide for foreign scope). */
  getArtifact: (novelId: number | string, artifactId: number) =>
    api.get<AgentArtifactView>(`/agent/novels/${novelId}/artifacts/${artifactId}`),

  /** Artifact revision lineage (ascending); content carries the preparation. */
  listArtifactRevisions: (novelId: number | string, artifactId: number) =>
    api.get<{ items: AgentArtifactRevisionView[]; total: number }>(
      `/agent/novels/${novelId}/artifacts/${artifactId}/revisions`,
      { params: { skip: 0, limit: 100 } }
    ),

  /** Read-only deterministic freeze: replays the current snapshot + hash. */
  agentPrepare: (
    novelId: number | string,
    projectId: number,
    body: {
      branch?: string | null;
      fork?: string | null;
      evidence_refs: string[];
    }
  ) =>
    api.post<DerivativeExportAgentPrepareResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/export/agent/prepare`,
      body
    ),

  /** The ONLY browser export path: materialize an approved artifact. */
  materialize: (
    novelId: number | string,
    projectId: number,
    body: DerivativeExportMaterializeBody
  ) =>
    api.post<DerivativeExportMaterializeResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/export/agent/materialize`,
      body
    ),

  /** Three-dimension audit (the quality source; never derived from a download). */
  audit: (novelId: number | string, projectId: number) =>
    api.get<DerivativeExportAuditReport>(
      `/novels/${novelId}/derivative-projects/${projectId}/export/audit`
    ),

  /**
   * Read-only deterministic file download. The returned bytes carry
   * `X-Export-Manifest-Hash` so the panel can verify them against the approved
   * artifact's frozen manifest checksum.
   */
  download: (
    novelId: number | string,
    projectId: number,
    format: DerivativeExportFormat
  ) =>
    api.get<Blob>(
      `/novels/${novelId}/derivative-projects/${projectId}/export/download`,
      { params: { format }, responseType: "blob" }
    ),
};
