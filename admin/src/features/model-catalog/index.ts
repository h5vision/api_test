export type ModelInfo = {
  model_id: string;
  model_name: string;
  display_name: string;
  provider: string;
  location: "internal" | "cloud" | "local";
  deployment_type?: "cloud" | "local" | "remote_server";
  endpoint?: string | null;
  enabled: boolean;
  available: boolean;
  is_default: boolean;
};

export type ModelListResponse = {
  default_model_id: string;
  checked_at: string;
  models: ModelInfo[];
};

export type AIProviderRecord = {
  provider_id: string;
  name: string;
  protocol: "ollama" | "openai";
  base_url: string;
  auth_type: "none" | "bearer" | "x-api-key";
  api_key_configured: boolean;
  api_key_hint: string | null;
  enabled: boolean;
  deployment_type: "cloud" | "local" | "remote_server";
  chat_processing_mode: "vision_managed" | "provider_managed";
  status: "unknown" | "online" | "degraded" | "offline" | "disabled";
  error: string | null;
  latency_ms: number;
  model_count: number;
  models: string[];
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AIProviderListResponse = {
  providers: AIProviderRecord[];
  total: number;
};

export type OllamaScanResponse = {
  checked_at: string;
  targets: {
    source: string;
    base_url: string;
    status: "online" | "degraded" | "offline";
    models: string[];
    skipped_non_chat_models: string[];
    latency_ms: number;
    error: string | null;
    registered: boolean;
    provider_id: string | null;
  }[];
  discovered_servers: number;
  registered_providers: number;
  chat_models: number;
};

export type RuntimeServiceSettingsResponse = {
  configured: boolean;
  setup_required: boolean;
  missing: string[];
  groq: {
    enabled: boolean;
    base_url: string;
    model: string;
    public_model_id: string;
    api_key_configured: boolean;
  };
  default_model_id: string;
  vector: {
    provider: string;
    vector_target_id: string;
    embedding_profile_id: string;
    host: string;
    port: number;
    collection: string;
    embedding_deployment: string;
    embedding_provider: string;
    embedding_base_url: string;
    embedding_model: string;
    embedding_model_id: string;
    embedding_dimension: number;
    embedding_batch_size: number;
    index_version: string;
    active_host: string;
    active_port: number;
    active_collection: string;
    active_embedding_deployment: "api" | "local";
    active_embedding_provider: string;
    active_embedding_base_url: string;
    active_embedding_model: string;
    active_embedding_model_id: string;
    active_embedding_dimension: number;
    active_embedding_batch_size: number;
    active_index_version: string;
    restart_required: boolean;
    reindex_required: boolean;
  };
  updated_at: string | null;
};
