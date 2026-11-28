import { activityFixture, auditFixture as auditPageFixture, backupsFixture, filterContextFixture, healthFixture, itemDetailFixture, itemsFixture, leaderboardFixture,
  profileFixture, queueFixture, runsFixture, stagedFixture, searchFixture, trendFixture, versionDetailFixture, olderRevisionsFixture } from "./test-fixtures-pages";
import { homeFixture } from "./test-fixtures-insights";

export const operatorTimestamp = Date.parse("2026-11-25T12:00:00Z");
const unavailable = { source: "unavailable", observedAt: null, value: null, providerVerified: false };
export const overviewFixture = { version: 1, observedAt: operatorTimestamp, period: "2026-11-27",
  storage: { source: "artifact_storage_usage", observedAt: operatorTimestamp, providerVerified: false,
    globalBytes: 12345 },
  databaseSize: unavailable, providerUsage: unavailable, backup: unavailable,
  activity: { source: "partial_application_observed", providerVerified: false, restoreProvenance: "unknown",
    resources: { worker_requests: { observed: 40, observedAt: operatorTimestamp - 3600000 },
      d1_writes: { observed: null, observedAt: null } } },
  observations: ["download", "import", "vote", "report"].map(handler => ({ handler, source: "partial_handler_observed",
    providerVerified: false, occurrences: handler === "download" ? 3 : null, reads: handler === "download" ? 55 : null,
    unknownReads: handler === "download" ? 1 : null, writes: handler === "download" ? 9 : null,
    unknownWrites: handler === "download" ? 0 : null, observedAt: handler === "download" ? operatorTimestamp : null })) };
export const contributorFixture = { contributorId: "contributor-1", displayName: "<img src=x onerror=alert(1)>", state: "suspended", createdAt: operatorTimestamp };
export const versionFixture = { versionId: "version-1", workId: "work-1", status: "candidate", aliasId: null, labelText: "Remastered",
  mergedInto: null, runtimeMs: 5400000, trust: 0.5, revision: 3, quarantined: 0, createdAt: operatorTimestamp };
export const versionRowFixture = { ...versionFixture, contributorCount: 2, filterCount: 4, servedFilterCount: 3 };
export const suggestionFixture = { suggestionId: "suggestion-1", normalized: "remastered", text: "Remastered", submissionId: "submission-1",
  state: "pending", aliasId: null, createdAt: operatorTimestamp };
export const filterRowFixture = { filterId: "filter-1", state: "active", quarantined: 0, category: "violence", subcategory: "gore",
  severity: "high", action: "skip", startMs: 1000, endMs: 2000, confidence: 0.75, contributorCount: 2, attestations: 3,
  upvotes: 1, downvotes: 0, disputes: 0, served: 1 };
export const submissionRowFixture = { submissionId: "submission-1", contributorId: "contributor-1", state: "counted", tier: 1,
  quarantined: 0, createdAt: operatorTimestamp };
export const reportFixture = { reportId: "report-1", contributorId: "contributor-2", targetType: "filter", targetId: "filter-1", reason: "privacy",
  note: "<script>window.hostileTextExecuted=true</script>", createdAt: operatorTimestamp };
export const operationFixture = { contributorId: "contributor-1", kind: "publication", operationId: "operation-1",
  state: "completed", createdAt: operatorTimestamp, updatedAt: operatorTimestamp };
export const auditFixture = { actionId: "action-1", actor: "fixture-operator", resource: "filter:filter-1", command: "quarantine",
  reason: "<b>Private operator reason</b>", outcome: "applied", expectedRevision: 1, createdAt: operatorTimestamp };
export const keyFixture = { keyId: "key-1", installationId: "installation-1", state: "retired", createdAt: operatorTimestamp, retiredAt: operatorTimestamp };
export const usageFixture = { version: 1, contributorId: "contributor-1", observedAt: operatorTimestamp, period: "2026-11-27",
  storage: { source: "artifact_storage_usage", observedAt: operatorTimestamp, bytes: 12345 },
  observations: ["worker_requests", "d1_writes"].map(resource => ({ resource, outcome: "allowed", occurrences: 10,
    units: 10, observedAt: operatorTimestamp })) };
