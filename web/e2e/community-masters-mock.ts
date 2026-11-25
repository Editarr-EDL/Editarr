import { expect, type Page } from "@playwright/test";
import { json } from "./app-fixtures";
import type {
  FindResult, MasterFilter, MasterLink, MasterPreferences, MasterUpdate, UpdateCounts, UpdateRow,
} from "../src/api/api-community-master-types";
import { sharingContextFixture, sharingPreviewFixture } from "../src/scans/sharing-test-fixtures";

const values = (startMs: number, subcategory: string, severity = "high", action = "skip") =>
  ({ startMs, endMs: startMs + 5_050, subcategory, severity, action });
const info = (confidence: number, lowConfidence = false) => ({ spread: { startMs: 300, endMs: 350 }, confidence, contributorCount: 4,
  support: { attestations: 4, upvotes: 1 }, negative: { downvotes: 1, disputes: 0, coverageNegatives: 1 }, lowConfidence, present: true });
const noUpdate: UpdateCounts = { new: 0, changed: 0, removed: 0, nowLowConfidence: 0 };

export const findResults: FindResult[] = [
  { communityVersionId: "v_theatrical", label: "Theatrical", aliasId: "theatrical", status: "confirmed", unconfirmed: false,
    tier: "2", matchPercent: 80, runtimeAgreement: "same_cut", filterCount: 34, contributorCount: 5, trust: 0.6, revision: 12,
    alignmentRequired: false, linkedLinkId: null },
  { communityVersionId: "v_extended", label: "Extended Edition", aliasId: "extended", status: "candidate", unconfirmed: true,
    tier: "4", matchPercent: 30, runtimeAgreement: null, filterCount: 3, contributorCount: 1, trust: 0.2, revision: 12,
    alignmentRequired: true, linkedLinkId: null },
];

function filter(filterId: string, category: string, startMs: number, subcategory: string, confidence: number,
  overrides: Partial<MasterFilter> = {}, lowConfidence = false): MasterFilter {
  const master = { ...values(startMs, subcategory), ...info(confidence, lowConfidence) };
  return { filterId, category, master, local: null, effective: values(startMs, subcategory),
    state: lowConfidence ? "low_confidence" : "pending", touched: false, locallyVerified: false, myVote: null, active: true, ...overrides };
}

function updateRow(rowId: string, category: string, overrides: Partial<UpdateRow>): UpdateRow {
  return { rowId, filterId: rowId, category, touched: false, changedFields: [], mine: null, oldMaster: null, newMaster: null,
    mergedInto: null, allowed: ["accept", "dismiss"], canAccept: true, ...overrides };
}
const baseline = (startMs: number, subcategory: string, severity = "high") =>
  ({ ...values(startMs, subcategory, severity), lowConfidence: false, present: true });
const newMaster = (startMs: number, subcategory: string, severity = "high") =>
  ({ ...values(startMs, subcategory, severity), ...info(0.7) });

/** The pending update a check finds: one new row, two changed rows (one the user edited) and one removed row. */
export function pendingUpdate(): MasterUpdate {
  return { version: 1, fromRevision: 12, toRevision: 13, alignmentRequired: true, alignmentApproved: true, groups: {
    new: [updateRow("f_new", "violence", { changedFields: ["startMs", "endMs", "subcategory", "severity", "action"],
      newMaster: newMaster(1_800_000, "punching") })],
    changed: [
      updateRow("f_lang", "language", { changedFields: ["severity"], oldMaster: baseline(900_000, "profanity", "medium"),
        newMaster: newMaster(900_000, "profanity", "high") }),
      updateRow("f_fight", "violence", { touched: true, changedFields: ["startMs"], allowed: ["accept", "keep_mine", "dismiss"],
        mine: values(600_100, "punching", "medium"), oldMaster: baseline(600_000, "punching"), newMaster: newMaster(601_000, "punching") }),
    ],
    removed: [updateRow("f_gone", "nudity", { changedFields: ["present"], oldMaster: baseline(1_200_000, "nudity") })],
    nowLowConfidence: [],
  } };
}

