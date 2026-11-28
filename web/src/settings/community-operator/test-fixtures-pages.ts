/** Page-level operator fixtures (catalog, profile, audit, system) built from the shared insight entities. */
import { activity, dayMs as d, filterRows, gore, goreLabel, healthBody, hourMs as h, items, leaders, now, people, queueCounts, queueItems, queueOldest,
  token, updates, versions } from "./test-fixtures-insights";

const envelope = { version: 1, observedAt: now };
const iv = (v: { versionId: string; label: string }, status: string, revision: number, runtimeMs: number, quarantined = false) =>
  ({ ...v, status, quarantined, runtimeMs, revision });
const row = (item: typeof items.arrival, list: ReturnType<typeof iv>[], at: number, filters: number, net: number, reports: number, uploads: number,
  who: { contributorId: string; displayName: string }[]) => ({ ...item, versions: list, lastChangeAt: at, filters, netVotes: net, openReports: reports, uploads, contributors: who });
export const itemRows = [
  row(items.arrival, [iv(versions.theatrical, "confirmed", 4, 7090000), iv(versions.dc, "confirmed", 7, 8072000), iv(versions.remaster, "candidate", 1, 8075000)],
    now - 2 * h, 53, 381, 1, 21, [people.mira, people.jonas, people.priya, people.theo, people.ana]),
  row(items.comet, [iv(versions.comet, "confirmed", 4, 6420000), iv(versions.cometNew, "candidate", 1, 6420000)], now - 3 * h, 28, 199, 0, 9, [people.kenji, people.mira, people.nora]),
  row(items.harbor, [iv(versions.harbor, "confirmed", 5, 2592000)], now - 26 * h, 17, 92, 1, 6, [people.jonas, people.omar]),
  row(items.lanterns, [iv(versions.lanterns, "confirmed", 3, 6100000)], now - 28 * h, 19, 36, 1, 5, [people.lena, people.sam, people.theo]),
  row(items.signal, [iv(versions.signal, "confirmed", 6, 7400000)], now - 2 * d, 34, 175, 0, 11, [people.priya, people.mira, people.ana, people.nora, people.jonas]),
  row(items.glass, [iv({ versionId: "v_glass_th", label: "Theatrical" }, "confirmed", 3, 6900000), iv({ versionId: "v_glass_test", label: "Test Cut" }, "candidate", 1, 6900000, true)],
    now - 4 * d, 26, 135, 0, 7, [people.mira, people.omar, people.kenji]),
  row(items.orchard, [iv({ versionId: "v_orchard_th", label: "Theatrical" }, "confirmed", 2, 6730000), iv(versions.orchard, "confirmed", 1, 6730000)],
    now - 6 * d, 22, 74, 1, 4, [people.ana, people.theo]),
  row(items.tide, [iv(versions.tide, "candidate", 1, 2592000)], now - 15 * d, 13, 20, 0, 2, [people.omar]),
];
export const itemsFixture = { ...envelope, sort: "updated", q: null, items: itemRows, nextCursor: null };

const detailVersion = (base: ReturnType<typeof iv>, trust: number, uploads: number, filters: number, who: { contributorId: string; displayName: string }[]) => ({
  ...base, aliasId: base.status === "confirmed" ? base.label.toLowerCase().replace(/[^a-z]+/g, "_") : null, labelText: base.status === "candidate" ? base.label : null,
  mergedInto: null, trust, createdAt: now - 200 * d, revisedAt: now - 2 * h, downloads: 146, uploads, filters, servedFilters: filters - 1, openReports: base.versionId === versions.dc.versionId ? 1 : 0,
  matchKeys: [{ type: "sha256_sampled", algorithm: "sha256", sizeBytes: 4000000, isReference: true, observations: 9 },
    { type: "audio_fingerprint", algorithm: "chromaprint", sizeBytes: null, isReference: false, observations: 6 }],
  contributors: who.map((person, index) => ({ ...person, tier: index ? 2 : 1, founder: index === 0 })), moderation: token(`version:${base.versionId}`) });
export const itemDetailFixture = { ...envelope,
  work: { ...items.arrival, createdAt: now - 230 * d, aliases: [{ title: "Arrival Road – Restored", year: 1977, observations: 3 }],
    identifiers: [{ provider: "tmdb", kind: "movie", value: "40213", season: null, episode: null, lastEpisode: null }] },
  versions: [detailVersion(itemRows[0].versions[0], 0.88, 6, 22, [people.theo, people.ana]),
    detailVersion(itemRows[0].versions[1], 0.92, 9, 31, [people.mira, people.jonas, people.priya, people.theo, people.ana, people.kenji, people.lena]),
    detailVersion(itemRows[0].versions[2], 0.41, 2, 29, [people.lena, people.kenji])] };
