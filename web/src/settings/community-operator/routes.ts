import type { OperatorResource } from "../../api/api-community-operator";

/** Top-level operator pages; `#/<page>/<id...>?<params>` addresses every view so back/forward restores it. */
export const pages = ["home", "items", "filters", "activity", "contributors", "queue", "versions", "aliases", "reports", "backups", "health", "audit",
  "inspect"] as const;
export type OperatorPage = typeof pages[number];
export interface OperatorRoute { page: OperatorPage; path: string[]; params: Record<string, string | null> }
type Params = Record<string, string | null | undefined>;

const safe = /^[A-Za-z0-9_.=:-]{1,1024}$/;
/** Keyset cursors are base64 (or base64url) of UTF-8 JSON, up to the service's 8192-character limit. */
const cursor = /^[A-Za-z0-9+/=_-]{1,8192}$/;
const cursorParams = new Set(["after", "before", "keysAfter", "filtersAfter", "submissionsAfter", "opsAfter"]);
/** Pre-redesign views and where they now live. */
const legacy: Record<string, string> = { overview: "home", recompute: "health", backup: "backups", operations: "health" };

/** Parse the hash route. Untrusted path segments and values that fail their pattern are dropped; `q` is free text. */
export function operatorRoute(hash = window.location.hash): OperatorRoute {
  const url = new URL(hash.slice(1) || "/home", window.location.origin);
  const segments = url.pathname.split("/").filter(Boolean).map(segment => { try { return decodeURIComponent(segment); } catch { return ""; } });
  const first = segments[0] ?? "home";
  const params: Record<string, string | null> = {};
  for (const [name, raw] of url.searchParams) {
    if (name === "q") params.q = raw.slice(0, 100);
    else if ((cursorParams.has(name) ? cursor : safe).test(raw)) params[name] = raw;
  }
  if (legacy[first]) return { page: legacy[first] as OperatorPage, path: [], params };
  const page = (pages as readonly string[]).includes(first) ? first as OperatorPage : "home";
  return { page, path: page === first ? segments.slice(1).filter(segment => safe.test(segment)) : [], params };
}
/** A hash link to `path` (for example "items/work-1") with the non-empty `params`. */
export function operatorLink(path: string, params: Params = {}) {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) if (value) query.set(name, value);
  return `#/${path.split("/").map(encodeURIComponent).join("/")}${query.size ? `?${query}` : ""}`;
}
/** Push a new history entry for a hash link. */
export function navigate(href: string) { window.location.hash = href.slice(1); }

/** An item page, optionally with a version, selected filter and revision-history cursor (`before`). */
export const itemLink = (workId: string, versionId?: string | null, filterId?: string | null, before?: string | null) =>
  operatorLink(`items/${workId}`, { version: versionId, filter: filterId, before });
export const contributorLink = (contributorId: string) => operatorLink("contributors", { selected: contributorId });
/** Where a resource is shown: filters on their item, contributors in their profile, versions/reports/suggestions in their lists, others in the inspector. */
export function resourceLink(kind: OperatorResource, id: string, contributor?: string) {
  if (kind === "filter") return operatorLink(`filters/${id}`);
  if (kind === "contributor") return contributorLink(id);
  if (kind === "version") return operatorLink("versions", { filter: "all", selected: id });
  if (kind === "report") return operatorLink("reports", { selected: id });
  if (kind === "alias_suggestion") return operatorLink("aliases", { filter: "all", selected: id });
  return operatorLink("inspect", { kind, selected: id, contributor });
}

const searchKinds = new Set(["filter", "attestation", "submission", "contributor", "version", "vote", "report", "alias_suggestion"]);
/** Where an exact-ID search hit of `kind` (from `GET /admin/v1/search` `ids`) opens. */
export function searchIdLink(kind: string, id: string) {
  if (kind === "work" || kind === "item") return itemLink(id);
  return searchKinds.has(kind) ? resourceLink(kind as OperatorResource, id) : operatorLink("inspect");
}
