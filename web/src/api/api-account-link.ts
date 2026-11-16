import { apiRequest, responseError, ApiError } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import type { AccountSummary } from "./api-users";

export type ProofBasis = "actor_authentication" | "admin_attested";
export type LinkKind = "link" | "relink";
type Role = "owner" | "admin" | "user";
type Status = "active" | "disabled";

export interface LinkIdentity { server_id: string; user_id: string }
export interface LinkExportMove {
  export_id: number; format: "edl" | "mcf"; media_item_id: string; media_source_id: string;
  publication_revision: number; action: "move" | "record"; conflict: string | null;
}

/** MU-35's impact preview. It names no host path, password, or token. */
export interface LinkPreview {
  user_id: number;
  kind: LinkKind | null;
  identity: { current: LinkIdentity | null; after: LinkIdentity };
  export_id: { current: string; after: string };
  role: { current: Role; after: Role };
  media: { current: "local" | "jellyfin" | "suspended"; after: "jellyfin"; all_libraries: boolean; libraries: number; parental_limit: boolean };
  profile: { name: { current: string | null; after: string }; avatar: "jellyfin" | "none"; synced_read_only: boolean };
  proof_basis: ProofBasis;
  /** `after` is active when linking re-enables an account disabled only for lacking a link. */
  status: { current: Status; after: Status; cause: AccountSummary["disable_cause"] };
  exports: LinkExportMove[];
  blockers: string[];
}

export interface ProvenLink { proof: string; expires_at: string; preview: LinkPreview; confirmation: string }
export interface LinkResult { state: "completed" | "removing"; preview: LinkPreview }

/** A link refusal. `review` carries the server's current preview when it sent one. */
export class LinkRefusal extends ApiError {
  readonly review: { preview: LinkPreview; confirmation: string } | null;
  constructor(error: ApiError, review: LinkRefusal["review"]) {
    super(error.message, error.status, error.code, null, null, null, [], [], [], [], error.retryAfterSeconds);
    this.name = "LinkRefusal";
    this.review = review;
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await apiRequest(`${runtimeApiBasePath()}${path}`, {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (response.ok) return response.json();
  const copy = response.clone();
  const error = await responseError(response, "Unable to link the account.");
  const parsed = await copy.json().catch(() => null) as { preview?: LinkPreview; confirmation?: string } | null;
  throw new LinkRefusal(error, parsed?.preview && parsed.confirmation ? { preview: parsed.preview, confirmation: parsed.confirmation } : null);
}

/** Prove the actor's fresh sign-in and the target's Jellyfin identity in one request. */
export function proveLink(userId: number, secrets: { password: string; jellyfin: { username: string; password: string } }): Promise<ProvenLink> {
  return post(`/users/${userId}/link/proof`, {
    reauthentication: { password: secrets.password },
    jellyfin: secrets.jellyfin,
  });
}

/** Spend a proof against the preview's confirmation digest. */
export function confirmLink(userId: number, password: string, proven: Pick<ProvenLink, "proof" | "confirmation">): Promise<LinkResult> {
  return post(`/users/${userId}/link`, { reauthentication: { password }, proof: proven.proof, confirmation: proven.confirmation });
}
