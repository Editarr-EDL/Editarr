import { apiRequest, responseError } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

/** The redacted archive summary a setup restore shows before activation. */
export type RestorePlan = {
  kind: "full";
  protection: "plain" | "age-scrypt";
  source_identity: { installation_id: string; key_id: string } | null;
  account_identities: number;
  administrator_included: boolean;
  included_secret_types: string[];
  omitted_environment: string[];
  /** The backup's saved binding, which the empty installation does not adopt; `null` is the default. */
  backup_deployment: { base_url: string | null; port: number | null };
};

export type BackupJob = {
  id: string;
  action: "export" | "restore";
  kind: "full";
  state: "waiting" | "uploaded" | "working" | "activating" | "ready" | "activated" | "failed" | "cancelled";
  error: string | null;
  /** A closed refusal code, such as `restore_target_has_work`, when a restore was refused. */
  reason: string | null;
  plan: RestorePlan | null;
  inventory: {
    protection: "plain" | "age-scrypt";
    identity_included: boolean;
    included_secret_types: string[];
    omitted_environment: string[];
  } | null;
  report: { move_disagreements: number } | null;
};

const exportEndpoint = (id?: string) => `${runtimeApiBasePath()}/backups/jobs${id ? `/${encodeURIComponent(id)}` : ""}`;
const restoreEndpoint = (id?: string) => `${runtimeApiBasePath()}/setup-restore/jobs${id ? `/${encodeURIComponent(id)}` : ""}`;
const setupHeaders = (token: string): Record<string, string> => ({ "X-Setup-Restore-Token": token });

async function checked(response: Response): Promise<BackupJob> {
  if (!response.ok) throw await responseError(response, "Backup request failed.");
  return await response.json() as BackupJob;
}

export async function startBackupExport(options: { encrypt?: boolean; passphrase?: string } = {}): Promise<BackupJob> {
  return checked(await apiRequest(exportEndpoint(), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "export", ...options }),
  }));
}

export async function backupStatus(id: string): Promise<BackupJob> {
  return checked(await apiRequest(exportEndpoint(id)));
}

export async function cancelBackup(id: string): Promise<BackupJob> {
  return checked(await apiRequest(exportEndpoint(id), { method: "DELETE" }));
}

export function backupDownloadUrl(id: string): string {
  return `${exportEndpoint(id)}/download`;
}

export async function checkBackupDownload(id: string): Promise<void> {
  const response = await apiRequest(backupDownloadUrl(id), { method: "HEAD" });
  if (!response.ok) throw await responseError(response, "Backup download is no longer available.");
}

/** Start a setup restore flow; refused with a named code unless the installation is empty. */
export async function beginSetupRestore(): Promise<string> {
  const response = await apiRequest(`${runtimeApiBasePath()}/setup-restore/flow`, { method: "POST" });
  if (!response.ok) throw await responseError(response, "Unable to start restore.");
  return (await response.json() as { token: string }).token;
}

export async function startSetupRestore(token: string): Promise<BackupJob> {
  return checked(await apiRequest(restoreEndpoint(), { method: "POST", headers: setupHeaders(token) }));
}

export async function setupRestoreStatus(id: string): Promise<BackupJob> {
  return checked(await apiRequest(restoreEndpoint(id)));
}

export async function uploadSetupRestore(id: string, file: File, token: string): Promise<BackupJob> {
  return checked(await apiRequest(`${restoreEndpoint(id)}/archive`, {
    method: "PUT", headers: { "Content-Type": "application/octet-stream", ...setupHeaders(token) }, body: file,
  }));
}

export async function preflightSetupRestore(id: string, token: string, passphrase?: string): Promise<BackupJob> {
  return checked(await apiRequest(`${restoreEndpoint(id)}/preflight`, {
    method: "POST", headers: { "Content-Type": "application/json", ...setupHeaders(token) },
    body: JSON.stringify({ passphrase: passphrase || null }),
  }));
}

export async function activateSetupRestore(id: string, token: string): Promise<BackupJob> {
  return checked(await apiRequest(`${restoreEndpoint(id)}/activate`, {
    method: "POST", headers: { "Content-Type": "application/json", ...setupHeaders(token) },
    body: JSON.stringify({ confirmed: true }),
  }));
}

export async function cancelSetupRestore(id: string, token: string): Promise<BackupJob> {
  return checked(await apiRequest(restoreEndpoint(id), { method: "DELETE", headers: setupHeaders(token) }));
}
