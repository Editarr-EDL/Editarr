import type { Page } from "@playwright/test";
import type { PublishedSubtitle, SubtitleSource } from "../src/api/api-review-subtitles";
import { testPublication } from "../src/review/export-test-fixtures";
import type { FindResult } from "../src/api/api-community-master-types";
import { emptyExport, json } from "./app-fixtures";
import { findResults } from "./community-masters-mock";
import { generatedLibrary, mockLibraryCatalog } from "./library-catalog-mock";
import { posterSvg } from "./website-capture-mock";

/** Capture-only routes for individual website screens: library, subtitles, publishing, accounts and community. */

const titles = ["Example Movie", "Arrival Road", "Blue Comet", "Dune Harbor", "Echo Valley", "Falcon", "Glass Kingdom",
  "Harbor Lights", "Island Story", "Jade Lantern", "Kingdom of Tides", "Lantern Bay", "Meadow Light", "North Star", "Ocean Echo",
  "Paper Moons", "Quiet River", "River Union", "Silver Tide", "Tide & Stone", "Union Square", "Valley Winter", "Winter Road",
  "Yellow Falcon", "Zephyr", "Comet Season", "Dune Echo", "Glass Harbor", "Night Meadow", "Silver Story"];

const palette = [["#1d3b72", "#7a2e5b"], ["#134e5e", "#2f7a5a"], ["#3a1c71", "#b0485f"], ["#283c86", "#3f9fa3"],
  ["#5b2a86", "#2b5876"], ["#7a3b1d", "#c27b2c"], ["#1f4037", "#3d7a5c"], ["#2c3e50", "#4b79a1"]];

/** Serve a Movies library of synthetic titles with generated posters. */
export async function mockWebsiteLibrary(page: Page) {
  const library = generatedLibrary(titles.length).map((item, index) => ({ ...item, name: titles[index], sort_name: titles[index] }));
  await mockLibraryCatalog(page, library);
  await page.route(/\/api\/library-items\/generated-\d+\/image$/, (route) => {
    const index = Number(route.request().url().match(/generated-(\d+)/)?.[1] ?? 0);
    const [from, to] = palette[index % palette.length];
    return route.fulfill({ contentType: "image/svg+xml", body: posterSvg(titles[index], from, to) });
  });
}

const track: SubtitleSource = { id: "english", label: "English", format: "srt", kind: "external", language: "en",
  supported: true, recommended: true, forced: false, sdh: false };
const subtitleSources: SubtitleSource[] = [
  track,
  { id: "english-sdh", label: "English SDH", format: "subrip", kind: "embedded", language: "en", supported: true, recommended: false, forced: false, sdh: true },
  { id: "spanish", label: "Español", format: "srt", kind: "external", language: "es", supported: true, recommended: false, forced: false, sdh: false },
];
const cue = (start: number, original: string, filtered: string) => ({ start_seconds: start, end_seconds: start + 2.4, original, filtered });
const cues = [
  cue(311.8, "Well, damn. That's not what I expected.", "Well, ****. That's not what I expected."),
  cue(844.6, "What the hell are you doing up there?", "What the **** are you doing up there?"),
  cue(1289.7, "Oh my God, look at the bridge!", "Oh my ***, look at the bridge!"),
  cue(1733.2, "Don't be an idiot. Get in the car.", "Don't be an *****. Get in the car."),
  cue(2410.1, "This map is total crap.", "This map is total ****."),
  cue(3014.9, "Damn it, the door's locked.", "**** **, the door's locked."),
];
const title = "Alex - Movie night";
const filename = `example-4k.${title}.en.srt`;

/** Serve subtitle sources, a cue preview and published subtitle files for `reviewId`. */
export async function mockWebsiteSubtitles(page: Page, reviewId: string) {
  const published: PublishedSubtitle[] = [
    { filename, label: title, format: "srt", forced: false, owned_by_viewer: true },
    { filename: "example-4k.Jordan - Family.en.srt", label: "Jordan - Family", format: "srt", forced: false, owned_by_viewer: false },
  ];
  await page.route("**/api/media/subtitles/publications?**", (route) => json(route, { published_subtitles: published }));
  await page.route(`**/api/reviews/${reviewId}/subtitles/sources`, (route) => json(route, {
    sources: subtitleSources, audio_sources: [], audio_source_id: null, fallback: null, published_subtitles: published, track_title: title,
    publication_block_reason: null,
  }));
  await page.route(`**/api/reviews/${reviewId}/subtitles/preview`, (route) => json(route, {
    source: track, cues, changed_cues: cues.length, unchanged_cues: 1184, preview_token: "a".repeat(64), track_title: title, filename,
    publication_block_reason: null,
  }));
}

