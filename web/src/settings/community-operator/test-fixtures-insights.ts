/** Realistic operator insight fixtures in the service's exact response shapes (see community-service/src/admin/insights). */
export const now = Date.parse("2026-11-27T18:00:00Z");
const h = 3600000, d = 86400000;
export const person = (key: string, displayName: string) => ({ contributorId: `c-${key}`, displayName });
export const people = { mira: person("mira", "Mira Okafor"), jonas: person("jonas", "Jonas Lind"), priya: person("priya", "Priya Raman"),
  ana: person("ana", "Ana Ruiz"), kenji: person("kenji", "Kenji Sato"), nora: person("nora", "Nora Brandt"), lena: person("lena", "Lena Vogel"),
  omar: person("omar", "Omar Farouk"), theo: person("theo", "Theo Marsh"), sam: person("sam", "Sam Doyle") };
const item = (workId: string, title: string, year: number, episode: { season: number; episode: number } | null = null) =>
  ({ workId, title, year, kind: episode ? "episode" : "movie", episode });
export const items = { arrival: item("w_arrival", "Arrival Road", 1977), comet: item("w_comet", "Blue Comet", 1984),
  harbor: item("w_harbor", "Example Series", 2019, { season: 1, episode: 3 }), lanterns: item("w_lanterns", "Paper Lanterns", 1989),
  signal: item("w_signal", "Northern Signal", 1996), glass: item("w_glass", "Glass Harbor", 2011), orchard: item("w_orchard", "The Quiet Orchard", 2003),
  tide: item("w_tide", "Example Series", 2019, { season: 1, episode: 4 }) };
const version = (versionId: string, label: string) => ({ versionId, label });
export const versions = { theatrical: version("v_arrival_th", "Theatrical"), dc: version("v_arrival_dc", "Director's Cut"),
  remaster: version("v_arrival_4k", "Remastered 4K"), comet: version("v_comet_th", "Theatrical"), cometNew: version("v_comet_rm", "Blue Comet: Remastered"),
  harbor: version("v_harbor_bc", "Broadcast"), lanterns: version("v_lanterns_th", "Theatrical"), signal: version("v_signal_dc", "Director's Cut"),
  orchard: version("v_orchard_un", "Unrated"), tide: version("v_tide_bc", "Broadcast") };
const label = (filterId: string, category: string, subcategory: string, action: string, start: number, end: number) =>
  ({ filterId, category, subcategory, action, startMs: Math.round(start * 1000), endMs: Math.round(end * 1000) });
const raw: [string, string, string, string, number, number, number, number, number, boolean?][] = [
  ["f_prof1", "language", "profanity", "mute", 724.2, 725, 0.93, 18, 0], ["f_prof2", "language", "profanity", "mute", 1311, 1312.4, 0.88, 14, 1],
  ["f_slur", "language", "slur", "mute", 2205.5, 2207.1, 0.91, 16, 0], ["f_prof3", "language", "profanity", "mute", 3020, 3021.2, 0.72, 6, 2],
  ["f_blas", "language", "blasphemy", "mute", 4410.8, 4412, 0.64, 4, 3], ["f_prof4", "language", "profanity", "mute", 6005, 6006.1, 0.9, 11, 0],
  ["f_fight1", "violence", "fighting", "skip", 1150, 1182, 0.81, 10, 1], ["f_gore", "violence", "gore", "skip", 3730, 3751.4, 0.86, 12, 1],
  ["f_weapons", "violence", "weapons", "skip", 5110, 5128, 0.77, 8, 1], ["f_fight2", "violence", "fighting", "skip", 6880, 6930, 0.84, 13, 0],
  ["f_nude1", "sexual", "nudity", "skip", 2600, 2640, 0.89, 15, 0], ["f_nude2", "sexual", "nudity", "skip", 5462, 5480, 0.6, 0, 0, true],
  ["f_innuendo", "sexual", "innuendo", "mute", 4020, 4026, 0.55, 2, 2], ["f_alcohol", "substances", "alcohol", "skip", 1790, 1815, 0.7, 5, 1],
  ["f_drugs", "substances", "drugs", "skip", 4995, 5030, 0.79, 7, 0], ["f_jump", "frightening", "jump scare", "skip", 2890, 2893, 0.83, 9, 1],
  ["f_tension", "frightening", "tension", "skip", 6290, 6355, 0.62, 4, 2]];
