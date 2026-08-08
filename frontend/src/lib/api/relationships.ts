/**
 * 人物关系图 API（Phase 09）。
 */

import { api } from "./client";

/** Canonical Phase 09 edge types only — causes/precedes/same_entity are not graph edges. */
export type RelationshipEdgeType =
  | "ally"
  | "enemy"
  | "family"
  | "mentor"
  | "romantic";

/** Graph projection labels include honesty-only cooccur for provisional edges. */
export type RelationshipGraphEdgeLabel = RelationshipEdgeType | "cooccur";

export type RelationshipEdgeKind =
  | "accepted_observation"
  | "provisional_cooccurrence";

export type RelationshipVersionSource =
  | "active"
  | "running_candidate"
  | "history";

export type GraphDegradationMode = "normal" | "large" | "filters_required";

export type RelationshipProvenance = "machine" | "manual";

export interface RelationshipGraphNode {
  character_id: number;
  name: string;
  aliases: string[];
  first_visible_chapter: number;
}

export interface RelationshipGraphEdge {
  observation_id: number;
  source_character_id: number;
  target_character_id: number;
  relation_type: RelationshipGraphEdgeLabel;
  transition: "establish" | "change" | "end";
  confidence: number;
  valid_from_chapter: number;
  valid_to_chapter: number | null;
  provenance: RelationshipProvenance;
  evidence_preview: string | null;
  evidence_count: number;
  /** Truth tier: accepted observation vs provisional co-occurrence. */
  edge_kind?: RelationshipEdgeKind;
  /** Heuristic type clue for provisional edges only — not accepted fact. */
  suggested_type?: RelationshipEdgeType | null;
}

export interface RelationshipCounts {
  nodes: number;
  edges: number;
  relation_types: Record<string, number>;
}

export interface RelationshipDegradation {
  mode: GraphDegradationMode;
  node_count: number;
  edge_count: number;
  hard_node_cap: number;
  hard_edge_cap: number;
  message: string | null;
}

export interface RelationshipGraphEnvelope {
  novel_id: number;
  version_id: number;
  source: RelationshipVersionSource;
  through_chapter: number;
  full_book: boolean;
  cutoff_chapter: number;
  nodes: RelationshipGraphNode[];
  edges: RelationshipGraphEdge[];
  counts: RelationshipCounts;
  available_relation_types: RelationshipGraphEdgeLabel[];
  available_character_ids: number[];
  degradation: RelationshipDegradation;
  generated_at: string | null;
}

export interface RelationshipEvidenceRef {
  evidence_id: string;
  chapter_id: number;
  source_start: number;
  source_end: number;
  content_hash: string;
  excerpt: string | null;
}

export interface RelationshipEvidenceResponse {
  observation_id: number;
  novel_id: number;
  version_id: number;
  through_chapter: number;
  relation_type: RelationshipEdgeType;
  source_character_id: number;
  target_character_id: number;
  evidence: RelationshipEvidenceRef[];
  provenance: RelationshipProvenance;
}

/**
 * Graph query params matching OpenAPI GET /relationships/{novel_id}/graph.
 * Server uses singular character_id / relation_type filters (09-03).
 * Client never sends owner_id.
 */
export interface RelationshipGraphQuery {
  source?: RelationshipVersionSource;
  version_id?: number;
  through_chapter?: number;
  full_book?: boolean;
  character_id?: number;
  relation_type?: RelationshipGraphEdgeLabel | string;
  /** When accepted edges exist, also load provisional co-occurrence layer. */
  include_provisional?: boolean;
}

export interface RelationshipEvidenceQuery {
  source?: RelationshipVersionSource;
  version_id?: number;
  through_chapter?: number;
  full_book?: boolean;
}

export const relationshipsApi = {
  getGraph: (novelId: string | number, params?: RelationshipGraphQuery) =>
    api.get<RelationshipGraphEnvelope>(`/relationships/${novelId}/graph`, {
      params: {
        source: params?.source,
        version_id: params?.version_id,
        through_chapter: params?.through_chapter,
        full_book: params?.full_book,
        character_id: params?.character_id,
        relation_type: params?.relation_type,
        include_provisional: params?.include_provisional,
      },
    }),
  getEvidence: (
    novelId: string | number,
    observationId: number,
    params?: RelationshipEvidenceQuery
  ) =>
    api.get<RelationshipEvidenceResponse>(
      `/relationships/${novelId}/observations/${observationId}/evidence`,
      {
        params: {
          source: params?.source,
          version_id: params?.version_id,
          through_chapter: params?.through_chapter,
          full_book: params?.full_book,
        },
      }
    ),
};
