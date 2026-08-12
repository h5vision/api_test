export type ProviderStatus = {
  ai_provider: string;
  ai_model: string;
  ai_configured: boolean;
  embedding_provider: string;
  embedding_model: string;
  embedding_configured: boolean;
  vector_db_provider: string;
  default_model_id: string;
};

export type HealthResponse = {
  status: string;
  service: string;
  version: string;
  configuration: ProviderStatus;
  vector_store: {
    provider?: string;
    status?: "ok" | "unavailable" | string;
    projects: number;
    chunks: number;
    error?: string;
  };
};

export type PersistenceCapability = {
  id: string;
  role: string;
  description: string;
  status: "ready" | "degraded" | "unavailable";
  table_count: number;
  records_estimate: number | null;
  missing_tables?: string[];
  missing_columns?: string[];
};

export type PersistenceStatusResponse = {
  checked_at: string;
  status: "ready" | "degraded" | "migration_required" | "revision_mismatch" | "unavailable";
  implementation: { engine: string; schema: string };
  schema: {
    managed: boolean;
    revision: string | null;
    expected_revision: string;
    baseline_compatible: boolean;
    missing_tables: string[];
    missing_columns: string[];
  };
  capabilities: PersistenceCapability[];
  error: string | null;
};

export type ConnectionState = "online" | "stale" | "degraded" | "offline" | "unknown";

export type ConnectivityResponse = {
  checked_at: string;
  frontend: {
    status: "online" | "stale" | "offline" | "unknown";
    connected: boolean;
    client_id: string | null;
    project_id: string | null;
    client_version: string | null;
    last_event: string | null;
    last_seen_at: string | null;
    age_seconds: number | null;
  };
  backendai: {
    status: "online" | "degraded" | "offline";
    connected: boolean;
    model_id: string;
    model: string;
    model_available: boolean;
    latency_ms: number;
    error: string | null;
  };
};
