import { expect, type Page } from "@playwright/test";
import { json } from "./app-fixtures";
import type { FindResult, MasterFilter, MasterLink, MasterPreferences } from "../src/api/api-community-master-types";
import { sharingContextFixture, sharingPreviewFixture } from "../src/scans/sharing-test-fixtures";

const values = (startMs: number, subcategory: string, severity = "high", action = "skip") =>
  ({ startMs, endMs: startMs + 5_050, subcategory, severity, action });
const info = (confidence: number, lowConfidence = false) => ({ spread: { startMs: 300, endMs: 350 }, confidence, contributorCount: 4,
  support: { attestations: 4, upvotes: 1 }, negative: { downvotes: 1, disputes: 0, coverageNegatives: 1 }, lowConfidence, present: true });

export const findResults: FindResult[] = [
  { communityVersionId: "v_theatrical", label: "Theatrical", aliasId: "theatrical", status: "confirmed", unconfirmed: false,
    tier: "2", matchPercent: 80, runtimeAgreement: "same_cut", filterCount: 34, contributorCount: 5, trust: 0.6, revision: 12,
    alignmentRequired: false, linkedLinkId: null },
  { communityVersionId: "v_extended", label: "Extended Edition", aliasId: "extended", status: "candidate", unconfirmed: true,
    tier: "4", matchPercent: 30, runtimeAgreement: null, filterCount: 3, contributorCount: 1, trust: 0.2, revision: 12,
    alignmentRequired: true, linkedLinkId: null },
];

function filter(filterId: string, category: string, startMs: number, subcategory: string, confidence: number,
  overrides: Partial<MasterFilter> = {}, lowConfidence = false, severity = "high"): MasterFilter {
  const master = { ...values(startMs, subcategory, severity), ...info(confidence, lowConfidence) };
  return { filterId, category, master, locallyVerified: false, myVote: null, active: true, ...overrides };
}

/** A served master filter as a saved review includes it: its values and whether it starts enabled. */
export type ServedFilter = { filterId: string; category: string; subcategory: string; severity: string; action: string;
  startMs: number; endMs: number; confidence: number; contributorCount: number; lowConfidence: boolean; startsEnabled: boolean };

export type CommunityMastersMock = {
  requests: { method: string; path: string; body: unknown }[];
  state: { link: MasterLink | null };
  /** The linked set's served filters now, as a saved review includes them. */
  servedFilters: () => ServedFilter[];
};

/**
 * Serve every `/api/community/*` route used by master sets, the daily sync and sharing with a small
 * in-memory account state. Register after `mockApi` so these routes take priority. "Check for filter
 * updates" applies revision 13: Language moves, Nudity is no longer served and a new filter arrives.
 */
export async function mockCommunityMasters(page: Page, options: { linked?: boolean } = {}): Promise<CommunityMastersMock> {
  let preferences: MasterPreferences = { confidenceThreshold: 0, showLowConfidence: false, hideDownvoted: false };
  let notificationsRead = false;
  const linkFor = (result: FindResult): MasterLink => ({ linkId: `link-${result.communityVersionId}`, localVersionId: "mv_1",
    communityVersionId: result.communityVersionId, label: result.label, aliasId: result.aliasId, status: result.status,
    unconfirmed: result.unconfirmed, tier: result.tier, matchPercent: result.matchPercent, appliedRevision: 12, latestRevision: 12,
    alignmentState: result.alignmentRequired ? "required" : "not_required", alignment: null, alignmentRevision: null,
    linkState: "active", mergedInto: null });
  let filters = [
    filter("f_fight", "violence", 600_000, "violence", 0.8, { locallyVerified: true, myVote: "up" }),
    filter("f_lang", "language", 900_000, "swearing", 0.3, {}, false, "medium"),
    filter("f_gone", "nudity", 1_200_000, "nudity", 0.2, {}, true),
  ];
  // The backend rule for which filters start enabled (scanner/modeling/community_review_filters.py).
  const startsEnabled = (item: MasterFilter) => (!item.master.lowConfidence || !!preferences.showLowConfidence)
    && item.master.confidence >= preferences.confidenceThreshold;
  const active = (item: MasterFilter) => mock.state.link?.alignmentState !== "required" && item.master.present && startsEnabled(item);
  const mock: CommunityMastersMock = { requests: [], state: { link: options.linked ? { ...linkFor(findResults[1]), alignmentState: "approved",
    alignment: { offsetMs: 0, scale: 1, anchors: 0 }, alignmentRevision: 12 } : null },
  servedFilters: () => filters.map((item) => ({ filterId: item.filterId, category: item.category,
    subcategory: item.master.subcategory ?? "", severity: item.master.severity ?? "", action: item.master.action,
    startMs: item.master.startMs, endMs: item.master.endMs, confidence: item.master.confidence,
    contributorCount: item.master.contributorCount, lowConfidence: item.master.lowConfidence, startsEnabled: startsEnabled(item) })) };
  const link = () => mock.state.link!;

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
      if (method === "PATCH") preferences = { confidenceThreshold: body.confidence_threshold, showLowConfidence: body.show_low_confidence,
        hideDownvoted: body.hide_downvoted };
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
        const changed = mock.state.link.latestRevision < 13;
        if (changed) {
          filters = [filters[0], filter("f_lang", "language", 901_000, "swearing", 0.4, {}, false, "high"),
            filter("f_new", "violence", 1_800_000, "violence", 0.7)];
          mock.state.link = { ...mock.state.link, appliedRevision: 13, latestRevision: 13 };
        }
        return json(route, { version: 1, changed, latestRevision: 13 });
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