const revision = (revisionNumber: number, at: number, delta: number[], cause: object) => ({ revision: revisionNumber, generatedAt: at, filters: 31,
  added: delta[0], removed: delta[1], retimed: delta[2], recategorised: delta[3], cause, current: revisionNumber === 7 });
export const versionDetailFixture = { ...envelope, item: items.arrival,
  versionInfo: { ...versions.dc, status: "confirmed", mergedInto: null, quarantined: false, runtimeMs: 8072000, trust: 0.92, revision: 7 },
  filters: filterRows, truncated: false,
  revisions: [revision(7, now - 2 * h, [3, 1, 0, 0], { kind: "upload", contributor: people.mira }),
    revision(6, now - 5 * d, [0, 0, 2, 0], { kind: "votes", contributor: people.nora }),
    revision(5, now - 13 * d, [0, 1, 0, 0], { kind: "moderation", actor: "ryan@example.org", command: "quarantine" }),
    revision(4, now - 20 * d, [6, 0, 0, 0], { kind: "upload", contributor: people.jonas }),
    revision(3, now - 34 * d, [2, 0, 0, 1], { kind: "upload", contributor: people.priya }),
    { ...revision(1, now - 53 * d, [0, 0, 0, 0], { kind: "upload", contributor: people.theo }), filters: null, added: null, removed: null, retimed: null, recategorised: null }],
  revisionsNextCursor: null };
const vote = (who: { contributorId: string; displayName: string }, direction: string, at: number, excluded = false) => ({ contributor: who, vote: direction,
  excluded, revision: 7, at, moderation: token(`vote:f_gore:${who.contributorId}`, excluded ? 1 : 0) });
const attest = (who: { contributorId: string; displayName: string }, submissionId: string, at: number, extra: object) => ({ contributor: who, submissionId, entryId: "e4",
  kind: "support", implicit: false, counted: true, tier: 1, at, startMs: 3730100, endMs: 3751200, alignedStartMs: 3730100, alignedEndMs: 3751200, origin: "manual",
  manualReview: false, locallyVerified: false, modelVersion: null, ignoredReason: null, quarantined: false, ...extra });
export const filterContextFixture = { ...envelope, filter: gore, item: items.arrival, versionLabel: versions.dc,
  votes: [vote(people.mira, "up", now - 2 * h), vote(people.jonas, "up", now - 3 * d), vote(people.priya, "up", now - 4 * d), vote(people.kenji, "up", now - 5 * d),
    vote(people.lena, "up", now - 5 * d), vote(people.nora, "up", now - 6 * d), vote(people.omar, "up", now - 8 * d), vote(people.ana, "down", now - 9 * d),
    vote(people.sam, "down", now - 10 * d, true)],
  attestations: [attest(people.mira, "sub_mira7", now - 2 * h, { manualReview: true, locallyVerified: true }),
    attest(people.jonas, "sub_jonas4", now - 20 * d, { tier: 2, origin: "model", modelVersion: "3.2", startMs: 3729400, endMs: 3750800 }),
    attest(people.priya, "sub_priya3", now - 34 * d, { tier: 2, implicit: true, origin: "download" }),
    attest(people.theo, "sub_theo1", now - 53 * d, { kind: "dispute", counted: false, tier: 2, endMs: 3755000 })],
  reports: [{ reportId: "r_gore", contributor: people.ana, reason: "inaccurate", note: "Ends about four seconds early; the scene continues until 01:02:35.",
    state: "open", at: now - 21 * h, moderation: token("report:r_gore") }],
  moderation: [{ actionId: "a-exclude-sam", actor: "ryan@example.org", resource: `vote:f_gore:${people.sam.contributorId}`, command: "exclude_vote",
    reason: "Second account of a suspended contributor", outcome: "applied", at: now - 9 * d, voter: people.sam }] };
export { goreLabel };

