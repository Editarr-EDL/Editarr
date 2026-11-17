/** import-free system settings and model types. */

export interface VersionInformation {
  release: string;
  build_revision: string | null;
  build_dirty: boolean | null;
  scanner_behavior: string;
  database_schema: string;
  local_api: string;
  community_api: string | null;
  artifact_schema: string | null;
}

export interface PrincipalSummary {
  user_id: number;
  role: "owner" | "admin" | "user";
  credential: "session" | "api_key";
  status: "active" | "disabled";
  link_state: "unlinked" | "verified" | "outage_grace" | "outage_expired" | "server_replaced";
}

/** What the signed-in account may attempt before per-target narrowing (MU-04). */
export interface CapabilityPayload {
  version: "editarr-capabilities-9";
  permission_revision: number;
  principal: PrincipalSummary;
  capabilities: string[];
}

export interface SignInOptions {
  jellyfin: boolean;
}

/** The wire shape of every sign-in, sign-out, and status response. Parse it with `parseSession`. */
export interface AuthenticationStatus {
  state: "setup_required" | "unauthenticated" | "authenticated";
  setup_required: boolean;
  sign_in: SignInOptions;
  display_name?: string | null;
  jellyfin_user_id?: string | null;
  access?: CapabilityPayload | null;
  csrf_token?: string | null;
  return_to?: string | null;
  restart_required: boolean;
}

export interface AuthenticationCredentials {
  username: string;
  password: string;
  remember_me?: boolean;
  return_to?: string;
}

/** A browser's Quick Connect sign-in. Jellyfin's secret stays on the Editarr server. */
export interface QuickConnectAttempt {
  state: "pending" | "approved" | "expired" | "cancelled" | "consumed";
  code: string;
}

export interface JellyfinSetupRequest {
  server_url: string;
  username: string;
  password: string;
}

/** A password confirmed inside the same request as a sensitive action. */
export interface Reauthentication {
  password: string;
}

export interface DeploymentSnapshot {
  base_url: string;
  public_origin: string;
  prefix: string;
  listening_port: number;
  base_url_source: "saved" | "environment" | "local_default";
  port_source: "cli" | "saved" | "default";
  cli_port_override: number | null;
}

export interface DeploymentSavedOverrides {
  base_url: string | null;
  port: number | null;
}

export interface RestartCapability {
  supported: boolean;
  mode: "standalone_controller" | "unsupported";
  generation: number;
  in_progress: boolean;
  outcome: "idle" | "restarting" | "applied" | "recovered" | "failed";
  target_generation: number | null;
  reason: "manual_uvicorn" | "development_reload" | "controller_unavailable" | null;
  detail: string | null;
}

export interface RestartAcknowledgement {
  status: "accepted";
  target_generation: number;
  reconnect_url: string;
}

/** Active configuration and separately resolved values for the next service start. */
export interface DeploymentConfiguration {
  active: DeploymentSnapshot;
  next_start: DeploymentSnapshot;
  saved: DeploymentSavedOverrides;
  restart_required: boolean;
  restart: RestartCapability;
  macos_dock_supported?: boolean;
}

/** Fields omitted from a deployment update retain their explicit saved override. */
export interface DeploymentUpdate {
  base_url?: string | null;
  port?: number | null;
}

export type DiagnosticLogChannel = "application" | "controller" | "http" | "worker";

export interface DiagnosticLogSettings {
  debug_enabled: boolean;
}

export interface DiagnosticLogTail {
  channel: DiagnosticLogChannel;
  lines: string[];
  truncated: boolean;
}

export interface ScanCapabilities {
  audio_models: ScanModelCapability[];
  audio_filters: ScanModelCapability[];
  video_models: ScanModelCapability[];
  qwen_models: ScanModelCapability[];
  target_id?: string;
  model_capabilities?: ModelCapability[];
}

export interface ModelDownloadState {
  state: "not_required" | "not_downloaded" | "downloading" | "ready" | "failed" | "cancelled";
  bytes_downloaded: number | null;
  bytes_total: number | null;
  error_code: string | null;
  error_detail: string | null;
}

export interface ModelCapability extends ScanModelCapability {
  stage: "audio" | "detector" | "enrichment";
  availability: "available" | "unavailable";
  reason: string | null;
  device: "cpu" | "cuda" | "coreml" | "metal" | "mlx" | "none";
  weight_requirement: "none" | "on_demand";
  download: ModelDownloadState;
  enrichment_behavior: "none" | "qwen_summary" | "detector_findings_only";
}

export interface ManagedModelArtifact {
  artifact_id: string;
  backend_ids: string[];
  state: ModelDownloadState;
  expected_size: number | null;
  source_url: string;
  license_spdx: string | null;
  license_status: "declared" | "license-review-required";
  removable: boolean;
}

export interface ModelStorageStatus {
  artifacts: ManagedModelArtifact[];
  model_capabilities: ModelCapability[];
}

export interface ModelActionResult {
  status: ModelStorageStatus | null;
  accepted: boolean;
  removed: boolean;
}

export interface ScanModelCapability {
  id: string;
  display_name: string;
}