/** Serve a personal EDL and an item-default MCF published from `reviewId`. */
export async function mockWebsitePublications(page: Page, reviewId: string) {
  const shared = { media_item_id: "media-source-1", media_source_id: "source-1", review_id: reviewId, exported_format_revision: 0 };
  const publications = [
    testPublication({ ...shared, id: 41 }),
    testPublication({ ...shared, id: 42, publication_id: "00000000-0000-4000-8000-000000000042", format: "mcf", scope: "default",
      target: "item_default", target_user_id: null, target_export_id: null }),
  ];
  const current = (format: "edl" | "mcf") => ({ ...emptyExport(format), present: true, current: true, review_id: reviewId,
    exported_at: "2026-11-22T20:20:00Z", exported_format_revision: 0 });
  const empty = { edl: emptyExport("edl"), mcf: emptyExport("mcf") };
  await page.route(/\/api\/exports(\?.*)?$/, (route) => json(route, { publications }));
  await page.route(/\/api\/library-items\/movie-1\/exports(\?.*)?$/, (route) => json(route, {
    exports: { edl: current("edl"), mcf: current("mcf") }, legacy_exports: empty, publications }));
}

const account = (id: number, display_name: string, role: string, extra: Record<string, unknown> = {}) => ({
  id, username: display_name.toLowerCase(), display_name, source: "jellyfin", role, status: "active", disable_cause: null,
  avatar: null, last_login_at: "2026-11-25T21:10:00Z", created_at: "2026-06-02T17:00:00Z",
  overrides: { permissions: false, media_access: false, filter_policy: false },
  language: "en", read_only_fields: ["username", "display_name", "avatar"], available_languages: ["en"], link_cleanup: null, successor_refusal: null,
  ...extra,
});
const accounts = [
  account(1, "Alex", "owner"),
  account(2, "Jordan", "admin", { last_login_at: "2026-11-24T18:42:00Z" }),
  account(3, "Sam", "user", { overrides: { permissions: false, media_access: false, filter_policy: true }, last_login_at: "2026-11-23T02:05:00Z" }),
  account(4, "Riley", "user", { overrides: { permissions: false, media_access: true, filter_policy: false }, last_login_at: "2026-11-22T16:30:00Z" }),
  account(5, "Mia", "user", { overrides: { permissions: true, media_access: false, filter_policy: false }, last_login_at: "2026-11-19T20:15:00Z" }),
  account(6, "Casey", "user", { status: "disabled", disable_cause: "upstream_disabled", last_login_at: "2026-09-30T19:00:00Z" }),
];

/** Serve a household of Jellyfin accounts with roles and overrides. */
export async function mockWebsiteUsers(page: Page) {
  await page.route(/\/api\/users(\?.*)?$/, (route) => json(route, { items: accounts, total: accounts.length, offset: 0, limit: 20 }));
  await page.route(/\/api\/users\/\d+$/, (route) => json(route, accounts.find((item) => item.id === Number(route.request().url().split("/").at(-1)))));
  await page.route("**/api/roles/defaults", (route) => json(route, { admin: [], user: [] }));
  await page.route("**/api/media-access/default", (route) => json(route, { inherited: false, all_sources: true, sources: [] }));
  await page.route("**/api/jellyfin/blocks", (route) => json(route, { blocks: [] }));
  await page.route("**/api/jellyfin/sync", (route) => json(route, { running: false, accounts: [], last_run: null }));
}

const communityVersions: FindResult[] = [
  findResults[0],
  { ...findResults[0], communityVersionId: "v_directors", label: "Director's Cut", aliasId: "directors_cut", tier: "3", matchPercent: 64,
    filterCount: 41, contributorCount: 3, trust: 0.5, alignmentRequired: true },
  findResults[1],
  { ...findResults[0], communityVersionId: "v_imax", label: "IMAX Enhanced", aliasId: null, tier: "3", matchPercent: 48,
    filterCount: 18, contributorCount: 2, trust: 0.4, alignmentRequired: true },
  { ...findResults[0], communityVersionId: "v_remaster", label: "Remastered", aliasId: null, tier: "3", matchPercent: 41,
    filterCount: 29, contributorCount: 4, trust: 0.5, alignmentRequired: true },
  { ...findResults[1], communityVersionId: "v_tv", label: "TV Broadcast", aliasId: null, matchPercent: 22, filterCount: 7 },
];

/** Offer six community versions from Find Filters; register after `mockCommunityMasters`. */
export async function mockWebsiteCommunityVersions(page: Page) {
  await page.route("**/api/community/find", (route) => json(route, { version: 1, findKey: "fk-1", localVersionId: "mv_1",
    keysUsed: ["md5_quick", "tmdb"], results: communityVersions, error: null }));
}
