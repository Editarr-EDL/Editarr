export const operatorTimestamp = Date.parse("2026-10-04T12:00:00Z");
const unavailable = { source: "unavailable", observedAt: null, value: null, providerVerified: false };
export const overviewFixture = { version: 1, observedAt: operatorTimestamp, period: "2026-10-04",
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
export const contributionFixture = { revisionId: "revision-1", contributionId: "root-1", contributorId: "contributor-1",
  displayName: "Synthetic Author", authorState: "suspended", kind: "scan", state: "quarantined", createdAt: operatorTimestamp,
  totalEvents: 5, payloadBytes: 12345, imports: 1, likes: 0, dislikes: 0,
  ranking: { policyVersion: "community-ranking-wilson-1", qualityScore: 0.094528655, voteCount: 0, uploaderBoost: 0, score: 0.094528655 } };
export const reportFixture = { reportId: "report-1", contributorId: "contributor-2", revisionId: "revision-1", reason: "privacy",
  note: "<script>window.hostileTextExecuted=true</script>", createdAt: operatorTimestamp };
export const operationFixture = { contributorId: "contributor-1", kind: "publication", operationId: "operation-1",
  state: "completed", createdAt: operatorTimestamp, updatedAt: operatorTimestamp };
export const auditFixture = { actionId: "action-1", actor: "fixture-operator", resource: "revision:revision-1", command: "quarantine",
  reason: "<b>Private operator reason</b>", outcome: "applied", expectedRevision: 1, createdAt: operatorTimestamp };
export const keyFixture = { keyId: "key-1", installationId: "installation-1", state: "retired", createdAt: operatorTimestamp, retiredAt: operatorTimestamp };
export const usageFixture = { version: 1, contributorId: "contributor-1", observedAt: operatorTimestamp, period: "2026-10-04",
  storage: { source: "artifact_storage_usage", observedAt: operatorTimestamp, bytes: 12345 },
  observations: ["worker_requests", "d1_writes"].map(resource => ({ resource, outcome: "allowed", occurrences: 10,
    units: 10, observedAt: operatorTimestamp })) };
export function pageFixture(items: unknown[] = [], nextCursor: string | null = null) {
  return { version: 1, source: "service_database", observedAt: operatorTimestamp, items, nextCursor };
}
export function operatorFixture(path: string) {
  if (path === "/admin/v1/session") return { version: 1, subject: "fixture-operator" };
  if (path === "/admin/v1/dashboard/overview") return overviewFixture;
  if (path === "/admin/v1/dashboard/contributions") return pageFixture([contributionFixture]);
  if (path === "/admin/v1/dashboard/contributors") return pageFixture([contributorFixture]);
  if (path === "/admin/v1/dashboard/operations") return pageFixture([operationFixture]);
  if (path === "/admin/v1/dashboard/audit") return pageFixture([auditFixture]);
  if (path === "/admin/v1/reports") return { version: 1, reports: [reportFixture], nextCursor: null };
  if (path.endsWith("/usage")) return usageFixture;
  if (path.endsWith("/keys")) return pageFixture([keyFixture]);
  if (path.startsWith("/admin/v1/moderation/")) {
    const { resource: _resource, ...entry } = auditFixture;
    const kind = path.split("/")[4], id = path.split("/")[5];
    const state = kind === "revision" ? { revisionId: id, contributorId: "contributor-1", state: "quarantined", authorState: "suspended", imports: 1, likes: 0, dislikes: 0 } :
      kind === "report" ? { ...reportFixture, state: "open" } :
        { contributorId: id, displayName: contributorFixture.displayName, state: "suspended" };
    return { version: 1, resource: `${kind}:${id}`, revision: 2, state, audit: [entry] };
  }
  throw new Error(`Unknown synthetic operator route: ${path}`);
}