export const leaderboardFixture = { ...envelope, period: "30d", metric: "accepted", since: now - 30 * d, total: leaders.length, items: leaders, nextCursor: null };
export const profileFixture = { ...envelope,
  identity: { ...people.mira, state: "active", quarantined: false, joinedAt: now - 238 * d, profileUpdatedAt: now - 30 * d,
    membership: [{ authority: "discord", state: "member", checkedAt: now - 9 * 60000 }], moderation: token(`contributor:${people.mira.contributorId}`) },
  totals: { uploads: 18, items: 6, accepted: 142, votesCast: 311, votesUpShare: 0.94, reportsFiled: 3 },
  installations: [{ installationId: "0f7c2a1e-0000-4000-8000-000000000001", createdAt: now - 238 * d, lastSyncAt: now - 3 * h, keys: { active: 1, retired: 0, revoked: 0 },
    newestKey: { keyId: "sha256:4c6d3b91", state: "active", createdAt: now - 238 * d, retiredAt: null } },
  { installationId: "0f7c2a1e-0000-4000-8000-000000000002", createdAt: now - 100 * d, lastSyncAt: now - 2 * d, keys: { active: 1, retired: 1, revoked: 0 },
    newestKey: { keyId: "sha256:99aa01ff", state: "active", createdAt: now - 40 * d, retiredAt: null } }],
  uploads: [{ submissionId: "sub_mira7", at: now - 2 * h, tier: 1, quarantined: false, state: "counted", entries: 14, item: items.arrival, version: versions.dc, filter: null },
    { submissionId: "sub_mira6", at: now - 4 * d, tier: 1, quarantined: false, state: "counted", entries: 22, item: items.glass, version: { versionId: "v_glass_th", label: "Theatrical" }, filter: null },
    { submissionId: "sub_mira5", at: now - 7 * d, tier: 2, quarantined: false, state: "superseded", entries: 9, item: items.comet, version: versions.cometNew, filter: null }],
  votes: [{ eventId: 412, kind: "cast", vote: "up", at: now - 3 * h, item: items.comet, version: versions.comet, filter: activity[1].filter },
    { eventId: 398, kind: "cast", vote: "down", at: now - 26 * h, item: items.signal, version: versions.signal, filter: activity[2].filter },
    { eventId: 377, kind: "removed", vote: "up", at: now - 3 * d, item: items.arrival, version: versions.dc, filter: goreLabel }],
  reportsFiled: [{ reportId: "r_mira1", reason: "spam", state: "resolved", at: now - 12 * d, targetType: "filter", item: items.lanterns, version: versions.lanterns,
    filter: { filterId: "f_kk1", category: null, subcategory: null, action: null, startMs: null, endMs: null } }],
  reportsAgainst: [],
  usage: { source: "application_observed", providerVerified: false, period: "2026-11-27", requests: { allowed: { occurrences: 1912, units: 1912, observedAt: now - h } },
    writes: { allowed: { occurrences: 40, units: 830, observedAt: now - h } }, storage: { bytes: 1468006, quotaBytes: 7000000 } } };
export const searchFixture = { ...envelope, q: "arrival", items: [items.arrival], contributors: [people.ana],
  ids: [{ kind: "filter", id: "f_gore", label: "violence › gore 01:02:10–01:02:31 · Arrival Road (1977)" }] };
const audit = (actionId: string, resource: string, command: string, reason: string, at: number, text: string, outcome = "applied") => ({ actionId, actor: "ryan@example.org",
  resource, targetId: resource.split(":")[1], command, reason, outcome, errorCode: outcome === "applied" ? null : "moderation_conflict", expectedRevision: 0, createdAt: at,
  resourceLabel: { type: resource.split(":")[0], id: resource.split(":")[1], text, item: items.arrival, version: versions.dc, filter: null, contributor: null } });
export const auditFixture = { ...envelope, source: "service_database", items: [
  audit("a-q-nude2", "filter:f_nude2", "quarantine", "Duplicate of the 01:31:05 filter with wrong bounds", now - 6 * d, "sexual › nudity 01:31:02–01:31:20 · Arrival Road (1977) · Director's Cut"),
  audit("a-exclude-sam", `vote:f_gore:${people.sam.contributorId}`, "exclude_vote", "Second account of a suspended contributor", now - 9 * d, "Sam Doyle's vote on violence › gore"),
  audit("a-merge-test", "version:v_glass_test", "merge_version", "Test upload from a developer build", now - 25 * d, "Glass Harbor (2011) · Test Cut", "conflict")],
  nextCursor: null };
const queueKinds: Record<string, string> = { reports: "report", aliases: "alias", versions: "version", quarantined: "quarantined", disputed: "disputed" };
export const queueFixture = (kind: string) => ({ ...envelope, kind, counts: queueCounts, oldest: queueOldest,
  oldestAt: queueOldest[kind as keyof typeof queueOldest] ?? null,
  items: kind === "all" ? queueItems : queueItems.filter(entry => entry.kind === queueKinds[kind]), nextCursor: kind === "all" || kind === "reports" ? "cursor-next" : null });
