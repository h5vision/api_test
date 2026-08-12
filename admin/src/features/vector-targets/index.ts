export type VectorRouteCandidate = {
  binding_id: string;
  snapshot_id: string;
  generation_id: string | null;
  generation_status: string | null;
  vector_index_id: string;
  ownership_mode: "vision_managed" | "external_attached";
  binding_source: "managed_generation" | "external_verification";
  verification_method: "managed_build" | "external_probe" | "manual";
  vector_target_id: string;
  embedding_profile_id: string;
  vector_index_status: string;
  vector_target_status: string;
  embedding_profile_status: string;
  external_verification_state: string | null;
  payload_keys: string[];
  eligible: boolean;
  routable: boolean;
  active: boolean;
  reason: string | null;
};

export type VectorRouteCandidatesResponse = {
  project_id: string;
  active_binding_id: string | null;
  routing_mode: "managed_auto" | "pinned";
  revision: number;
  candidates: VectorRouteCandidate[];
};

export type VectorRouteRecord = {
  project_id: string;
  tenant_id: string;
  active_binding_id: string | null;
  routing_mode: "managed_auto" | "pinned";
  revision: number;
  selected_by: string | null;
  selected_at: string | null;
  reason: string | null;
  active: VectorRouteCandidate | null;
  created_at: string;
  updated_at: string;
};

export type RepositorySource = {
  source_id: string;
  project_id: string;
  enabled: boolean;
};

export type RepositorySourceListResponse = {
  sources: RepositorySource[];
  total: number;
};

export type IndexingJobSummary = {
  job_id: string;
  job_kind: "repository" | "upload";
  project_id: string;
  source_id: string | null;
  state: string;
  stage: string;
  active: boolean;
  stalled: boolean;
  progress_percent: number;
  processed: number;
  total: number;
  files_processed: number;
  files_total: number;
  chunks_stored: number;
  updated_at: string;
  error: string | null;
};

export type IndexingJobListResponse = {
  checked_at: string;
  jobs: IndexingJobSummary[];
  total: number;
  active: number;
};

export type OfflineEmbeddingArtifact = {
  artifact_id: string;
  project_id: string;
  snapshot_id: string;
  generation_id: string;
  model_id: string;
  model_name: string;
  embedding_dimension: number;
  index_version: string;
  chunk_count: number;
  shard_count: number;
  relative_path: string;
  compatible: boolean;
  contract_errors: string[];
  imported: boolean;
  completed_at: string | null;
  error: string | null;
};

export type OfflineEmbeddingArtifactListResponse = {
  checked_at: string;
  root_available: boolean;
  artifacts: OfflineEmbeddingArtifact[];
  total: number;
  ready: number;
  imported: number;
};
