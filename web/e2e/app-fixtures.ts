import { CAPABILITIES } from "../src/auth/capabilities";
import type { AuditEntry } from "../src/api/api-audit";
import { expect, type Page, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

export const auditEvent: AuditEntry = {
  event_id: "00000000-0000-4000-8000-000000000001", occurred_at: "2026-08-19T00:00:00Z",
  category: "security", action: "api_key.issue", outcome: "succeeded", actor_kind: "user",
  actor_id: 1, credential: "session", target_user_id: 2, media_key: null, resource: null, details: {},
};

export const libraries = [{ id: "movies", name: "Movies", collection_type: "movies", locations: ["/media/movies"] }];
export const emptyExport = (format: "edl" | "mcf") => ({
  format, present: false, exported_at: null, review_id: null,
  exported_format_revision: null, current: false, outdated: false,
  exported_scans: [], exported_review: null,
});
export const emptyExports = () => ({ exports: { edl: emptyExport("edl"), mcf: emptyExport("mcf") } });
export const previousEdl = {
  ...emptyExports(),
  exports: {
    ...emptyExports().exports,
    edl: {
      ...emptyExport("edl"), present: true, exported_at: "2026-08-19T00:00:00Z",
      current: true, exported_scans: [{ id: "scan-previous", scan_id: 2, role: "both" }],
    },
  },
};
export const items = [{
  id: "movie-1", library_id: "movies", parent_id: null, item_type: "Movie", name: "Example Movie",
  sort_name: "Example Movie", year: 2025, overview: "A test movie.", path: "/media/movies/example.mkv",
  official_rating: "PG-13", index_number: null,
  runtime_ticks: null, duration_seconds: 5700, resolution: "4K", media_format: "MKV · HEVC", media_source_count: 2,
  versions: [
    { id: "source-1", name: "4K HDR", scene_name: "Example.Movie.2025.2160p.WEB-DL-GROUP", release_group: "GROUP", edition: "Director's Cut", path: "/media/movies/example-4k.mkv", file_name: "example-4k.mkv", audio_track_count: 2, container: "mkv", resolution: "4K", media_format: "MKV · HEVC", duration_seconds: 5700, size_bytes: 10_000_000_000, exports: previousEdl },
    { id: "source-2", name: "1080p SDR", scene_name: "Example.Movie.2025.1080p.WEB-DL-OTHER", release_group: "OTHER", edition: null, path: "/media/movies/example-1080p.mkv", file_name: "example-1080p.mkv", audio_track_count: 0, container: "mkv", resolution: "1080p", media_format: "MKV · H264", duration_seconds: 5700, size_bytes: 5_000_000_000, exports: emptyExports() },
  ],
  image_tags: { Primary: "poster-tag" }, image_url: "/api/library-items/movie-1/image",
  exports: previousEdl,
}];
export const series = { ...items[0], id: "series-1", item_type: "Series", name: "Example Series", sort_name: "Example Series", overview: "A series." };
export const season = { ...items[0], id: "season-1", parent_id: "series-1", item_type: "Season", name: "Season 1", sort_name: "Season 1", overview: null };
export const episode = { ...items[0], id: "episode-1", parent_id: "season-1", item_type: "Episode", name: "Episode 1", sort_name: "Episode 1", overview: "An episode." };
export interface MockReviewFilter {
  id: string; source_scan_id: string; source_finding_id: number | null;
  source: "audio" | "visual"; category: "language" | "nudity" | "violence" | null;
  subcategory: string | null; severity: "low" | "medium" | "high" | null;
  mcf_channel: "both" | "video" | "audio" | null; mcf_comment: string | null;
  label: string | null; details: string | null; group_key: string | null;
  start_seconds: number; end_seconds: number | null; confidence: number;
  edit_action: "mute" | "skip"; selected: boolean;
}

export interface MockReview {
  id: string; media_item_id: string;
  target: { source: "jellyfin"; item_id: string; media_source_id: string | null };
  name: string; revision: number; content_revision: number; edl_content_revision: number; mcf_content_revision: number; created_at: string; updated_at: string;
  exports: ReturnType<typeof emptyExports>;
  memberships: Array<{ scan_id: string; role: "audio" | "video" | "both" }>;
  scans: unknown[]; filters: MockReviewFilter[];
  mcf_document: { title: string | null; year: number | null; work_type: "movie" | "series" | "episode" | null; season: number | null; episode: number | null; source_url: string | null; imdb_url: string | null; release: string | null; comment: string | null; material_start_seconds: number | null; material_end_seconds: number | null };
  synchronization: { start_seconds: number; end_seconds: number; end_mode: "before_credits" | "file_end" };
  accepted_credits: null;
}

export function mockReview(
  id: string,
  name: string,
  mediaSourceId: string | null,
  filters: MockReviewFilter[] = [],
): MockReview {
  return {
    id, media_item_id: `media-${mediaSourceId || "item"}`,
    target: { source: "jellyfin", item_id: "movie-1", media_source_id: mediaSourceId },
    name, revision: 0, content_revision: 0, edl_content_revision: 0, mcf_content_revision: 0,
    created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
    exports: emptyExports(),
    memberships: [], scans: [], filters,
    mcf_document: { title: name, year: 2025, work_type: "movie", season: null, episode: null, source_url: null, imdb_url: null, release: null, comment: null, material_start_seconds: 0, material_end_seconds: 5700 },
    synchronization: { start_seconds: 0, end_seconds: 5700, end_mode: "before_credits" },
    accepted_credits: null,
  };
}

export async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** The status the server answers with; signed-in responses carry the owner's capabilities. */
export function authenticationStatus(state: "setup_required" | "unauthenticated" | "authenticated") {
  const signedIn = state === "authenticated";
  return {
    state,
    setup_required: state === "setup_required",
    sign_in: { jellyfin: true },
    display_name: signedIn ? "owner" : null,
    access: signedIn ? {
      version: "editarr-capabilities-9",
      permission_revision: 1,
      principal: { user_id: 1, role: "owner", credential: "session", status: "active", link_state: "verified" },
      capabilities: [...CAPABILITIES],
    } : null,
    csrf_token: signedIn ? "test-csrf-token" : null,
    restart_required: false,
  };
}

/** Sign out through the sidebar account menu. */
export async function signOutThroughMenu(page: Page) {
  await page.getByRole("button", { name: /^Account menu for / }).click();
  await page.getByRole("menuitem", { name: "Sign Out" }).click();
}

export async function expectNoAccessibilityViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations,
    results.violations.map((violation) => (
      `${violation.id}: ${violation.help} (${violation.nodes.map((node) => node.target.join(" ")).join(", ")})`
    )).join("\n"),
  ).toEqual([]);
}