export function pageFixture(items: unknown[] = [], nextCursor: string | null = null) {
  return { version: 1, source: "service_database", observedAt: operatorTimestamp, items, nextCursor };
}
/** The synthetic response for an operator API path (with its query, if any); throws for unknown routes. */
export function operatorFixture(target: string) {
  const url = new URL(target, "https://operator.example.test"), path = url.pathname, query = url.searchParams;
  if (path === "/admin/v1/session") return { version: 1, subject: "fixture-operator" };
  const insight = insightFixture(path, query);
  if (insight) return insight;
  if (path === "/admin/v1/dashboard/overview") return overviewFixture;
  if (path === "/admin/v1/versions") return { version: 1, versions: [versionRowFixture], nextCursor: null };
  if (path === "/admin/v1/alias-suggestions") return { version: 1, suggestions: [suggestionFixture], nextCursor: null };
  if (path.endsWith("/filters")) return pageFixture([filterRowFixture]);
  if (path.endsWith("/submissions")) return pageFixture([submissionRowFixture]);
  if (path === "/admin/v1/dashboard/contributors") return pageFixture([contributorFixture]);
  if (path === "/admin/v1/dashboard/operations") return query.get("after") ?
    pageFixture([{ ...operationFixture, operationId: "operation-0", kind: "import", state: "failed" }]) : pageFixture([operationFixture], "WyJvcCJd+/=");
  if (path === "/admin/v1/dashboard/audit") return pageFixture([auditFixture]);
  if (path === "/admin/v1/reports") return { version: 1, reports: [reportFixture], nextCursor: null };
  if (path.endsWith("/usage")) return usageFixture;
  if (path.endsWith("/keys")) return pageFixture([keyFixture]);
  if (path.startsWith("/admin/v1/moderation/")) {
    const { resource: _resource, ...entry } = auditFixture;
    const [kind, id] = path.split("/").slice(4).map(decodeURIComponent);
    const state = kind === "version" ? { ...versionFixture, versionId: id, referenceSubmissionId: "submission-1" } :
      kind === "alias_suggestion" ? { ...suggestionFixture, suggestionId: id } :
        kind === "filter" ? { filterId: id, versionId: "version-1", state: "active", mergedInto: null, quarantined: 0, served: 1, confidence: 0.75, contributorCount: 2 } :
          kind === "report" ? { ...reportFixture, state: "open" } :
            { contributorId: id, displayName: contributorFixture.displayName, state: "suspended", quarantined: 0 };
    return { version: 1, resource: `${kind}:${id}`, revision: 2, state, audit: [entry] };
  }
  throw new Error(`Unknown synthetic operator route: ${path}`);
}
/** Fixtures for the operator insight, system and backup endpoints. */
function insightFixture(path: string, query: URLSearchParams): unknown {
  const route = path.replace(/^\/admin\/v1\//, "");
  if (route === "home") return { ...homeFixture, period: query.get("period") ?? "7d" };
  if (route === "items") return itemsFixture;
  if (/^items\/[^/]+$/.test(route)) return itemDetailFixture;
  if (/^items\/[^/]+\/versions\/[^/]+$/.test(route)) return query.get("before") ? olderRevisionsFixture : versionDetailFixture;
  if (/^filters\/[^/]+\/context$/.test(route)) return filterContextFixture;
  if (route === "activity") return activityFixture;
  if (route === "contributors/leaderboard") return { ...leaderboardFixture, period: query.get("period") ?? "30d", metric: query.get("metric") ?? "accepted" };
  if (/^contributors\/[^/]+\/profile$/.test(route)) return profileFixture;
  if (route === "queue") return queueFixture(query.get("kind") ?? "all");
  if (route === "search") return { ...searchFixture, q: query.get("q") ?? "" };
  if (route === "audit") return auditPageFixture;
  if (route === "health") return healthFixture;
  if (route === "trends") return trendFixture(query.get("metric") ?? "db", query.get("period") ?? "30d");
  if (route === "maintenance/runs") return query.get("after") ? { ...runsFixture, items: [{ ...runsFixture.items[2], runId: "run-ret-old",
    startedAt: runsFixture.items[2].startedAt - 86400000 * 3, outcome: "skipped", detail: { steps: 0, failedSteps: [] } }], nextCursor: null }
    : { ...runsFixture, nextCursor: "WzE3MDAsInJ1bi1yZXQteCJd+/=" };
  if (route === "backups" || route === "backups/config") return backupsFixture;
  if (/^backups\/[^/]+\/stage$/.test(route)) return stagedFixture;
  return null;
}
