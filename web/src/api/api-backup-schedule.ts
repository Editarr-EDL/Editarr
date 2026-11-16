import { apiRequest, responseError } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export type BackupScheduleConfig = {
  enabled: boolean;
  interval_hours: number;
  keep_count: number;
  destination: string;
  kind: "full";
  protection: "plain" | "age-scrypt";
  passphrase_file: string | null;
};

export type BackupScheduleStatus = {
  config: BackupScheduleConfig | null;
  next_run: number | null;
  last_attempt: number | null;
  last_success: number | null;
  outcome: "idle" | "running" | "succeeded" | "failed" | "interrupted";
  error: string | null;
  retained_count: number;
  restore_hold: boolean;
  /** Set when a saved settings-only schedule was loaded as a full one; cleared on save. */
  notice?: "settings_schedule_now_full" | null;
};

async function request(init: RequestInit = {}): Promise<BackupScheduleStatus> {
  const response = await apiRequest(`${runtimeApiBasePath()}/backups/schedule`, {
    cache: "no-store", ...init,
  });
  if (!response.ok) throw await responseError(response, "Backup schedule is unavailable.");
  return await response.json() as BackupScheduleStatus;
}

export function fetchBackupSchedule(signal?: AbortSignal) {
  return request({ signal });
}

export function saveBackupSchedule(config: BackupScheduleConfig) {
  return request({ method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...config, confirmed: true }) });
}

export function disableBackupSchedule() {
  return request({ method: "DELETE" });
}