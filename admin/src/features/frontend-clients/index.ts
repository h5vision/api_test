export type NetworkEndpointSettings = {
  ip: string;
  port: number;
};

export type NetworkSettingsResponse = {
  configured: boolean;
  setup_required: boolean;
  frontend: NetworkEndpointSettings;
  backendai: NetworkEndpointSettings;
  updated_at: string | null;
  frontend_reachable: boolean;
  frontend_latency_ms: number;
  frontend_error: string | null;
};

export type FrontendClientRecord = {
  client_id: string;
  instance_id: string | null;
  name: string;
  ip: string;
  port: number;
  enabled: boolean;
  chat_deep_normalization_mode: "inherit" | "auto" | "off";
  registration_type: "admin" | "auto";
  last_seen_ip: string | null;
  last_seen_at: string | null;
  reachable: boolean;
  latency_ms: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type ChatIntakeSettingsResponse = {
  deep_normalization_enabled: boolean;
  fallback_mode: "raw_message";
  basic_normalization_enabled: true;
  updated_at: string;
};

export type FrontendClientListResponse = {
  clients: FrontendClientRecord[];
  total: number;
  enabled: number;
  reachable: number;
};