export async function expectNoHorizontalPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
}

export async function expectNoAuthenticationSecretsInBrowser(page: Page, values: string[]) {
  const browserValues = await page.evaluate(() => ({
    url: window.location.href,
    text: document.body.textContent || "",
    localStorage: Object.values(window.localStorage).join("\n"),
    sessionStorage: Object.values(window.sessionStorage).join("\n"),
    readableCookies: document.cookie,
  }));
  for (const value of values) {
    expect(browserValues.url).not.toContain(value);
    expect(browserValues.text).not.toContain(value);
    expect(browserValues.localStorage).not.toContain(value);
    expect(browserValues.sessionStorage).not.toContain(value);
    expect(browserValues.readableCookies).not.toContain(value);
  }
}

export async function mockApi(page: Page) {
  let setupCompleted = false;
  let loginCompleted = false;
  let sessionAuthenticated = true;
  let mappings: Array<{ id: number; remote_path: string; local_path: string; sort_order: number; created_at: string; updated_at: string }> = [];
  let reviews: MockReview[] = [];
  let nextReviewId = 1;
  let modelState = "not_downloaded";
  let defaultFilterMode = "live";
  let personalFilters = { user_id: 1, mode: "live", mode_locked: false, revision: 1, policy_revision: 1, instance_revision: 0,
    rules: [] as unknown[], severities: [] as unknown[], enforced_rules: [] as string[], enforced_severities: [] as string[] };
  const scanScope = { library_id: "movies", media_source_id: "source-1", is_own: true };
  let queuedScans = [{
    ...scanScope,
    id: "scan-1", scan_id: 1, media_item_id: "media-1", jellyfin_item_id: "movie-1",
    item_name: "Example Movie", item_type: "Movie", status: "queued", scan_audio: true,
    audio_model: "whisper", scan_video: true, video_model: "nudenet", frame_interval: 1,
    speed_up: false, created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
    queue_id: 1, sort_order: 0, error_message: null,
  }];
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const pageQuery = new URL(page.url()).searchParams;
    if (url.pathname === "/api/auth/status") {
      const setupRequired = !setupCompleted && pageQuery.has("setup");
      const unauthenticated = (!loginCompleted && pageQuery.has("login")) || !sessionAuthenticated;
      await json(route, authenticationStatus(
        setupRequired ? "setup_required" : unauthenticated ? "unauthenticated" : "authenticated",
      )); return;
    }
    if (url.pathname === "/api/auth/setup" && method === "POST") {
      if (pageQuery.has("setup-conflict")) {
        setupCompleted = true;
        sessionAuthenticated = false;
        await json(route, { code: "setup_already_complete", detail: "Administrator setup is already complete." }, 409); return;
      }
      if (pageQuery.has("setup-not-admin")) {
        await json(route, { code: "jellyfin_not_administrator", detail: "Only a Jellyfin administrator can set up Editarr" }, 403); return;
      }
      setupCompleted = true;
      sessionAuthenticated = true;
      await json(route, authenticationStatus("authenticated"), 201); return;
    }
    if (url.pathname === "/api/auth/jellyfin/quick-connect" && method === "GET") {
      await json(route, { enabled: false }); return;
    }
    if (url.pathname === "/api/auth/jellyfin/login" && method === "POST") {
      if (pageQuery.has("login-error")) {
        await json(route, { code: "invalid_credentials", detail: "Incorrect username or password." }, 401); return;
      }
      loginCompleted = true;
      sessionAuthenticated = true;
      await json(route, authenticationStatus("authenticated")); return;
    }
    if (url.pathname === "/api/auth/logout" && method === "POST") {
      sessionAuthenticated = false;
      await json(route, authenticationStatus("unauthenticated")); return;
    }
    const setupBlocked = !setupCompleted && pageQuery.has("setup");
    if (setupBlocked || !sessionAuthenticated) {
      await json(route, { code: setupBlocked ? "setup_required" : "authentication_required" }, setupBlocked ? 423 : 401); return;
    }
    if (pageQuery.has("session-expiry") && url.pathname === "/api/settings/jellyfin" && method === "GET") {
      sessionAuthenticated = false;
      await json(route, { code: "authentication_required", detail: "Authentication is required" }, 401); return;
    }
    if (url.pathname === "/api/settings/jellyfin" && method === "GET") {
      await json(route, { server_url: "http://jellyfin.test", api_key_configured: true, last_checked_at: null, last_error: null }); return;
    }
    if (url.pathname === "/api/community/downloads/status") {
      const body = route.request().postDataJSON();
      await json(route, { version: 1, outcomes: body.choices.map((choice: { revisionId: string }) => ({ revisionId: choice.revisionId, status: "not_imported" })) }); return;
    }
    if (url.pathname === "/api/mcf-registry") {
      await json(route, { families: [
        { token: "language", label: "Language", categories: [{ token: "swearing", label: "Swearing" }] },
        { token: "nudity", label: "Nudity", categories: [{ token: "nudity", label: "Nudity (General)" }] },
        { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }] },
      ] }); return;
    }
    if (url.pathname === "/api/settings/jellyfin" && method === "PUT") {
      await json(route, { server_url: "http://jellyfin.test", api_key_configured: true, last_checked_at: null, last_error: null }); return;
    }
    if (url.pathname === "/api/settings/jellyfin/test") {
      await json(route, { server_url: "http://jellyfin.test", api_key_configured: true, last_checked_at: null, last_error: null }); return;
    }
    if (url.pathname === "/api/audit") {
      await json(route, { events: [auditEvent], total: 1, page: 1, page_size: 25, pages: 1, retained_since: auditEvent.occurred_at, retention_days: 90 }); return;
    }
    if (url.pathname === `/api/audit/${auditEvent.event_id}`) {
      await json(route, auditEvent); return;
    }
    if (url.pathname === "/api/settings/filters/default-mode") {
      if (method === "PUT") defaultFilterMode = route.request().postDataJSON().mode;
      await json(route, { mode: defaultFilterMode }); return;
    }
    if (url.pathname === "/api/settings/filters/personal/mode" && method === "PUT") {
      personalFilters = { ...personalFilters, mode: route.request().postDataJSON().mode, revision: personalFilters.revision + 1 };
      await json(route, personalFilters); return;
    }
    if (url.pathname === "/api/settings/filters/personal") {
      if (method === "PUT") personalFilters = { ...personalFilters, revision: personalFilters.revision + 1 };
      await json(route, personalFilters); return;
    }
    if (url.pathname === "/api/settings/filters" && method === "GET") {
      await json(route, { revision: 0, selection_rules: [], severity_defaults: [] }); return;
    }
    if (url.pathname === "/api/capabilities") { await json(route, { audio_models: [{ id: "whisper", display_name: "Whisper" }], audio_filters: [{ id: "better_profanity", display_name: "better-profanity" }], video_models: [{ id: "nudenet", display_name: "NudeNet" }], qwen_models: [] }); return; }
    if (url.pathname === "/api/settings/models/scans") {
      await json(route, { audio_padding_seconds: 0.1 }); return;
    }
    if (url.pathname === "/api/models") {
      await json(route, { artifacts: [{ artifact_id: "faster-whisper-base", backend_ids: ["whisper"], state: { state: modelState, bytes_downloaded: modelState === "downloading" ? 50 : modelState === "ready" ? 100 : null, bytes_total: 100, error_code: null, error_detail: null }, expected_size: 100, source_url: "https://example.test/model", license_spdx: "MIT", license_status: "declared", removable: true }], model_capabilities: [{ id: "whisper", display_name: "Whisper", stage: "audio", availability: "available", reason: null, device: "cpu", weight_requirement: "on_demand", download: { state: modelState, bytes_downloaded: null, bytes_total: 100, error_code: null, error_detail: null }, enrichment_behavior: "none" }] }); return;
    }
    if (url.pathname === "/api/models/prefetch" && method === "POST") {
      modelState = "downloading";
      await json(route, { status: { artifacts: [{ artifact_id: "faster-whisper-base", backend_ids: ["whisper"], state: { state: "downloading", bytes_downloaded: 50, bytes_total: 100, error_code: null, error_detail: null }, expected_size: 100, source_url: "https://example.test/model", license_spdx: "MIT", license_status: "declared", removable: true }], model_capabilities: [{ id: "whisper", display_name: "Whisper", stage: "audio", availability: "available", reason: null, device: "cpu", weight_requirement: "on_demand", download: { state: "downloading", bytes_downloaded: 50, bytes_total: 100, error_code: null, error_detail: null }, enrichment_behavior: "none" }] }, accepted: true, removed: false }, 202); return;
    }
    if (url.pathname === "/api/models/faster-whisper-base/cancel" && method === "POST") {
      modelState = "cancelled";
      await json(route, { status: { artifacts: [], model_capabilities: [] }, accepted: true, removed: false }); return;
    }
    if (url.pathname === "/api/models/faster-whisper-base" && method === "DELETE") {
      modelState = "not_downloaded";
      await json(route, { status: { artifacts: [], model_capabilities: [] }, accepted: false, removed: true }); return;
    }
    if (url.pathname === "/api/libraries") { await json(route, libraries); return; }
    if (url.pathname === "/api/libraries/movies/items") {
      const filteredItems = items.filter((item) =>
        url.searchParams.get("search") !== "missing"
        && (url.searchParams.get("edl_filter") !== "not_exported" || !item.exports.exports.edl.present),
      );
      await json(route, { items: filteredItems, total_count: filteredItems.length, rating_values: ["PG-13"], resolution_values: ["4K"] }); return;
    }
    if (url.pathname === "/api/library-items/movie-1" && method === "GET") { await json(route, items[0]); return; }
    if (url.pathname === "/api/library-items/series-1" && method === "GET") { await json(route, series); return; }
    if (url.pathname === "/api/library-items/season-1" && method === "GET") { await json(route, season); return; }
    if (url.pathname === "/api/library-items/episode-1" && method === "GET") { await json(route, episode); return; }
    if (url.pathname === "/api/library-items/series-1/seasons") { await json(route, [season]); return; }
    if (url.pathname === "/api/library-items/season-1/children") { await json(route, [episode]); return; }
    if (url.pathname === "/api/library-items/movie-1/exports/edl" && method === "DELETE") { await route.fulfill({ status: 204 }); return; }
    if (url.pathname === "/api/library-items/movie-1/scans") {
      await json(route, [{
        ...scanScope,
        id: "scan-previous", scan_id: 2, media_item_id: "media-1", jellyfin_item_id: "movie-1",
        item_name: "Example Movie", item_type: "Movie", media_source_name: "4K HDR", status: "complete", scan_audio: true,
        audio_model: "whisper", scan_video: true, video_model: "nudenet", frame_interval: 1,
        speed_up: false, error_message: null, created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
      }]);
      return;
    }
    if (url.pathname === "/api/scans/scan-previous/findings" && method === "GET") {
      await new Promise((resolve) => setTimeout(resolve, 50));
      await json(route, { revision: 0, findings: [{
        id: 10, scan_id: "scan-previous", source: "visual", category: "nudity", subcategory: "nudity", severity: "medium", mcf_channel: "video", mcf_comment: null, label: "Visual content",
        details: null, group_key: null, start_seconds: 10, end_seconds: 18, confidence: 0.9,
        edit_action: "skip", selected: true,
      }] }); return;
    }
    if (url.pathname === "/api/library-items/movie-1/reviews" && method === "GET") {
      const mediaSourceId = url.searchParams.get("media_source_id");
      await json(route, reviews.filter((review) => review.target.media_source_id === mediaSourceId)); return;
    }
    if (url.pathname === "/api/library-items/movie-1/reviews" && method === "POST") {
      const payload = route.request().postDataJSON() as {
        name: string; media_source_id: string | null; memberships: MockReview["memberships"];
        filters: MockReviewFilter[]; new_manual_filters: Array<Omit<MockReviewFilter, "source_scan_id" | "source_finding_id">>;
      };
      const reviewId = `review-${nextReviewId++}`;
      const manualFilters = payload.new_manual_filters.map((filter, index) => ({
        ...filter, id: `${reviewId}-manual-${index}`, source_scan_id: `${reviewId}-manual-scan`, source_finding_id: index + 100,
      }));
      const manualMemberships = manualFilters.map((filter) => ({
        scan_id: filter.source_scan_id,
        role: filter.source === "audio" ? "audio" as const : "video" as const,
      }));
      const created = mockReview(reviewId, payload.name, payload.media_source_id, [...payload.filters, ...manualFilters]);
      created.memberships = [...payload.memberships, ...manualMemberships];
      reviews = [created, ...reviews];
      await json(route, created, 201); return;
    }
    const reviewRoute = url.pathname.match(/^\/api\/reviews\/([^/]+)$/)?.[1];
    if (reviewRoute && method === "GET") {
      const review = reviews.find((candidate) => candidate.id === reviewRoute);
      await json(route, review || { detail: "Review not found" }, review ? 200 : 404); return;
    }
    if (reviewRoute && method === "PUT") {
      const payload = route.request().postDataJSON() as {
        name: string; memberships: MockReview["memberships"]; filters: MockReviewFilter[];
        new_manual_filters: Array<Omit<MockReviewFilter, "source_scan_id" | "source_finding_id">>;
      };
      const review = reviews.find((candidate) => candidate.id === reviewRoute)!;
      const sourceFilters = payload.filters;
      const manualFilters = payload.new_manual_filters.map((filter, index) => ({
        ...filter, id: `${review.id}-manual-${index}`, source_scan_id: `${review.id}-manual-scan`, source_finding_id: null,
      }));
      const manualMemberships = manualFilters.map((filter) => ({
        scan_id: filter.source_scan_id,
        role: filter.source === "audio" ? "audio" as const : "video" as const,
      }));
      Object.assign(review, {
        name: payload.name, memberships: [...payload.memberships, ...manualMemberships], filters: [...sourceFilters, ...manualFilters],
        revision: review.revision + 1, content_revision: review.content_revision + 1,
        updated_at: "2026-08-20T00:00:00Z",
        exports: { exports: { ...review.exports.exports, edl: { ...review.exports.exports.edl, current: false, outdated: review.exports.exports.edl.present } } },
      });
      await json(route, review); return;
    }
    if (reviewRoute && method === "PATCH") {
      const payload = route.request().postDataJSON() as { name: string };
      const review = reviews.find((candidate) => candidate.id === reviewRoute)!;
      Object.assign(review, { name: payload.name, revision: review.revision + 1, updated_at: "2026-08-21T00:00:00Z" });
      await json(route, review); return;
    }
    if (reviewRoute && method === "DELETE") {
      reviews = reviews.filter((candidate) => candidate.id !== reviewRoute);
      await route.fulfill({ status: 204 }); return;
    }
    const reviewDuplicate = url.pathname.match(/^\/api\/reviews\/([^/]+)\/duplicate$/)?.[1];
    if (reviewDuplicate && method === "POST") {
      const payload = route.request().postDataJSON() as { name: string };
      const sourceReview = reviews.find((candidate) => candidate.id === reviewDuplicate)!;
      const duplicate = mockReview(`review-${nextReviewId++}`, payload.name, sourceReview.target.media_source_id, sourceReview.filters.map((filter) => ({ ...filter, id: `${filter.id}-copy` })));
      duplicate.memberships = structuredClone(sourceReview.memberships);
      reviews = [duplicate, ...reviews];
      await json(route, duplicate, 201); return;
    }
    const reviewExport = url.pathname.match(/^\/api\/reviews\/([^/]+)\/edl$/)?.[1];
    if (reviewExport && method === "POST") {
      const review = reviews.find((candidate) => candidate.id === reviewExport)!;
      const exportedScans = [{ id: "scan-previous", scan_id: 2, role: "both" }];
      review.exports = { exports: { ...review.exports.exports, edl: { ...emptyExport("edl"), present: true, exported_at: "2026-08-20T00:00:00Z", review_id: review.id, exported_format_revision: review.edl_content_revision, current: true, exported_scans: exportedScans, exported_review: null } } };
      await json(route, { format: "edl", review_id: review.id, exported_format_revision: review.edl_content_revision, exported_at: "2026-08-20T00:00:00Z", exported_scans: exportedScans, exported_review: review }); return;
    }
    if (url.pathname === "/api/edl-imports/upload" && method === "POST") {
      const form = route.request().postData() ?? "";
      const fileName = form.match(/filename="([^"]*)"/)?.[1] ?? "";
      const payload = JSON.parse(form.match(/name="options"\r\n\r\n(.*)\r\n/)?.[1] ?? "{}") as { name: string; target: { media_source_id: string | null } };
      if (fileName.includes("orphan")) { await json(route, { detail: { code: "jellyfin_item_required", message: "This file must belong to a Jellyfin item. Open its Jellyfin item and import the file there." } }, 400); return; }
      if (fileName.includes("invalid")) { await json(route, { detail: { code: "invalid_edl_import", message: "Invalid EDL", line_number: 1, references: null } }, 422); return; }
      const importedFilter: MockReviewFilter = { id: `import-${nextReviewId}-filter`, source_scan_id: `import-${nextReviewId}-scan`, source_finding_id: null, source: "audio", category: null, subcategory: null, severity: null, mcf_channel: null, mcf_comment: null, label: "Imported mute", details: null, group_key: null, start_seconds: 1, end_seconds: 2, confidence: 1, edit_action: "mute", selected: true };
      const imported = mockReview(`review-${nextReviewId++}`, payload.name, payload.target.media_source_id, [importedFilter]);
      reviews = [imported, ...reviews];
      await json(route, { review: imported, scan: { id: importedFilter.source_scan_id, scan_id: 3, name: payload.name, origin: "imported" }, warning: fileName.includes("mixed") ? { code: "unsupported_actions_ignored", message: "Unsupported EDL actions were ignored and not imported", ignored_action_count: 2 } : null }, 201); return;
    }
    if (url.pathname === "/api/scans/queue") {
      await json(route, queuedScans);
      return;
    }
    if (url.pathname === "/api/scans/queue/reorder" && method === "POST") {
      const payload = route.request().postDataJSON() as { queue_ids: number[] };
      queuedScans = payload.queue_ids.map((id, sort_order) => ({ ...queuedScans.find((scan) => scan.queue_id === id)!, sort_order }));
      await json(route, { ok: true }); return;
    }
    const queueId = url.pathname.match(/^\/api\/scans\/queue\/(\d+)$/)?.[1];
    if (queueId && method === "DELETE") {
      queuedScans = queuedScans.filter((scan) => scan.queue_id !== Number(queueId));
      await json(route, { cancelled: true, cancelling: false }); return;
    }
    if (url.pathname === "/api/scans/history") {
      await json(route, [{ ...scanScope, id: "scan-history", scan_id: 2, media_item_id: "media-1", jellyfin_item_id: "movie-1", item_name: "Example Movie", item_type: "Movie", status: "complete", scan_audio: true, audio_model: "whisper", scan_video: true, video_model: "nudenet", frame_interval: 1, speed_up: false, error_message: null, created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z" }]);
      return;
    }
    if (url.pathname === "/api/scans" && method === "POST") {
      await json(route, {
        ...scanScope,
        id: "scan-1", scan_id: 1, media_item_id: "media-1", jellyfin_item_id: "movie-1",
        item_name: "Example Movie", item_type: "Movie", status: "queued", scan_audio: true,
        audio_model: "whisper", scan_video: true, video_model: "nudenet", frame_interval: 1,
        speed_up: false, created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
        queue_id: 1, sort_order: 0, error_message: null,
      }, 201);
      return;
    }
    if (url.pathname === "/api/settings/path-mappings" && method === "GET") { await json(route, mappings); return; }
    if (url.pathname === "/api/settings/path-mappings" && method === "POST") {
      const payload = route.request().postDataJSON() as { remote_path: string; local_path: string };
      const mapping = { id: Math.max(0, ...mappings.map((item) => item.id)) + 1, remote_path: payload.remote_path, local_path: payload.local_path, sort_order: mappings.length, created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z" };
      mappings = [...mappings, mapping];
      await json(route, mapping, 201); return;
    }
    if (url.pathname === "/api/settings/path-mappings/reorder" && method === "POST") {
      const payload = route.request().postDataJSON() as { mapping_ids: number[] };
      mappings = payload.mapping_ids.map((id, sort_order) => ({ ...mappings.find((mapping) => mapping.id === id)!, sort_order }));
      await json(route, mappings); return;
    }
    const mappingId = url.pathname.match(/^\/api\/settings\/path-mappings\/(\d+)$/)?.[1];
    if (mappingId && method === "PATCH") {
      const payload = route.request().postDataJSON() as { remote_path: string; local_path: string };
      mappings = mappings.map((mapping) => mapping.id === Number(mappingId) ? { ...mapping, ...payload } : mapping);
      await json(route, mappings.find((mapping) => mapping.id === Number(mappingId))); return;
    }
    if (mappingId && method === "DELETE") {
      mappings = mappings.filter((mapping) => mapping.id !== Number(mappingId));
      await route.fulfill({ status: 204 }); return;
    }
    if (url.pathname === "/api/settings/path-mappings/resolve") { await json(route, { remote_path: "/media/example.mkv", local_path: "/mnt/media/example.mkv", mapping_id: 1 }); return; }
    if (url.pathname === "/api/library-items/movie-1/image") {
      await route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mNk+M/wHwAF/gL+1YQK0wAAAABJRU5ErkJggg==", "base64") }); return;
    }
    await json(route, { detail: `Unexpected request: ${url.pathname}` }, 404);
  });
}