export const filterRows = raw.map(([id, category, sub, action, start, end, confidence, up, down, quarantined]) => ({
  ...label(id, category, sub, action, start, end), state: quarantined ? "quarantined" : "active", mergedInto: null, quarantined: !!quarantined,
  severity: category === "violence" ? "high" : category === "language" ? "medium" : "low", confidence, spread: { startMs: -400, endMs: 800 },
  contributorCount: 3, attestations: 4, upvotes: up, downvotes: down, disputes: id === "f_gore" ? 1 : 0, coverageNegatives: 0, lowConfidence: confidence < 0.6,
  served: !quarantined, openReports: id === "f_gore" ? 1 : 0, moderation: { resource: `filter:${id}`, revision: quarantined ? 1 : 0 } }));
export const gore = filterRows.find(row => row.filterId === "f_gore")!;
export const goreLabel = label("f_gore", "violence", "gore", "skip", 3730, 3751.4);
const series = (values: number[], step: number) => values.map((v, index) => ({ t: now - (values.length - index) * step, v }));
const token = (resource: string, revision = 0) => ({ resource, revision });

export const queueItems = [
  { kind: "report", id: "r_gore", at: now - 18 * h, resource: "report:r_gore", revision: 0, target: { type: "filter", id: "f_gore" },
    title: { targetType: "filter", text: "violence › gore 01:02:10–01:02:31 · Arrival Road (1977) · Director's Cut" }, item: items.arrival, version: versions.dc,
    filter: goreLabel, reporter: people.ana, reason: "inaccurate", quote: "Ends about four seconds early; the scene continues until 01:02:35.", suggestion: null,
    impact: { served: 1, confidence: 0.86, upvotes: 12, downvotes: 1, disputes: 1, attestations: 4 }, latestAction: null },
  { kind: "report", id: "r_orchard", at: now - 6 * h, resource: "report:r_orchard", revision: 0, target: { type: "version", id: versions.orchard.versionId },
    title: { targetType: "version", text: "The Quiet Orchard (2003) · Unrated" }, item: items.orchard, version: versions.orchard, filter: null, reporter: people.theo,
    reason: "other", quote: "This is the Theatrical cut with a different label; runtimes match to the second.", suggestion: null,
    impact: { runtimeMs: 6730000, trust: 0.71, uploads: 4, contributors: 3, filters: 22 }, latestAction: null },
  { kind: "alias", id: "as_comet", at: now - 2 * d, resource: "alias_suggestion:as_comet", revision: 0, target: { type: "alias_suggestion", id: "as_comet" },
    title: { targetType: "alias_suggestion", text: "Blue Comet (1984) · Blue Comet: Remastered" }, item: items.comet, version: versions.cometNew, filter: null,
    reporter: null, reason: null, quote: null, suggestion: { text: "Blue Comet: Remastered", normalized: "remastered" }, impact: { versionsWithLabel: 1 }, latestAction: null },
  { kind: "version", id: versions.remaster.versionId, at: now - 3 * d, resource: `version:${versions.remaster.versionId}`, revision: 0,
    target: { type: "version", id: versions.remaster.versionId }, title: { targetType: "version", text: "Arrival Road (1977) · Remastered 4K" }, item: items.arrival,
    version: versions.remaster, filter: null, reporter: null, reason: null, quote: null, suggestion: null,
    impact: { runtimeMs: 8075000, trust: 0.41, uploads: 2, contributors: 2, filters: 29 }, latestAction: null },
  { kind: "disputed", id: "f_tension", at: now - 4 * d, resource: "filter:f_tension", revision: 0, target: { type: "filter", id: "f_tension" },
    title: { targetType: "filter", text: "frightening › tension · Arrival Road (1977) · Director's Cut" }, item: items.arrival, version: versions.dc,
    filter: label("f_tension", "frightening", "tension", "skip", 6290, 6355), reporter: null, reason: null, quote: null, suggestion: null,
    impact: { served: 1, confidence: 0.62, upvotes: 4, downvotes: 2, disputes: 3, attestations: 5 }, latestAction: null },
  { kind: "quarantined", id: "filter:f_nude2", at: now - 6 * d, resource: "filter:f_nude2", revision: 1, target: { type: "filter", id: "f_nude2" },
    title: { targetType: "filter", text: "sexual › nudity · Arrival Road (1977) · Director's Cut" }, item: items.arrival, version: versions.dc,
    filter: label("f_nude2", "sexual", "nudity", "skip", 5462, 5480), reporter: null, reason: "Duplicate of the 01:31:05 filter with wrong bounds", quote: null,
    suggestion: null, impact: { served: 0, confidence: 0.6, upvotes: 0, downvotes: 0, disputes: 0, attestations: 1 },
    latestAction: { actor: "ryan@example.org", command: "quarantine", reason: "Duplicate of the 01:31:05 filter with wrong bounds", at: now - 6 * d } },
];
export const queueCounts = { all: 5, reports: 2, aliases: 1, versions: 1, quarantined: 1, disputed: 1 };
const cause = (who: { contributorId: string; displayName: string }) => ({ kind: "upload", contributor: who });
export const updates = [
  { at: now - 2 * h, item: items.arrival, version: versions.dc, filter: null, revision: 7, generatedAt: now - 2 * h, filters: 31, added: 3, removed: 1, retimed: 0,
    recategorised: 0, cause: cause(people.mira) },
  { at: now - 3 * h, item: items.comet, version: versions.cometNew, filter: null, revision: 1, generatedAt: now - 3 * h, filters: 9, added: 9, removed: 0, retimed: 0,
    recategorised: 0, cause: cause(people.kenji) },
  { at: now - 26 * h, item: items.harbor, version: versions.harbor, filter: null, revision: 5, generatedAt: now - 26 * h, filters: 17, added: 0, removed: 0, retimed: 2,
    recategorised: 0, cause: cause(people.jonas) },
  { at: now - 28 * h, item: items.lanterns, version: versions.lanterns, filter: null, revision: 3, generatedAt: now - 28 * h, filters: 19, added: 0, removed: 38,
    retimed: 0, recategorised: 0, cause: { kind: "moderation", actor: "ryan@example.org", command: "quarantine" } },
  { at: now - 2 * d, item: items.signal, version: versions.signal, filter: null, revision: 6, generatedAt: now - 2 * d, filters: 34, added: 4, removed: 0, retimed: 0,
    recategorised: 0, cause: cause(people.priya) },
];
const leader = (who: { contributorId: string; displayName: string }, rank: number, accepted: number, uploads: number, cast: number, received: number, against: number) =>
  ({ contributor: who, rank, accepted, uploads, votesReceived: received, votesCast: cast, reportsAgainst: against, joinedAt: now - 200 * d, state: "active" });