export type CommunityMastersMock = { requests: { method: string; path: string; body: unknown }[]; state: { link: MasterLink | null } };

/**
 * Serve every `/api/community/*` route used by master sets, the daily sync and sharing with a small
 * in-memory account state. Register after `mockApi` so these routes take priority.
 */
export async function mockCommunityMasters(page: Page, options: { linked?: boolean } = {}): Promise<CommunityMastersMock> {
  let preferences: MasterPreferences = { confidenceThreshold: 0, showLowConfidence: false };
  let counts = noUpdate;
  let update = pendingUpdate();
  let notificationsRead = false;
  const linkFor = (result: FindResult): MasterLink => ({ linkId: `link-${result.communityVersionId}`, localVersionId: "mv_1",
    communityVersionId: result.communityVersionId, label: result.label, aliasId: result.aliasId, status: result.status,
    unconfirmed: result.unconfirmed, tier: result.tier, matchPercent: result.matchPercent, appliedRevision: 12, latestRevision: 12,
    alignmentState: result.alignmentRequired ? "required" : "not_required", alignment: null, alignmentRevision: null,
    pendingUpdate: noUpdate, linkState: "active", mergedInto: null });
  const mock: CommunityMastersMock = { requests: [], state: { link: options.linked ? { ...linkFor(findResults[1]), alignmentState: "approved",
    alignment: { offsetMs: 0, scale: 1, anchors: 0 }, alignmentRevision: 12 } : null } };
  const filters = [
    filter("f_fight", "violence", 600_000, "punching", 0.8, { state: "verified", touched: true, locallyVerified: true, myVote: "up" }),
    filter("f_lang", "language", 900_000, "profanity", 0.3),
    filter("f_gone", "nudity", 1_200_000, "nudity", 0.2, {}, true),
  ];
  // The backend `is_active` rule (scanner/modeling/community_master_state.py).
  const inEffect = ["pending", "verified", "adjusted"];
  const active = (item: MasterFilter) => {
    if (mock.state.link?.alignmentState === "required" || !item.master.present) return false;
    if (item.touched) return inEffect.includes(item.state);
    if (item.master.lowConfidence) {
      if (!preferences.showLowConfidence || ![...inEffect, "low_confidence"].includes(item.state)) return false;
    } else if (!inEffect.includes(item.state)) return false;
    return item.master.confidence >= preferences.confidenceThreshold;
  };
  const link = () => ({ ...mock.state.link!, pendingUpdate: counts });
  const remaining = () => ({ ...update, groups: { ...update.groups } });
  const decide = (rowId: string) => {
    update = { ...update, groups: Object.fromEntries(Object.entries(update.groups).map(([group, rows]) =>
      [group, rows.filter((row) => row.rowId !== rowId)])) as MasterUpdate["groups"] };
  };

  await page.route(/\/api\/community\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/community\//, "");
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    if (method !== "GET") expect(request.headers()["x-csrf-token"]).toBe("test-csrf-token");
    mock.requests.push({ method, path: `${path}${url.search}`, body });
    if (path === "aliases") return json(route, { version: 1, choices: [
      { kind: "alias", aliasId: "theatrical", label: "Theatrical" }, { kind: "alias", aliasId: "directors_cut", label: "Director's Cut" },
      { kind: "other", label: "Other" }] });
    if (path === "sync/run" || path === "sync") return json(route, { version: 1, ran: path === "sync/run", state: "idle",
      lastRunAt: 1795553648, nextDueAt: 1795640048, lastError: null, unreadCount: notificationsRead ? 0 : 1, notifications: [
        { notificationId: "n1", linkId: "link-v_extended", libraryId: "movies", itemId: "movie-1", mediaSourceId: "source-2",
          title: "Example Movie", kind: "changed", fromRevision: 12, toRevision: 13, createdAt: 1795553648, read: notificationsRead }] });
    if (path === "sync/notifications/read") { notificationsRead = true; return route.fulfill({ status: 204 }); }
    if (path === "find") return json(route, { version: 1, findKey: "fk-1", localVersionId: "mv_1", keysUsed: ["md5_quick", "tmdb"],
      results: findResults.map((result) => ({ ...result, linkedLinkId: mock.state.link?.communityVersionId === result.communityVersionId
        ? mock.state.link.linkId : null })), error: null });
    if (path === "masters" && method === "POST") {
      const result = findResults.find((item) => item.communityVersionId === body.community_version_id)!;
      if (mock.state.link && !body.replace) return json(route, { code: "replace_required", retryable: false }, 409);
      mock.state.link = linkFor(result);
      return json(route, { version: 1, link: link() });
    }
    if (path === "masters" && method === "GET") return json(route, { version: 1, links: mock.state.link ? [link()] : [] });
    if (path === "preferences") {
      if (method === "PATCH") preferences = { confidenceThreshold: body.confidence_threshold, showLowConfidence: body.show_low_confidence };
      return json(route, { version: 1, ...preferences });
    }
    const linkPath = path.match(/^masters\/([^/]+)(?:\/(.*))?$/);
    if (linkPath && mock.state.link) {
      const action = linkPath[2] ?? "";
      if (action === "") return json(route, { version: 1, link: link(), preferences,
        filters: filters.map((item) => ({ ...item, active: active(item) })) });
      if (action === "alignment/preview") return json(route, { version: 1, token: "align-1",
        alignment: { offsetMs: body.offset_ms ?? 0, scale: 1, anchors: 0 }, rows: filters.map((item) => ({ filterId: item.filterId,
          alignedStartMs: item.master.startMs + (body.offset_ms ?? 0), alignedEndMs: item.master.endMs + (body.offset_ms ?? 0) })) });
      if (action === "alignment/approve") {
        mock.state.link = { ...mock.state.link, alignmentState: "approved", alignment: { offsetMs: 0, scale: 1, anchors: 0 }, alignmentRevision: 12 };
        return json(route, { version: 1, link: link() });
      }
      if (action.endsWith("/vote")) return json(route, { version: 1, state: "completed", result: { accepted: true } });
      if (action === "check") {
        counts = { new: 1, changed: 2, removed: 1, nowLowConfidence: 0 };
        return json(route, { version: 1, changed: true, latestRevision: 13, pendingUpdate: counts });
      }
      if (action === "update") return json(route, remaining());
      if (action === "update/accept-all") {
        for (const row of update.groups[body.group as keyof MasterUpdate["groups"]]) if (!row.touched && row.canAccept) decide(row.rowId);
        return json(route, remaining());
      }
      if (action === "update/decisions") {
        for (const decision of body.decisions) decide(decision.row_id);
        return route.fulfill({ status: 204 });
      }
    }
    if (path.startsWith("sharing/")) {
      if (path.endsWith("/preview")) return json(route, { ...sharingPreviewFixture(), version: 1,
        estimates: { approximate: true, newFilters: 4, confirmations: 27, adjustments: 3, disputes: 1 },
        versionChoice: body.version_choice.choice === "other"
          ? { choice: "other", text: body.version_choice.text, label: body.version_choice.text ?? "Other" }
          : { choice: "alias", aliasId: body.version_choice.alias_id, label: "Theatrical" },
        work: { kind: "movie", tmdb: "603", tvdb: null, title: "Example Movie", year: 2025 }, entryCount: 35, warnings: [] });
      if (method === "DELETE") return json(route, { discarded: true });
      return json(route, { ...sharingContextFixture(), jobs: [] });
    }
    return json(route, { detail: "Not Found" }, 404);
  });
  return mock;
}