export const activityFixture = { ...envelope, kind: "all", items: activity, nextCursor: null };
export const healthFixture = { ...envelope, ...healthBody };
const trendPoints: Record<string, (i: number, r: number) => [number, number?]> = {
  db: (i, r) => [Math.round((36.6 + i * 0.055 + r * 0.08) * 1048576), Math.round((0.6 + (i % 6) * 0.45) * 1048576)],
  storage: (i, r) => [Math.round((91 + i * 0.72 + Math.max(0, r) * 0.6) * 1048576), 367001600],
  denials: (i, r) => [Math.round(i === 25 ? 96 : i === 26 ? 54 : 9 + r * 6)], requests: (i, r) => [Math.round(12100 + i * 55 + r * 500), Math.round(9 + r * 6)],
  uploads_votes: (i, r) => [Math.round(5 + r * 3 + i * 0.05), Math.round(150 + r * 28 + i * 1.3)], downloads: (i, r) => [Math.round(120 + r * 14 + i * 0.6)] };
export function trendFixture(metric: string, period: string) {
  const points = Array.from({ length: 30 }).map((_, i) => { const [v, v2] = trendPoints[metric](i, Math.sin(i * 1.7) * 0.5 + Math.sin(i * 0.63 + 1) * 0.5);
    return { t: now - (30 - i) * d, v, ...(v2 !== undefined ? { v2 } : {}) }; });
  const gauge = metric === "db" || metric === "storage", total = points.reduce((sum, point) => sum + point.v, 0);
  const total2 = points.reduce((sum, point) => sum + (point.v2 ?? 0), 0);
  return { ...envelope, metric, period, unit: gauge ? "bytes" : "events", bucketMs: d, points, total: gauge ? points[29].v : total,
    previousTotal: gauge ? points[0].v : Math.round(total * 0.9), total2: points[0].v2 !== undefined ? gauge ? points[29].v2 : total2 : null,
    previousTotal2: points[0].v2 !== undefined ? gauge ? points[0].v2 : Math.round(total2 * 0.92) : null,
    provenance: ["denials", "requests"].includes(metric) ? "application_observed" : "service_database" };
}
export const runsFixture = { ...envelope, items: [healthBody.lastRetention, { ...healthBody.lastRetention, runId: "run-ret-0", startedAt: now - 26 * h, outcome: "batch_limit",
  detail: { steps: 14, failedSteps: [] } }, { ...healthBody.lastRetention, runId: "run-ret-x", startedAt: now - 50 * h, outcome: "failed", detail: { steps: 14, failedSteps: [3] } }],
  nextCursor: null };
const run = (runId: string, at: number, trigger: string, bytes: number | null, status: string, extra: object = {}) => ({ runId, startedAt: at, trigger,
  actor: trigger === "manual" ? "ryan@example.org" : null, fileName: bytes ? `community-${runId}.sqlite` : null, bytes, durationMs: bytes ? 6200 : 400,
  integrity: status === "verified" ? "ok" : null, sha256: bytes ? "9f3c5e7a1b2d4c6e8f0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4a41e" : null, status, errorCode: null, note: null, ...extra });
export const backupsFixture = { ...envelope,
  config: { enabled: true, frequency: "daily", atUtc: "03:30", keepLast: 14, keepWeekly: 8, destination: "nightly", integrityCheck: true, updatedAt: now - 20 * d, updatedBy: "ryan@example.org" },
  destination: { configured: true, writable: true, freeBytes: 44667813888, root: "/var/lib/editarr-community/backups", path: "/var/lib/editarr-community/backups/nightly", error: null },
  runs: [run("bk-27", now - 14.5 * h, "scheduled", 43830067, "verified"), run("bk-26m", now - 26 * h, "manual", 43725209, "verified", { note: "Before merging Blue Comet versions" }),
    run("bk-26", now - 38.5 * h, "scheduled", 43515494, "verified"), run("bk-25", now - 62.5 * h, "scheduled", null, "failed", { errorCode: "destination_unwritable" }),
    run("bk-24", now - 86.5 * h, "scheduled", 43096064, "verified")],
  next: { at: now + 9.5 * h }, running: false };
export const stagedFixture = { ...envelope, runId: "bk-27", stagedPath: "/online-backups/staged/community-20261127T033000Z-bk27.sqlite",
  sha256: backupsFixture.runs[0].sha256, bytes: 43830067, schemaVersion: 5, restoreVolume: "editarr-community-restore-bk27",
  commands: ["docker compose -f community-service/compose.yaml stop community",
    `COMMUNITY_RESTORE_VOLUME=editarr-community-restore-bk27 docker compose -f community-service/compose.yaml run --rm --no-deps restore --confirm --source /online-backups/staged/community-20261127T033000Z-bk27.sqlite --sha256 ${backupsFixture.runs[0].sha256}`] };
export { updates };