export const leaders = [leader(people.mira, 1, 142, 18, 311, 486, 3), leader(people.jonas, 2, 118, 14, 402, 371, 1), leader(people.priya, 3, 97, 11, 266, 302, 0),
  leader(people.ana, 4, 71, 9, 188, 210, 4), leader(people.kenji, 5, 64, 8, 95, 133, 0), leader(people.nora, 6, 38, 4, 520, 61, 2),
  leader(people.lena, 7, 35, 6, 140, 88, 3), leader(people.omar, 8, 29, 5, 77, 49, 0), leader(people.theo, 9, 22, 3, 61, 74, 1)];
const contributorActor = (who: { contributorId: string; displayName: string }) => ({ type: "contributor", ...who });
export const activity = [
  { id: "u:sub_mira7", kind: "uploads", at: now - 2 * h, actor: contributorActor(people.mira), verb: "uploaded", item: items.arrival, version: versions.dc, filter: null,
    subject: null, detail: { submissionId: "sub_mira7", entries: 14, state: "counted" } },
  { id: "v:00000000000000000412", kind: "votes", at: now - 3 * h, actor: contributorActor(people.jonas), verb: "voted_up", item: items.comet, version: versions.comet,
    filter: label("f_comet_prof", "language", "profanity", "mute", 724.2, 725), subject: null, detail: { vote: "up" } },
  { id: "v:00000000000000000411", kind: "votes", at: now - 4 * h, actor: contributorActor(people.nora), verb: "voted_down", item: items.signal, version: versions.signal,
    filter: label("f_signal_jump", "frightening", "jump scare", "skip", 2890, 2893), subject: null, detail: { vote: "down" } },
  { id: "m:a-resolve", kind: "moderation", at: now - 6 * h, actor: { type: "operator", subject: "ryan@example.org" }, verb: "resolve_report", item: items.orchard,
    version: versions.orchard, filter: null, subject: null, detail: { resource: "report:r_privacy", resourceType: "report", resourceText: "privacy report", reason: "No personal data in the note" } },
  { id: "u:sub_kenji2", kind: "uploads", at: now - 7 * h, actor: contributorActor(people.kenji), verb: "uploaded", item: items.comet, version: versions.cometNew,
    filter: null, subject: null, detail: { submissionId: "sub_kenji2", entries: 9, state: "counted" } },
  { id: "r:r_gore", kind: "reports", at: now - 21 * h, actor: contributorActor(people.ana), verb: "reported", item: items.arrival, version: versions.dc, filter: goreLabel,
    subject: null, detail: { reportId: "r_gore", reason: "inaccurate", state: "open", targetType: "filter" } },
  { id: "m:a-quarantine", kind: "moderation", at: now - 26 * h, actor: { type: "operator", subject: "ryan@example.org" }, verb: "quarantine", item: items.lanterns,
    version: versions.lanterns, filter: null, subject: null, detail: { resource: "submission:sub_kk", resourceType: "submission", resourceText: "Upload by kk_shift", reason: "40 one-second filters, none match" } },
  { id: "u:sub_lena1", kind: "uploads", at: now - 3 * d, actor: contributorActor(people.lena), verb: "uploaded", item: items.arrival, version: versions.remaster,
    filter: null, subject: null, detail: { submissionId: "sub_lena1", entries: 29, state: "counted" } },
];
const check = (id: string, labelText: string, ok: boolean, detail: string) => ({ id, label: labelText, ok, detail });
export const healthBody = { ready: true, checks: [check("schema", "Database schema", true, "community-master-1 version 5"),
  check("retention", "Hourly maintenance", true, "Last sweep 2 h ago"), check("storage", "Submission storage", true, "112 MiB of 350 MiB used"),
  check("wal", "Write-ahead log", true, "1.9 MiB"), check("backups", "Scheduled backups", true, "Last backup verified 14 h ago")],
  database: { bytes: 40055603, walBytes: 1992294, pageSize: 4096 }, storage: { payloadBytes: 117440512, quotaBytes: 367001600 },
  lastRetention: { runId: "run-ret-1", kind: "retention", startedAt: now - 2 * h, durationMs: 3100, outcome: "ok", detail: { steps: 14, failedSteps: [] } },
  lastRecomputeAll: { runId: "run-rc-1", kind: "recompute_all", startedAt: now - 1.2 * d, durationMs: 41000, outcome: "ok",
    detail: { actor: "ryan@example.org", after: null, versions: 13, nextAfter: null } } };
const kpi = (values: number[], previous: number) => ({ value: values.reduce((a, b) => a + b, 0), previous, series: series(values, d) });
export const homeFixture = { version: 1, observedAt: now, period: "7d",
  kpis: { activeContributors: { value: 29, previous: 25, series: series([22, 24, 23, 26, 25, 27, 29], d) }, uploads: { ...kpi([5, 7, 4, 8, 6, 9, 7], 41), items: 7 },
    votes: { total: kpi([160, 172, 158, 190, 181, 205, 218], 1189), upShare: 0.93 }, openReports: { count: 2, oldestAt: now - 18 * h, versionLevel: 1 },
    lastBackup: { status: "verified", at: now - 14.5 * h, bytes: 43830067, nextAt: now + 9.5 * h } },
  queueCounts, attention: queueItems.slice(0, 5), latestUpdates: updates, topContributors: leaders.slice(0, 5), activity: activity.slice(0, 5),
  health: healthBody };
export { d as dayMs, h as hourMs, token };
